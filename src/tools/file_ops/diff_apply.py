"""apply_diff — применение unified diff к рабочему дереву через git apply.

Принимает diff в формате:

    --- a/path/to/file
    +++ b/path/to/file
    @@ -10,3 +10,5 @@
     context
    -old line
    +new line 1
    +new line 2
     context

Или без префиксов a/b:

    --- path/to/file
    +++ path/to/file
    @@ ... @@

Перед apply делаем `git apply --check` (dry-run). Если падает —
возвращаем ошибку с выводом, файлы не трогаем.

Поддерживает multi-hunk и multi-file diff в одном вызове.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from logger import logger
from tools.models import ToolCall, ToolResult
from tools._paths import resolve_path

_TIMEOUT = 30


def _extract_diff_body(call: ToolCall) -> str:
    """Достаёт тело diff из call (args.diff или raw command)."""
    args = call.args or {}
    body = args.get("diff") or args.get("patch") or ""
    if body:
        return body
    # fallback на raw command (для fenced format где тело идёт после path=...)
    return call.command or ""


_FILE_HEADER_RE = re.compile(r"^\+\+\+ (?:b/)?(.+?)(?:\t.*)?$", re.MULTILINE)


def _affected_files(diff_text: str) -> list[str]:
    return [m.group(1).strip() for m in _FILE_HEADER_RE.finditer(diff_text)]


def _run_git_apply(diff_text: str, cwd: Path, check_only: bool = False) -> tuple[int, str]:
    # --whitespace=nowarn: подавляем предупреждения git о trailing-whitespace и
    # несовпадении отступов. Трейдофф: git может молча применить hunk с лёгким
    # whitespace-расхождением вместо отказа. Это сознательно — diff от модели
    # часто отличается хвостовыми пробелами; --check выше всё равно ловит
    # реальные конфликты контекста.
    cmd = ["git", "apply", "--whitespace=nowarn"]
    if check_only:
        cmd.append("--check")
    try:
        r = subprocess.run(
            cmd, cwd=cwd, input=diff_text,
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        return r.returncode, out
    except subprocess.TimeoutExpired:
        return -1, f"[timeout {_TIMEOUT}s]"
    except Exception as e:
        return -1, f"[error: {e}]"


def _run_patch_fallback(diff_text: str, cwd: Path) -> tuple[int, str]:
    """Fallback на системный patch -p1 (на случай, если git нет/не git repo)."""
    with tempfile.NamedTemporaryFile("w", suffix=".diff", delete=False) as tf:
        tf.write(diff_text)
        diff_path = tf.name
    try:
        # Сначала dry-run
        try:
            r = subprocess.run(
                ["patch", "-p1", "--dry-run", "-i", diff_path],
                cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT,
            )
        except FileNotFoundError:
            return -1, "[error: 'patch' utility not installed — cannot apply diff fallback]"
        except subprocess.TimeoutExpired:
            return -1, f"[timeout {_TIMEOUT}s]"
        if r.returncode != 0:
            return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
        # Применяем
        r2 = subprocess.run(
            ["patch", "-p1", "-i", diff_path],
            cwd=cwd, capture_output=True, text=True, timeout=_TIMEOUT,
        )
        return r2.returncode, ((r2.stdout or "") + (r2.stderr or "")).strip()
    finally:
        try:
            Path(diff_path).unlink()
        except OSError:
            pass

def _git_unavailable(out: str) -> bool:
    """True, если git как инструмент отсутствует / это не git-репозиторий.

    Отличаем 'инструмента нет' (можно делать fallback на patch) от
    'хунк не совпал' (реальный конфликт — fallback запрещён).
    """
    low = out.lower()
    if not low:
        return False
    markers = (
        "not a git repository",
        "command not found",
        "no such file or directory",
        "is not recognized",
        "[error:",
        "[timeout",
    )
    return any(m in low for m in markers)


def apply_diff(call: ToolCall) -> ToolResult:
    diff_text = _extract_diff_body(call).strip()
    if not diff_text:
        return ToolResult(
            name="apply_diff",
            status="error",
            output=(
                "Пустой diff. Передай unified diff в теле fenced-блока "
                "или в args.diff."
            ),
            exit_code=1,
            command=call.command,
        )

    # Гарантируем финальный перевод строки — git apply требует
    if not diff_text.endswith("\n"):
        diff_text += "\n"

    cwd = resolve_path(".")
    files = _affected_files(diff_text)
    if not files:
        return ToolResult(
            name="apply_diff",
            status="error",
            output=(
                "В diff не найдено ни одного '+++ ...' заголовка. "
                "Используй формат unified diff с заголовками '--- a/file' и '+++ b/file'."
            ),
            exit_code=1,
            command=call.command,
        )

    # 1) Пробуем git apply --check
    rc_check, out_check = _run_git_apply(diff_text, cwd, check_only=True)
    if rc_check == 0:
        rc, out = _run_git_apply(diff_text, cwd, check_only=False)
        backend = "git apply"
    elif _git_unavailable(out_check):
        # 2) git как инструмент недоступен / не git-репозиторий → fallback на patch -p1
        logger.debug("apply_diff: git unavailable, falling back to patch: {}", out_check)
        rc, out = _run_patch_fallback(diff_text, cwd)
        backend = "patch -p1"
    else:
        # git есть, но diff реально не применяется (конфликт контекста) —
        # НЕ делаем lenient fuzzing-fallback, сразу отдаём ошибку.
        logger.debug("apply_diff: git apply --check failed (real conflict): {}", out_check)
        rc, out = rc_check, out_check
        backend = "git apply"

    if rc != 0:
        return ToolResult(
            name="apply_diff",
            status="error",
            output=(
                f"✗ apply_diff failed ({backend}):\n{out}\n\n"
                f"Затронутые файлы (из diff): {', '.join(files)}\n"
                "Проверь что:\n"
                "- пути в '+++ b/...' относительные от рабочей директории\n"
                "- контекст в hunk совпадает с актуальным содержимым\n"
                "- hunk-заголовки '@@ -X,Y +A,B @@' корректны"
            ),
            exit_code=rc,
            command=call.command,
        )

    logger.info("apply_diff: ok via {} ({} files)", backend, len(files))

    # Запускаем lsp_diagnostics+ruff в фоне, без синхронного дублирования.
    from tools.auto_checks import queue_python_auto_check
    queued_checks: list[str] = []
    for f in files:
        p = resolve_path(f)
        if p.exists() and p.suffix == ".py" and queue_python_auto_check(p, f):
            queued_checks.append(f)

    output_parts = [f"✓ apply_diff: {len(files)} file(s) updated via {backend}"]
    output_parts.extend(f"  - {f}" for f in files)
    if queued_checks:
        output_parts.append("↻ auto-check queued: lsp_diagnostics + ruff for " + ", ".join(queued_checks))

    return ToolResult(
        name="apply_diff",
        status="ok",
        output="\n".join(output_parts),
        exit_code=0,
        command=call.command,
    )