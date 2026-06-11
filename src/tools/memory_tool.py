"""Инструменты работы с персистентной памятью (memdir).

Модель сама решает, когда сохранить долговременный факт (предпочтение
пользователя, обратную связь, контекст проекта, внешний референс), вызвав
memory_write. memory_list/​memory_read — для просмотра уже сохранённого.
"""

from __future__ import annotations

import datetime as _dt

from logger import logger
from tools._paths import get_working_dir
from tools.models import ToolCall, ToolResult


def _today() -> str:
    # Дата нужна для frontmatter; формат YYYY-MM-DD (абсолютная, не относительная).
    return _dt.date.today().isoformat()


def memory_write(call: ToolCall) -> ToolResult:
    """args: {name, body, type?}  — создать/обновить memory-файл."""
    from memory import write_memory
    from memory.memdir import MEMORY_TYPES

    args = call.args or {}
    name = str(args.get("name", "")).strip()
    body = str(args.get("body", "")).strip()
    mtype = str(args.get("type", "project")).strip() or "project"

    if not name or not body:
        return ToolResult(
            name="memory_write", status="error",
            output="memory_write requires non-empty 'name' and 'body'.",
            exit_code=1, command=call.command,
        )
    if mtype not in MEMORY_TYPES:
        return ToolResult(
            name="memory_write", status="error",
            output=f"type must be one of {', '.join(MEMORY_TYPES)} (got {mtype!r}).",
            exit_code=1, command=call.command,
        )
    try:
        mf = write_memory(name, body, mtype=mtype, today=_today(), working_dir=get_working_dir())
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).error("memory_write failed: {}", e)
        return ToolResult(
            name="memory_write", status="error",
            output=f"memory write failed: {type(e).__name__}: {e}",
            exit_code=1, command=call.command,
        )
    return ToolResult(
        name="memory_write", status="ok",
        output=f"Saved memory '{mf.name}' (type={mf.type}).",
        command=call.command,
    )


def memory_list(call: ToolCall) -> ToolResult:
    """Список memory-файлов проекта с кратким содержанием."""
    from memory import scan_memories

    files = scan_memories(get_working_dir())
    if not files:
        return ToolResult(
            name="memory_list", status="ok",
            output="No memories saved for this project yet.",
            command=call.command,
        )
    lines = []
    for f in files:
        first = f.body.splitlines()[0][:100] if f.body else ""
        lines.append(f"- {f.name} [type={f.type}, updated={f.updated}]: {first}")
    return ToolResult(
        name="memory_list", status="ok",
        output="\n".join(lines), command=call.command,
    )


def memory_read(call: ToolCall) -> ToolResult:
    """args: {name} — прочитать содержимое memory-файла целиком."""
    from config.paths import memory_dir_for
    from memory import read_memory

    name = str((call.args or {}).get("name", "")).strip()
    if not name:
        return ToolResult(
            name="memory_read", status="error",
            output="memory_read requires 'name'.", exit_code=1, command=call.command,
        )
    if not name.endswith(".md"):
        name += ".md"
    path = memory_dir_for(get_working_dir()) / name
    mf = read_memory(path) if path.exists() else None
    if mf is None:
        return ToolResult(
            name="memory_read", status="error",
            output=f"Memory '{name}' not found.", exit_code=1, command=call.command,
        )
    return ToolResult(
        name="memory_read", status="ok",
        output=f"[type={mf.type}, created={mf.created}, updated={mf.updated}]\n{mf.body}",
        command=call.command,
    )
