"""Structured observability for necli.

Архитектурные правила:
- Лог наблюдает за выполнением, но не участвует в нём.
- INFO — только lifecycle события (не payload, не chunks, не SSE).
- У каждого события есть тип (event) и correlation IDs.
- Каждый потенциально дорогой участок обёрнут в log_span с auto slow-warning.
- Два sink: necli.log (INFO+, human-readable) и necli-debug.jsonl (DEBUG+, structured).
- Payloads логируются только при NECLI_LOG_PAYLOADS=1.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger as _loguru_logger

# Патч ширины emoji в Rich (rich.cells) ДО любого импорта rich-объектов
# в проекте. См. ui/_emoji_width.py — включается через config "emoji_width": 1
# или env NECLI_EMOJI_WIDTH=1.
try:
    from ui._emoji_width import apply_emoji_width_patch as _apply_emoji_patch

    _apply_emoji_patch()
except Exception as _emoji_patch_error:
    print(
        f"necli: emoji width patch failed: {_emoji_patch_error}",
        file=sys.stderr,
    )

# ───────────────────── Configuration ─────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _resolve_log_dir() -> Path:
    override = os.environ.get("NECLI_LOG_DIR")
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, "frozen", False):
        necli_home = os.environ.get("NECLI_HOME")
        base = Path(necli_home).expanduser() if necli_home else Path.home() / ".necli"
        return (base / "logs").resolve()
    return _PROJECT_ROOT / "logs"


_LOG_DIR = _resolve_log_dir()
_INFO_LOG = _LOG_DIR / "necli.log"
_DEBUG_JSONL = _LOG_DIR / "necli-debug.jsonl"

# Payloads disabled by default. Set NECLI_LOG_PAYLOADS=1 to enable raw text logging.
# NEVER logs API keys, Authorization headers, cookies, or proxy passwords regardless.
_LOG_PAYLOADS = os.environ.get("NECLI_LOG_PAYLOADS", "0") == "1"

# Slow operation thresholds (seconds)
SLOW_TOOL_THRESHOLD = 2.0
SLOW_CONTEXT_THRESHOLD = 0.5
SLOW_FS_THRESHOLD = 0.5
SLOW_API_TTFB_THRESHOLD = 10.0
SLOW_API_TOTAL_THRESHOLD = 60.0
SLOW_ROUND_GAP_THRESHOLD = 2.0
SLOW_EVENT_LOOP_THRESHOLD = 0.5

# ───────────────────── Correlation context ─────────────────────


@dataclass
class _CorrelationContext:
    session_id: str = ""
    turn: int = 0
    round: int = 0
    request: int = 0
    subagent: str = ""
    tool_call: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


_ctx_var: contextvars.ContextVar[_CorrelationContext | None] = contextvars.ContextVar(
    "necli_log_ctx", default=None
)


def _get_ctx() -> _CorrelationContext:
    """Current correlation context (auto-initialized)."""
    ctx = _ctx_var.get()
    if ctx is None:
        ctx = _CorrelationContext()
        _ctx_var.set(ctx)
    return ctx


def get_ctx() -> _CorrelationContext:
    """Return current correlation context (read-only)."""
    return _get_ctx()


def bind(**fields: Any) -> None:
    """Update current correlation context with given fields.

    Reserved keys: session, turn, round, request, subagent, tool_call.
    All other keys go into extra dict and appear in every subsequent event.
    """
    ctx = _get_ctx()
    new_ctx = _CorrelationContext(
        session_id=fields.get("session", ctx.session_id) or ctx.session_id,
        turn=fields.get("turn", ctx.turn) if "turn" in fields else ctx.turn,
        round=fields.get("round", ctx.round) if "round" in fields else ctx.round,
        request=fields.get("request", ctx.request) if "request" in fields else ctx.request,
        subagent=fields.get("subagent", ctx.subagent) if "subagent" in fields else ctx.subagent,
        tool_call=fields.get("tool_call", ctx.tool_call)
        if "tool_call" in fields
        else ctx.tool_call,
        extra={
            **ctx.extra,
            **{
                k: v
                for k, v in fields.items()
                if k not in ("session", "turn", "round", "request", "subagent", "tool_call")
            },
        },
    )
    _ctx_var.set(new_ctx)


def unbind(*keys: str) -> None:
    """Remove fields from current correlation context."""
    ctx = _get_ctx()
    extra = {k: v for k, v in ctx.extra.items() if k not in keys}
    new_ctx = _CorrelationContext(
        session_id=ctx.session_id if "session" not in keys else "",
        turn=ctx.turn if "turn" not in keys else 0,
        round=ctx.round if "round" not in keys else 0,
        request=ctx.request if "request" not in keys else 0,
        subagent=ctx.subagent if "subagent" not in keys else "",
        tool_call=ctx.tool_call if "tool_call" not in keys else "",
        extra=extra,
    )
    _ctx_var.set(new_ctx)


@contextmanager
def scope(**fields: Any) -> Iterator[None]:
    """Temporarily bind fields for the duration of the context, then restore."""
    prev = _get_ctx()
    bind(**fields)
    try:
        yield
    finally:
        _ctx_var.set(prev)


# ───────────────────── Event emission ─────────────────────


def _build_fields(event_name: str, **extra: Any) -> dict[str, Any]:
    """Build structured fields dict for an event."""
    ctx = _get_ctx()
    fields: dict[str, Any] = {"event": event_name}
    if ctx.session_id:
        fields["session"] = ctx.session_id
    if ctx.turn:
        fields["turn"] = ctx.turn
    if ctx.round:
        fields["round"] = ctx.round
    if ctx.request:
        fields["request"] = ctx.request
    if ctx.subagent:
        fields["subagent"] = ctx.subagent
    if ctx.tool_call:
        fields["tool_call"] = ctx.tool_call
    fields.update(ctx.extra)
    fields.update(extra)
    return fields


def _format_human(event_name: str, fields: dict[str, Any]) -> str:
    """Format event as human-readable string for necli.log."""
    parts = [event_name]
    # Correlation fields are rendered once in the bracket below.  They are
    # also present in ``fields`` for the structured sink, so iterating over
    # them again produced lines such as ``[turn=2 round=2] turn=2 round=2``.
    skip = {"event", "session", "turn", "round", "subagent"}
    ctx = _get_ctx()

    # Correlation bracket
    ctx_parts = []
    if ctx.session_id:
        ctx_parts.append(f"session={ctx.session_id[:8]}")
    if ctx.turn:
        ctx_parts.append(f"turn={ctx.turn}")
    if ctx.round:
        ctx_parts.append(f"round={ctx.round}")
    if ctx.subagent:
        ctx_parts.append(f"agent={ctx.subagent}")
    if ctx_parts:
        parts.append("[" + " ".join(ctx_parts) + "]")

    # Key-value pairs
    for k, v in fields.items():
        if k in skip:
            continue
        if isinstance(v, float):
            parts.append(f"{k}={v:.3f}")
        elif isinstance(v, bool):
            parts.append(f"{k}={str(v).lower()}")
        else:
            parts.append(f"{k}={v}")
    return " ".join(parts)


def _emit_event(level: str, event_name: str, depth: int, fields: dict[str, Any]) -> None:
    data = _build_fields(event_name, **fields)
    msg = _format_human(event_name, data)
    _loguru_logger.opt(depth=depth).bind(structured=data).log(level.upper(), msg)


def event(level: str, event_name: str, **fields: Any) -> None:
    """Emit a structured event at the given level."""
    _emit_event(level, event_name, 2, fields)


def info(event_name: str, **fields: Any) -> None:
    """Emit INFO event."""
    _emit_event("INFO", event_name, 2, fields)


def warning(event_name: str, **fields: Any) -> None:
    """Emit WARNING event."""
    _emit_event("WARNING", event_name, 2, fields)


def error(event_name: str, **fields: Any) -> None:
    """Emit ERROR event."""
    _emit_event("ERROR", event_name, 2, fields)


def debug(event_name: str, **fields: Any) -> None:
    """Emit DEBUG event."""
    _emit_event("DEBUG", event_name, 2, fields)


# ───────────────────── Payload gate ─────────────────────


def should_log_payloads() -> bool:
    """Check if raw payload logging is enabled (NECLI_LOG_PAYLOADS=1)."""
    return _LOG_PAYLOADS


def payload_preview(text: Any, max_chars: int = 200) -> str:
    """Return safe preview of payload. Returns '<payload hidden>' unless NECLI_LOG_PAYLOADS=1."""
    if not _LOG_PAYLOADS:
        return "<payload hidden>"
    if text is None:
        return ""
    s = str(text)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"...({len(s) - max_chars} more)"


# ───────────────────── Spans with slow-warning ─────────────────────

_SPAN_THRESHOLDS: dict[str, float] = {
    "context.build": SLOW_CONTEXT_THRESHOLD,
    "context.prune": SLOW_CONTEXT_THRESHOLD,
    "fs.snapshot": SLOW_FS_THRESHOLD,
    "project.check": SLOW_FS_THRESHOLD,
    "project.stats": SLOW_FS_THRESHOLD,
    "git.refresh": SLOW_FS_THRESHOLD,
    "tools.batch": SLOW_TOOL_THRESHOLD,
    "api.request": SLOW_API_TOTAL_THRESHOLD,
}


@contextmanager
def log_span(name: str, slow_threshold: float | None = None, **fields: Any) -> Iterator[None]:
    """Time a block and emit DEBUG perf.<name>.start/end + WARNING if slow.

    Args:
        name: Span name (e.g., "context.prune", "tools.batch")
        slow_threshold: Override default threshold (None = use default from _SPAN_THRESHOLDS)
        **fields: Additional fields to include in events
    """
    t0 = time.monotonic()
    debug(f"{name}.start", **fields)
    try:
        yield
    finally:
        duration = time.monotonic() - t0
        data = {"duration": duration, **fields}
        debug(f"{name}.end", **data)

        threshold = slow_threshold if slow_threshold is not None else _SPAN_THRESHOLDS.get(name)
        if threshold is not None and duration > threshold:
            warning(f"perf.{name}.slow", duration=duration, threshold=threshold, **fields)


# ───────────────────── Event loop stall detector ─────────────────────

_stall_task: asyncio.Task | None = None
_stall_lock = threading.Lock()


async def _stall_monitor(interval: float = 0.25) -> None:
    """Background task that detects event loop stalls."""
    while True:
        t0 = time.monotonic()
        await asyncio.sleep(interval)
        actual_delay = time.monotonic() - t0 - interval
        if actual_delay > SLOW_EVENT_LOOP_THRESHOLD:
            warning("event_loop.stall", duration=actual_delay, expected=interval)


def start_stall_monitor() -> None:
    """Start event loop stall detection (idempotent)."""
    global _stall_task
    with _stall_lock:
        if _stall_task is not None:
            return
        try:
            loop = asyncio.get_running_loop()
            _stall_task = loop.create_task(_stall_monitor(), name="log-stall-monitor")
        except RuntimeError:
            pass


def stop_stall_monitor() -> asyncio.Task | None:
    """Request stall-monitor cancellation and return the task for optional joining."""
    global _stall_task
    with _stall_lock:
        task = _stall_task
        _stall_task = None
    if task is not None:
        task.cancel()
    return task


async def stop_stall_monitor_async() -> None:
    """Cancel and join the monitor so loop shutdown has no pending task."""
    task = stop_stall_monitor()
    if task is None or task is asyncio.current_task():
        return
    with suppress(asyncio.CancelledError):
        await task


# ───────────────────── Sinks ─────────────────────


def _jsonl_format(record: dict[str, Any]) -> str:
    """Format log record as JSON line for necli-debug.jsonl."""
    data: dict[str, Any] = {
        "ts": record["time"].isoformat(timespec="milliseconds"),
        "level": record["level"].name,
        "message": record["message"],
        "source": record["name"],
        "function": record["function"],
        "line": record["line"],
    }

    extra = record.get("extra", {})
    if "structured" in extra:
        # Structured event — merge all fields
        data.update(extra["structured"])
    else:
        # Legacy log — parse message format "event_name [ctx] k=v k=v"
        msg = record["message"]
        parts = msg.split()
        if parts:
            data["event"] = parts[0]
        for p in parts:
            if "=" in p and not p.startswith("["):
                k, v = p.split("=", 1)
                # Try to parse numeric values
                try:
                    if "." in v:
                        data[k] = float(v)
                    else:
                        data[k] = int(v)
                except ValueError:
                    data[k] = v

    # Add exception if present
    if record.get("exception"):
        data["exception"] = str(record["exception"])

    # loguru 0.7.x прогоняет результат callable-формата через format_map:
    # фигурные скобки JSON интерпретировались бы как плейсхолдеры формата.
    # Экранируем их, чтобы format_map вернул строку без изменений.
    rendered = json.dumps(data, ensure_ascii=False, default=str)
    return rendered.replace("{", "{{").replace("}", "}}").replace("<", r"\<")


# ───────────────────── Initialization ─────────────────────

_initialized = False


class _InterceptHandler(logging.Handler):
    """Forward standard-library logging records to the configured Loguru sinks."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = _loguru_logger.level(record.levelname).name
        except (AttributeError, ValueError):
            level = record.levelno

        def patch_source(loguru_record: dict[str, Any]) -> None:
            loguru_record["name"] = record.name
            loguru_record["function"] = record.funcName
            loguru_record["line"] = record.lineno
            loguru_record["module"] = record.module
            loguru_record["file"].name = record.filename
            loguru_record["file"].path = record.pathname

        _loguru_logger.patch(patch_source).opt(
            depth=0,
            exception=record.exc_info,
        ).log(level, record.getMessage())


def _install_stdlib_intercept() -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(_InterceptHandler())
    root.setLevel(logging.DEBUG)

    for name in (
        "aiohttp",
        "aiogram",
        "anthropic",
        "asyncio",
        "charset_normalizer",
        "ddgs",
        "google.auth",
        "google.genai",
        "groq",
        "httpcore",
        "httpx",
        "openai",
        "PIL",
        "prompt_toolkit",
        "trafilatura",
        "urllib3",
        "websockets",
    ):
        third_party_logger = logging.getLogger(name)
        third_party_logger.setLevel(logging.WARNING)
        third_party_logger.propagate = True


def setup_logging() -> None:
    """Initialize logging sinks. Call once at startup."""
    global _initialized
    if _initialized:
        return
    _initialized = True

    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Remove default loguru handlers
    _loguru_logger.remove()

    # prompt_toolkit owns the terminal while the interactive UI is running.
    # Any asynchronous stderr write moves its cursor behind prompt_toolkit's
    # back and corrupts the dynamic Working/input area.  Console logging is
    # therefore opt-in; file sinks below always keep WARNING and ERROR events.
    console_level = os.environ.get("NECLI_LOG_CONSOLE", "").strip().upper()
    if console_level and console_level not in {"0", "OFF", "NONE", "FALSE"}:
        try:
            logging_level = logging._nameToLevel[console_level]
        except KeyError:
            console_level = "WARNING"
            logging_level = logging.WARNING
        _loguru_logger.add(
            sys.stderr,
            level=console_level,
            format="<level>{level: <5}</level> | {message}",
            filter=lambda r: r["level"].no >= logging_level,
        )

    # Human INFO log: necli.log (20 MB × 10 rotations)
    _loguru_logger.add(
        str(_INFO_LOG),
        level="INFO",
        rotation="20 MB",
        retention=10,
        compression=None,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}"
        ),
        filter=lambda r: r["level"].no >= logging.INFO,
        enqueue=False,  # Synchronous for ordering
    )

    # Structured DEBUG+ JSONL: necli-debug.jsonl (50 MB × 5 rotations)
    _loguru_logger.add(
        str(_DEBUG_JSONL),
        level="DEBUG",
        rotation="50 MB",
        retention=5,
        compression=None,
        format=lambda r: _jsonl_format(r) + "\n",
        enqueue=False,
    )

    _install_stdlib_intercept()

    # Start event loop stall monitor if we're in an async context
    try:
        asyncio.get_running_loop()
        start_stall_monitor()
    except RuntimeError:
        pass


# ───────────────────── Backward compatibility ─────────────────────

# Expose loguru logger for backward compatibility with existing code
logger = _loguru_logger

# Convenience aliases
log = logger

__all__ = [
    # Thresholds
    "SLOW_API_TOTAL_THRESHOLD",
    "SLOW_API_TTFB_THRESHOLD",
    "SLOW_CONTEXT_THRESHOLD",
    "SLOW_EVENT_LOOP_THRESHOLD",
    "SLOW_FS_THRESHOLD",
    "SLOW_ROUND_GAP_THRESHOLD",
    "SLOW_TOOL_THRESHOLD",
    # Correlation
    "bind",
    "debug",
    "error",
    # Events
    "event",
    "get_ctx",
    "info",
    "log",
    # Spans
    "log_span",
    # Core
    "logger",
    # Payloads
    "payload_preview",
    "scope",
    "setup_logging",
    "should_log_payloads",
    # Stall detection
    "start_stall_monitor",
    "stop_stall_monitor",
    "stop_stall_monitor_async",
    "unbind",
    "warning",
]
