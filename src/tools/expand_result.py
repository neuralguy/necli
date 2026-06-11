"""Tool: expand_tool_result — возвращает полный текст ранее обрезанного output."""

from tools.models import ToolCall, ToolResult


def execute_expand_tool_result(call: ToolCall) -> ToolResult:
    args = call.args or {}
    rid = (args.get("id") or "").strip()
    if not rid:
        return ToolResult(
            name="expand_tool_result",
            status="error",
            output='Не указан id. Использование: {"id": "<id из ...>"}',
            exit_code=1,
            command=call.command,
        )

    from agent.result_cache import get as _get_full, size as _size
    text = _get_full(rid)
    if text is None:
        return ToolResult(
            name="expand_tool_result",
            status="error",
            output=(
                f"id '{rid}' не найден в кэше "
                f"(текущий размер кэша: {_size()}). "
                f"Кэш живёт только в рамках процесса CLI и ограничен размером — "
                f"возможно, запись вытеснена. Перезапусти инструмент-источник."
            ),
            exit_code=1,
            command=call.command,
        )

    return ToolResult(
        name="expand_tool_result",
        status="ok",
        output=text,
        exit_code=0,
        command=call.command,
        full_content=True,
    )