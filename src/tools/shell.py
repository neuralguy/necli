"""Выполнение shell-команд через subprocess."""

import os
import re
import subprocess
import sys
from typing import Optional

from logger import logger
from tools.models import ToolCall, ToolResult
from tools._paths import resolve_path as _resolve_path, get_working_dir, set_working_dir  # noqa: F401 (re-exported)

_EXECUTION_TIMEOUT = 60

_BLOCKED_COMMANDS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=/dev/zero",
    ":(){ :|:& };:",
    "> /dev/sda",
]

# Паттерны heredoc / перенаправления для создания файлов
_HEREDOC_RE = re.compile(
    r'(?:cat|tee)\s+.*?<<\s*[\'"]?(\w+)[\'"]?',
    re.DOTALL,
)
_CAT_REDIRECT_RE = re.compile(
    r'cat\s+>',
)

# _working_dir, get_working_dir, set_working_dir — перенесены в tools/_paths.py
# Реэкспортируются через этот модуль для обратной совместимости.


def _utf8_env() -> dict:
    """env с UTF-8 локалью. На Windows C.UTF-8 нет — задаём только PYTHONUTF8."""
    env = dict(os.environ)
    if sys.platform == "win32":
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
    else:
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
    return env


def _is_blocked(command: str) -> Optional[str]:
    cmd_stripped = command.strip().lower()
    for blocked in _BLOCKED_COMMANDS:
        if blocked in cmd_stripped:
            return f"Заблокировано: '{blocked}'"
    return None


def _is_file_write_via_shell(command: str) -> Optional[str]:
    """
    Детектирует попытки записи файлов через shell вместо нативных инструментов.
    Возвращает сообщение-подсказку или None.
    """
    cmd = command.strip()

    # heredoc: cat > file << 'EOF', cat > file <<EOF, tee file << EOF
    if _HEREDOC_RE.search(cmd):
        return (
            "REJECTED: Do not use heredoc (<<EOF) to write files. "
            "Use the write_file or create_file tool instead.\n"
            "Example:\n"
            ':::call create_file path="file.py"\n'
            "your content here\n"
            "call:::"
        )

    # cat > file (without heredoc, just redirect)
    if _CAT_REDIRECT_RE.search(cmd):
        return (
            "REJECTED: Do not use 'cat >' to write files. "
            "Use the write_file or create_file tool instead."
        )

    return None


def _strip_shell_prefix(command: str) -> str:
    """Убирает дублированный префикс 'shell' из команды."""
    if command.startswith("shell"):
        rest = command[5:]
        if not rest or rest[0] in (' ', '\t', '\n'):
            return rest.lstrip()
    return command


def execute_shell(call: ToolCall) -> ToolResult:
    """Выполняет shell-команду."""
    command = _strip_shell_prefix(call.command.strip())

    if not command:
        return ToolResult(
            name="shell", status="error",
            output="Пустая команда",
            exit_code=-1, command="",
        )

    blocked = _is_blocked(command)
    if blocked:
        return ToolResult(
            name=call.name, status="error",
            output=blocked,
            exit_code=-1, command=command,
        )

    # Блокируем запись файлов через shell
    file_write_hint = _is_file_write_via_shell(command)
    if file_write_hint:
        return ToolResult(
            name=call.name, status="error",
            output=file_write_hint,
            exit_code=-1, command=command,
        )

    # Фоновое выполнение: для тяжёлых/долгих команд. Запускаем в потоке,
    # сразу возвращаем job-id и продолжаем работу. Уведомление о завершении
    # придёт отдельным результатом в одном из следующих раундов.
    if (call.args or {}).get("background"):
        from tools.background import start_background

        job_id = start_background(command, get_working_dir(), _utf8_env())
        return ToolResult(
            name=call.name, status="ok",
            output=(
                f"Started in background as {job_id}. Continue with other work — "
                f"a notification with this command's output will arrive "
                f"automatically once it finishes."
            ),
            exit_code=0, command=command,
        )

    # cd разрешён: команда выполняется в одном subprocess (shell=True), поэтому
    # любой `cd` действует только внутри ЭТОГО вызова и не «утекает» между
    # вызовами — следующий запуск снова стартует с cwd=get_working_dir().
    # Это даёт агенту свободу работать в произвольных директориях
    # (`cd /any/path && cmd`), не нарушая изоляцию субагентов.
    logger.info("shell exec: {!r} (cwd={})", command[:300], get_working_dir())
    run_kwargs = dict(
        capture_output=True, text=True,
        timeout=_EXECUTION_TIMEOUT,
        cwd=get_working_dir(),
        env=_utf8_env(),
    )
    if sys.platform != "win32":
        run_kwargs["executable"] = "/bin/bash"
    try:
        result = subprocess.run(command, shell=True, **run_kwargs)
        logger.debug(
            "shell done: exit={} stdout_len={} stderr_len={}",
            result.returncode,
            len(result.stdout or ""),
            len(result.stderr or ""),
        )

        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")

        output = "\n".join(parts) if parts else "(no output)"

        return ToolResult(
            name=call.name,
            status="ok" if result.returncode == 0 else "error",
            output=output,
            exit_code=result.returncode,
            command=command,
        )

    except subprocess.TimeoutExpired:
        logger.warning("shell timeout {}s: {!r}", _EXECUTION_TIMEOUT, command[:200])
        return ToolResult(
            name=call.name, status="error",
            output=f"Timeout: {_EXECUTION_TIMEOUT}s",
            exit_code=-1, command=command,
        )
    except Exception as e:
        logger.opt(exception=True).error("shell crashed: {}", e)
        return ToolResult(
            name=call.name, status="error",
            output=f"Error: {e}",
            exit_code=-1, command=command,
        )
