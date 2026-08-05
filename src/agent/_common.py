"""Общие утилиты для агентных модулей (вынесены из loop.py, commit_agent.py и др.)."""

from __future__ import annotations

import tools


def native_tool_calls_to_calls(native_calls: list[dict] | None) -> list[tools.ToolCall]:
    """Конвертирует native tool_calls из ответа модели в список ToolCall."""
    calls: list[tools.ToolCall] = []
    for tc in native_calls or []:
        name = tc.get("name") or "shell"
        args: dict = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        command = str(args.get("command") or "shell") if name == "shell" else name
        calls.append(tools.ToolCall(command=command, tool_name=name, args=args, raw=""))
    return calls


def build_repeat_tool_notice(
    last_tool_name: str | None,
    calls: list[tools.ToolCall],
) -> tuple[str, str | None]:
    """Проверяет, не вызван ли тот же инструмент два хода подряд."""
    if not calls:
        return "", None
    tool_name = calls[0].tool_name
    if tool_name != last_tool_name:
        return "", tool_name
    return (
        "[repeat-tool notice]\n"
        f"You called `{tool_name}` in two consecutive tool rounds. "
        "Before calling it again, check whether the previous result already "
        "answers the task, or explain why repeating the same tool is necessary.",
        tool_name,
    )
