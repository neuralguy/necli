"""Project-level семантическая проверка после раунда правок.

Запускается, если step_tracker содержит изменённые файлы. Гоняет:
- ruff check (Python) — на проекте, если в раунде менялись .py
- mypy <changed_files> — если в проекте есть mypy конфиг и менялись .py
- tsc --noEmit — если в проекте есть tsconfig.json и менялись .ts/.tsx/.js/.jsx

Все шаги опциональные: инструмент не установлен → пропускаем без ошибки.
Конфигурация проекта детектится автоматически по наличию маркеров.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from logger import logger

_TIMEOUT = 30
_MAX_OUTPUT = 4000

_PY_EXT = {".py"}
_TS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}


def _has_mypy_config(root: Path) -> bool:
    if (root / "mypy.ini").is_file():
        return True
    if (root / ".mypy.ini").is_file():
        return True
    pyproj = root / "pyproject.toml"
    if pyproj.is_file():
        try:
            text = pyproj.read_text(encoding="utf-8", errors="ignore")
            if "[tool.mypy]" in text:
                return True
        except OSError:
            pass
    return False


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
    """Запускает доступные проектные чекеры на изменённых файлах.

    Возвращает агрегированный текст блока для tool_results, либо "".
    """
    if not changed_files:
        return ""

    root = Path(working_dir)
    if not root.is_dir():
        return ""

    # Отбрасываем удалённые файлы: после delete_file/rename/move они остаются
    # в step_tracker.files_changed, но ruff/mypy на них упадут с E902/etc.
    def _abs(f: str) -> Path:
        p = Path(f)
        return p if p.is_absolute() else (root / p)

    py_files = [
        f for f in changed_files
        if Path(f).suffix in _PY_EXT and _abs(f).is_file()
    ]
    ts_files = [
        f for f in changed_files
        if Path(f).suffix in _TS_EXT and _abs(f).is_file()
    ]

    blocks: list[str] = []

    # 1) ruff check на изменённых .py
    if py_files and shutil.which("ruff"):
        rc, out = _run(["ruff", "check", "--no-cache", *py_files], cwd=root)
        if rc != 0 and out and "[not installed]" not in out:
            blocks.append(f"⚠ ruff (project):\n{_truncate(out)}")
        logger.debug("project_check.ruff rc={} files={}", rc, len(py_files))

    # 2) mypy на изменённых .py (если в проекте сконфигурирован)
    if py_files and _has_mypy_config(root) and shutil.which("mypy"):
        rc, out = _run(
            ["mypy", "--no-incremental", "--follow-imports=silent", *py_files],
            cwd=root,
        )
        if rc != 0 and out and "[not installed]" not in out:
            blocks.append(f"⚠ mypy:\n{_truncate(out)}")
        logger.debug("project_check.mypy rc={} files={}", rc, len(py_files))

    # 3) tsc --noEmit на проекте (TypeScript смотрит весь проект всё равно)
    if ts_files and _has_tsconfig(root) and shutil.which("tsc"):
        rc, out = _run(["tsc", "--noEmit", "-p", "."], cwd=root)
        if rc != 0 and out and "[not installed]" not in out:
            blocks.append(f"⚠ tsc --noEmit:\n{_truncate(out)}")
        logger.debug("project_check.tsc rc={}", rc)

    if not blocks:
        return ""

    return "\n\n--- PROJECT CHECK ---\n" + "\n\n".join(blocks) + "\n--- END PROJECT CHECK ---"