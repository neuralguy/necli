"""Централизованная система логирования necli-api.

Все логи разделены на слои:
  - ui.log       : терминальный интерфейс, prompt_toolkit, slash-команды
  - tools.log    : инструменты агента (shell, file_ops, ssh, web_search, ...)
  - ai.log       : стриминг ответа, парсинг tool calls, sanitizer, рендеринг
  - agent.log    : агентный цикл, субагенты, планировщик, system prompt, skills
  - api.log      : API-провайдеры (OpenAI/Anthropic/Google/...), HTTP, токены
  - errors.log   : все ошибки ERROR+ со всех слоёв (дублирование)
  - general.log  : всё, что не попало в другие слои (config, session, main)

Перенаправление stdlib logging → loguru через InterceptHandler — большинство
модулей используют `logging.getLogger(__name__)`, и без перехвата их записи
теряются.

Использование:
    from logger import logger, LogContext, log_call, new_request_id

    logger.info("message")

    with LogContext(request_id="abc123"):
        logger.info("with request_id")

    @log_call(level="INFO")
    async def my_func(): ...
"""

from __future__ import annotations

import sys

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

import asyncio
import logging
import os
import uuid
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

from loguru import logger as _loguru_logger

# ── Пути ──

SRC_ROOT = Path(__file__).parent.resolve()

if getattr(sys, "frozen", False):
    _home = os.environ.get("NECLI_HOME")
    _base = Path(_home).expanduser() if _home else Path.home() / ".necli"
    LOGS_DIR = _base / "logs"
else:
    LOGS_DIR = SRC_ROOT / "logs"

# ── Контекст (request_id для трейсинга одной операции через слои) ──

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

def get_request_id() -> str:
    return request_id_var.get()

def set_request_id(request_id: Optional[str]) -> None:
    request_id_var.set(request_id if request_id is not None else "-")

def new_request_id() -> str:
    return uuid.uuid4().hex[:8]

def _context_patcher(record: dict) -> None:
    record["extra"].setdefault("request_id", request_id_var.get())

# ── Форматы ──

def _file_format(record: dict) -> str:
    req_id = record["extra"].get("request_id", "-")
    req_part = req_id if req_id and req_id != "-" else "--------"
    req_part = req_part.replace("{", "{{").replace("}", "}}")
    return (
        f"{{time:YYYY-MM-DD HH:mm:ss.SSS}} | "
        f"{{level: <8}} | "
        f"{req_part} | "
        f"{{name}}:{{function}}:{{line}} | "
        f"{{message}}\n"
        f"{{exception}}"
    )

def _error_format(record: dict) -> str:
    """Краткий формат для errors.log — без полного трейсбека.

    Если в записи есть исключение, добавляем только его тип и сообщение
    одной строкой, а не весь стек."""
    req_id = record["extra"].get("request_id", "-")
    req_part = req_id if req_id and req_id != "-" else "--------"
    req_part = req_part.replace("{", "{{").replace("}", "}}")
    exc = record.get("exception")
    exc_part = ""
    if exc is not None and exc.type is not None:
        exc_part = f" | {exc.type.__name__}: {exc.value}"
        exc_part = exc_part.replace("{", "{{").replace("}", "}}")
        exc_part = exc_part.replace("<", r"\<")
    return (
        f"{{time:YYYY-MM-DD HH:mm:ss.SSS}} | "
        f"{{level: <8}} | "
        f"{req_part} | "
        f"{{name}}:{{function}}:{{line}} | "
        f"{{message}}{exc_part}\n"
    )

# ── Распределение модулей по файлам ──
# Правила сопоставления: модуль попадает в слой, если его dotted-name равен
# одному из префиксов или начинается с `.`.

_LAYER_FILTERS: dict[str, list[str]] = {
    "ui": [
        "ui",
        "commands",
    ],
    "tools": [
        "tools",
    ],
    "ai": [
        "agent.stream",
        "agent.stream_parser",
        "agent.stream_render",
        "agent.sanitizer",
        "agent.display",
        "agent.syntax",
        "planner",
    ],
    "agent": [
        "agent.loop",
        "agent.context",
        "agent.executor",
        "agent.subagent",
        "agent.subagent_api",
        "agent.subagent_render",
        "agent.events",
        "agent.messages",
        "agent.project_stats",
        "system_prompt",
        "prompts",
        "skills",
    ],
    "api": [
        "apis",
    ],
}

def _module_matches(name: str, prefixes: list[str]) -> bool:
    return any(name == p or name.startswith(p + ".") for p in prefixes)

def _make_layer_filter(prefixes: list[str]):
    def _filter(record: dict) -> bool:
        return _module_matches(record["name"], prefixes)
    return _filter

_ALL_LAYER_PREFIXES: list[str] = [p for prefixes in _LAYER_FILTERS.values() for p in prefixes]

def _general_filter(record: dict) -> bool:
    return not _module_matches(record["name"], _ALL_LAYER_PREFIXES)

# ── Перехват stdlib logging → loguru ──

class _InterceptHandler(logging.Handler):
    """Forward stdlib `logging` records into loguru с сохранением имени модуля.

    Через `logger.patch` подменяем поля `name`/`function`/`line` записи loguru
    реальными значениями из `LogRecord` — иначе фильтры по `record["name"]`
    видели бы `__main__`/`logging` (фрейм-источник интерсепта), и все записи
    падали бы в `general.log`.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = _loguru_logger.level(record.levelname).name
        except (ValueError, AttributeError):
            level = record.levelno

        def _patch(lr: dict) -> None:
            lr["name"] = record.name
            lr["function"] = record.funcName
            lr["line"] = record.lineno
            lr["module"] = record.module
            lr["file"].name = record.filename
            lr["file"].path = record.pathname

        _loguru_logger.patch(_patch).opt(
            depth=0,
            exception=record.exc_info,
        ).log(level, record.getMessage())

def _install_stdlib_intercept() -> None:
    root = logging.getLogger()
    # Удаляем все существующие хэндлеры (включая дефолтный StreamHandler)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(_InterceptHandler())
    root.setLevel(logging.DEBUG)

    # Шумные сторонние библиотеки — глушим до WARNING
    for _name in (
        "httpx", "httpcore", "openai", "groq", "urllib3",
        "aiogram", "anthropic", "asyncio",
        "google.auth", "google.genai", "httpx", "httpx._client", "httpcore",
        "PIL", "websockets", "aiohttp",
        "charset_normalizer", "prompt_toolkit", "textual",
        "ddgs", "trafilatura",
    ):
        lg = logging.getLogger(_name)
        lg.setLevel(logging.WARNING)
        lg.propagate = True  # чтобы InterceptHandler в root всё-таки видел

# ── Настройка ──

def setup_logger():
    LOGS_DIR.mkdir(exist_ok=True)
    _loguru_logger.remove()
    _loguru_logger.configure(patcher=_context_patcher)

    common_kwargs = dict(
        level="DEBUG",
        format=_file_format,
        enqueue=True,
        encoding="utf-8",
        rotation="2 MB",
        retention=5,
        compression="zip",
        diagnose=False,
        backtrace=False,
    )

    # Per-layer sinks
    for layer_name, prefixes in _LAYER_FILTERS.items():
        _loguru_logger.add(
            LOGS_DIR / f"{layer_name}.log",
            filter=_make_layer_filter(prefixes),
            **common_kwargs,
        )

    # General — всё, что не попало в слои
    _loguru_logger.add(
        LOGS_DIR / "general.log",
        filter=_general_filter,
        **{**common_kwargs, "level": "INFO"},
    )

    # Errors — дублирование всех ERROR+ для быстрого просмотра.
    # diagnose/backtrace выключены: пишем краткое сообщение об ошибке,
    # а не полный расширенный трейсбек со значениями переменных.
    _loguru_logger.add(
        LOGS_DIR / "errors.log",
        level="ERROR",
        format=_error_format,
        enqueue=True,
        encoding="utf-8",
        rotation="2 MB",
        retention=5,
        compression="zip",
        diagnose=False,
        backtrace=False,
    )

    _install_stdlib_intercept()
    return _loguru_logger

logger = setup_logger()

# ── Декоратор log_call ──

def log_call(
    level: str = "DEBUG",
    log_args: bool = True,
    log_result: bool = False,
    max_arg_length: int = 200,
):
    def decorator(func: Callable) -> Callable:
        func_name = func.__qualname__

        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            args_str = ""
            if log_args:
                args_str = f" | args={_safe_repr(args[1:], max_arg_length)}"
                if kwargs:
                    args_str += f", kwargs={_safe_repr(kwargs, max_arg_length)}"
            logger.log(level, f"→ {func_name}{args_str}")
            start = perf_counter()
            try:
                result = await func(*args, **kwargs)
                elapsed = perf_counter() - start
                result_str = f" | result={_safe_repr(result, max_arg_length)}" if log_result else ""
                logger.log(level, f"← {func_name} | OK | {elapsed:.3f}s{result_str}")
                return result
            except Exception as e:
                elapsed = perf_counter() - start
                logger.opt(exception=True).error(
                    f"✗ {func_name} | FAILED | {elapsed:.3f}s | {type(e).__name__}: {e}"
                )
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            args_str = ""
            if log_args:
                args_str = f" | args={_safe_repr(args[1:], max_arg_length)}"
                if kwargs:
                    args_str += f", kwargs={_safe_repr(kwargs, max_arg_length)}"
            logger.log(level, f"→ {func_name}{args_str}")
            start = perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = perf_counter() - start
                result_str = f" | result={_safe_repr(result, max_arg_length)}" if log_result else ""
                logger.log(level, f"← {func_name} | OK | {elapsed:.3f}s{result_str}")
                return result
            except Exception as e:
                elapsed = perf_counter() - start
                logger.opt(exception=True).error(
                    f"✗ {func_name} | FAILED | {elapsed:.3f}s | {type(e).__name__}: {e}"
                )
                raise

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper
    return decorator

def _safe_repr(obj: Any, max_len: int = 200) -> str:
    try:
        if isinstance(obj, tuple) and len(obj) == 0:
            return "()"
        s = repr(obj)
        return s[:max_len] + "..." if len(s) > max_len else s
    except Exception:
        return f"<{type(obj).__name__}>"

# ── Контекстный менеджер для request_id ──

class LogContext:
    def __init__(self, request_id: Optional[str] = None):
        self.request_id = request_id or new_request_id()
        self._token = None

    def __enter__(self):
        self._token = request_id_var.set(self.request_id)
        return self

    def __exit__(self, *args):
        if self._token is not None:
            request_id_var.reset(self._token)

    async def __aenter__(self):
        return self.__enter__()

    async def __aexit__(self, *args):
        self.__exit__(*args)