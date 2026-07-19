"""Протокол событий агента — разделение UI и бизнес-логики.

Агентный цикл генерирует события (tool_start, tool_result, stream_chunk и т.д.),
а обработчик (handler) решает, как их отображать. Это позволяет:
- Использовать agent без Rich-консоли (API, тесты)
- Подменять UI-слой без изменения бизнес-логики
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import tools
from planner import Plan


@runtime_checkable
class AgentEventHandler(Protocol):
    """Протокол обработки событий агентного цикла."""

    def on_tool_start(self, call: tools.ToolCall, subtitle: str = "") -> None:
        """Вызывается перед выполнением tool-вызова."""
        ...

    def on_tool_result(self, result: tools.ToolResult) -> None:
        """Вызывается после выполнения tool-вызова."""
        ...

    def on_plan_update(
        self,
        plan: Plan,
        action: str = "",
        focus_index: int | None = None,
    ) -> None:
        """Вызывается при обновлении плана."""
        ...

    def on_status(self, message: str, level: str = "info") -> None:
        """Статусное сообщение (truncation warning, plan nudge и т.д.)."""
        ...

    def on_subagent_start(
        self, index: int, total: int, mode: str, prompt: str,
        model_label: str = "",
    ) -> None:
        """Вызывается при запуске субагента."""
        ...

    def on_subagent_status(self, index: int, message: str) -> None:
        """Обновление статуса субагента."""
        ...

    def on_subagent_done(self, index: int, result: object = None) -> None:
        """Вызывается при завершении субагента."""
        ...

class RichEventHandler:
    """Реализация для Rich-терминала — делегирует в agent/display.py."""

    def __init__(self):
        from rich.console import Console

        self._console = Console()
        self._pending_call: tools.ToolCall | None = None
        self._pending_subtitle: str = ""

    def on_tool_start(self, call: tools.ToolCall, subtitle: str = "") -> None:
        self._pending_call = call
        self._pending_subtitle = subtitle

    def on_tool_result(self, result: tools.ToolResult) -> None:
        call = self._pending_call
        subtitle = self._pending_subtitle
        self._pending_call = None
        self._pending_subtitle = ""

        if call is not None:
            from agent.display import show_tool_combined
            show_tool_combined(call, result, subtitle=subtitle)
        else:
            from agent.display import show_output
            show_output(result)

    def on_plan_update(
        self,
        plan: Plan,
        action: str = "",
        focus_index: int | None = None,
    ) -> None:
        from agent.display import show_plan_update
        show_plan_update(plan, action=action, focus_index=focus_index)

    def on_status(self, message: str, level: str = "info") -> None:
        style_map = {
            "info": "dim",
            "warning": "dim yellow",
            "error": "red",
            "success": "green",
        }
        style = style_map.get(level, "dim")
        self._console.print(f"  [{style}]{message}[/{style}]")

    def on_subagent_start(
        self, index: int, total: int, mode: str, prompt: str,
        model_label: str = "",
    ) -> None:
        from agent.display import show_subagent_start
        show_subagent_start(index, total, mode, prompt, model_label=model_label)

    def on_subagent_status(self, index: int, message: str) -> None:
        from agent.display import show_subagent_status
        show_subagent_status(index, message)

    def on_subagent_done(self, index: int, result=None) -> None:
        from agent.display import show_subagent_done
        show_subagent_done(index, result)
