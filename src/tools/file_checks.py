"""File validation: syntax check, ruff lint, side-by-side diff."""

import shutil
import subprocess
from pathlib import Path

_SYNTAX_CHECK_TIMEOUT = 15

def _check_js_syntax(path: Path, path_str: str) -> str:
    node_cmd = shutil.which("node")
    if not node_cmd:
        return ""
    try:
        result = subprocess.run(
            [node_cmd, "--check", str(path)],
            capture_output=True,
            text=True,
            timeout=_SYNTAX_CHECK_TIMEOUT,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()
        if result.returncode != 0 and output:
            return f"\n✗ SyntaxError in {path_str}:\n{output}"
    except Exception:
        pass
    return ""




def _check_py_syntax(path: Path, path_str: str) -> str:
    """Быстрый syntax-check одного Python-файла через py_compile."""
    if path.suffix != ".py":
        return ""
    try:
        result = subprocess.run(
            ["python3", "-m", "py_compile", str(path)],
            capture_output=True,
            text=True,
            timeout=_SYNTAX_CHECK_TIMEOUT,
        )
        output = (result.stdout or "") + (result.stderr or "")
        output = output.strip()
        if result.returncode != 0 and output:
            return f"\n✗ SyntaxError in {path_str}:\n{output}"
    except Exception:
        pass
    return ""


def _run_ruff_on_python_file_sync(path: Path, path_str: str) -> str:
    """Per-file syntax-check.

    Раньше тут также гонялся ruff на каждом patch/write — теперь ruff делает
    только финальный project_check по всем изменённым файлам раунда, чтобы не
    показывать одни и те же ошибки многократно при серии патчей одного файла.
    """
    if path.suffix in (".js", ".mjs"):
        return _check_js_syntax(path, path_str)
    return _check_py_syntax(path, path_str)


def _run_ruff_on_python_file(path: Path, path_str: str) -> str:
    """Backward-compat alias. Запускает только syntax-check, без ruff."""
    return _run_ruff_on_python_file_sync(path, path_str)







