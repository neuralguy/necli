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


def _now() -> str:
    # Абсолютные дата+время для frontmatter; timezone сохраняет смысл между сессиями.
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def memory_write(call: ToolCall) -> ToolResult:
    """args: {name, body, type?, scope?}  — создать/обновить memory-файл."""
    from memory import write_memory
    from memory.memdir import MEMORY_TYPES

    args = call.args or {}
    name = str(args.get("name", "")).strip()
    body = str(args.get("body", "")).strip()
    mtype = str(args.get("type", "project")).strip() or "project"
    scope = str(args.get("scope", "project")).strip() or "project"

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
    if scope not in ("project", "global"):
        return ToolResult(
            name="memory_write", status="error",
            output=f"scope must be 'project' or 'global' (got {scope!r}).",
            exit_code=1, command=call.command,
        )
    try:
        mf = write_memory(
            name, body, mtype=mtype, timestamp=_now(),
            working_dir=get_working_dir(), scope=scope,
        )
    except Exception as e:
        logger.opt(exception=True).error("memory_write failed: {}", e)
        return ToolResult(
            name="memory_write", status="error",
            output=f"memory write failed: {type(e).__name__}: {e}",
            exit_code=1, command=call.command,
        )
    return ToolResult(
        name="memory_write", status="ok",
        output=(
            f"Saved memory '{mf.name}' (type={mf.type}, scope={scope}, "
            f"created={mf.created}, updated={mf.updated})."
        ),
        command=call.command,
    )


def memory_list(call: ToolCall) -> ToolResult:
    """Список memory-файлов (global + проект) с кратким содержанием."""
    from memory import scan_memories

    global_files = scan_memories(get_working_dir(), scope="global")
    project_files = scan_memories(get_working_dir(), scope="project")
    if not global_files and not project_files:
        return ToolResult(
            name="memory_list", status="ok",
            output="No memories saved yet (project or global).",
            command=call.command,
        )
    lines = []
    for scope_label, files in (("global", global_files), ("project", project_files)):
        for f in files:
            first = f.body.splitlines()[0][:100] if f.body else ""
            lines.append(
                f"- {f.name} [scope={scope_label}, type={f.type}, "
                f"created={f.created}, updated={f.updated}]: {first}"
            )
    return ToolResult(
        name="memory_list", status="ok",
        output="\n".join(lines), command=call.command,
    )


def memory_read(call: ToolCall) -> ToolResult:
    """args: {name, scope?} — прочитать содержимое memory-файла целиком.

    scope по умолчанию ищет сперва в проекте, затем в global.
    """
    from config.paths import global_memory_dir, memory_dir_for
    from memory import read_memory

    args = call.args or {}
    name = str(args.get("name", "")).strip()
    scope = str(args.get("scope", "")).strip()
    if not name:
        return ToolResult(
            name="memory_read", status="error",
            output="memory_read requires 'name'.", exit_code=1, command=call.command,
        )
    if not name.endswith(".md"):
        name += ".md"

    if scope == "global":
        candidates = [(global_memory_dir() / name, "global")]
    elif scope == "project":
        candidates = [(memory_dir_for(get_working_dir()) / name, "project")]
    else:
        candidates = [
            (memory_dir_for(get_working_dir()) / name, "project"),
            (global_memory_dir() / name, "global"),
        ]

    for path, found_scope in candidates:
        mf = read_memory(path) if path.exists() else None
        if mf is not None:
            return ToolResult(
                name="memory_read", status="ok",
                output=(
                    f"[scope={found_scope}, type={mf.type}, "
                    f"created={mf.created}, updated={mf.updated}]\n{mf.body}"
                ),
                command=call.command,
            )
    return ToolResult(
        name="memory_read", status="error",
        output=f"Memory '{name}' not found.", exit_code=1, command=call.command,
    )
