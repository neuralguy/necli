"""Background auto-checks for changed Python files."""

from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from logger import logger
from tools._paths import resolve_path
from tools.background import publish_background_result
from tools.models import ToolResult

_DEBOUNCE_SECONDS = 0.6
_TIMEOUT = 20


@dataclass(frozen=True)
class _FileSig:
    mtime_ns: int
    size: int


_lock = threading.Lock()
_running_or_done: dict[str, _FileSig] = {}


def _sig(path: Path) -> _FileSig | None:
    try:
        st = path.stat()
        return _FileSig(mtime_ns=st.st_mtime_ns, size=st.st_size)
    except OSError:
        return None


def queue_python_auto_check(path: Path | str, display_path: str | None = None) -> bool:
    """Queue background diagnostics+ruff for a changed Python file.

    Returns True if a new background job was queued, False if skipped/deduped.
    """
    if os.environ.get("NECLI_AUTO_CHECKS") == "0":
        return False

    p = Path(path)
    if not p.is_absolute():
        p = resolve_path(str(path))
    if p.suffix != ".py" or not p.exists():
        return False

    sig = _sig(p)
    if sig is None:
        return False

    key = str(p.resolve())
    with _lock:
        if _running_or_done.get(key) == sig:
            return False
        _running_or_done[key] = sig

    label = display_path or str(path)
    thread = threading.Thread(
        target=_run_auto_check,
        args=(p, label, sig),
        daemon=True,
        name=f"necli-auto-check-{p.name}",
    )
    thread.start()
    logger.info("auto_check queued: {} sig={}", label, sig)
    return True


def _run_auto_check(path: Path, display_path: str, expected_sig: _FileSig) -> None:
    time.sleep(_DEBOUNCE_SECONDS)

    current_sig = _sig(path)
    if current_sig != expected_sig:
        queue_python_auto_check(path, display_path)
        return

    started = time.monotonic()
    sections: list[str] = []

    diagnostics = _run_lsp_diagnostics(path)
    if diagnostics:
        sections.append("lsp_diagnostics:\n" + diagnostics)

    ruff = _run_ruff(path)
    if ruff:
        sections.append("ruff check:\n" + ruff)

    if not sections:
        logger.info("auto_check ok: {}", display_path)
        return

    elapsed = time.monotonic() - started
    output = (
        f"[auto-check finished — {display_path}, {elapsed:.1f}s]\n"
        "Automatic diagnostics/ruff found issues for a changed Python file.\n\n"
        + "\n\n".join(sections)
    )
    publish_background_result(ToolResult(
        name="auto_check",
        status="error",
        output=output,
        exit_code=1,
        command=f"auto_check {display_path}",
    ))


def _run_lsp_diagnostics(path: Path) -> str:
    try:
        from apis.lsp_client import get_diagnostics_for_path
        text = get_diagnostics_for_path(str(path))
        return text or ""
    except Exception as e:
        logger.debug("auto_check lsp failed for {}: {}", path, e)
        return ""


def _run_ruff(path: Path) -> str:
    try:
        result = subprocess.run(
            ["uv", "run", "ruff", "check", str(path)],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except FileNotFoundError:
        return ""
    except subprocess.TimeoutExpired:
        return f"Timeout after {_TIMEOUT}s"
    except Exception as e:
        logger.debug("auto_check ruff failed for {}: {}", path, e)
        return ""

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0 and output:
        return output
    return ""
