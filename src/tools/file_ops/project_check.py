"""Project-level TypeScript check after an edit round."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from logger import logger

_TIMEOUT = 30
_MAX_OUTPUT = 4000

_TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _has_tsconfig(root: Path) -> bool:
    return (root / "tsconfig.json").is_file()


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return -1, f"[timeout {_TIMEOUT}s]"
    except FileNotFoundError:
        return -1, "[not installed]"
    except Exception as e:
        return -1, f"[error: {e}]"


def _truncate(text: str, limit: int = _MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    head = text[: limit // 2]
    tail = text[-limit // 2 :]
    return f"{head}\n... [{len(text) - limit} chars skipped] ...\n{tail}"


def run_project_check(working_dir: str, changed_files: set[str]) -> str:
    """Запускает TypeScript-проверку на изменённых файлах.

    Возвращает агрегированный текст блока для tool_results, либо "".
    """
    if not changed_files:
        return ""

    root = Path(working_dir)
    if not root.is_dir():
        return ""

    ts_files = [
        f for f in changed_files
        if Path(f).suffix in _TS_EXT and (root / f).is_file()
    ]

    blocks: list[str] = []

    # TypeScript смотрит весь проект всё равно.
    if ts_files and _has_tsconfig(root) and shutil.which("tsc"):
        rc, out = _run(["tsc", "--noEmit", "-p", "."], cwd=root)
        if rc != 0 and out and "[not installed]" not in out:
            blocks.append(f"⚠ tsc --noEmit:\n{_truncate(out)}")
        logger.debug("project_check.tsc rc={}", rc)

    if not blocks:
        return ""

    return "\n\n--- PROJECT CHECK ---\n" + "\n\n".join(blocks) + "\n--- END PROJECT CHECK ---"
