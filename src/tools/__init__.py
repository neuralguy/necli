"""
Инструменты агента: shell + нативные файловые операции.

Публичный API — обратная совместимость с остальным кодом.
"""

from ._paths import get_working_dir, set_working_dir
from .models import ToolCall, ToolResult
from .parser import (
    has_tool_calls,
    parse_tool_calls,
    strip_tool_calls,
    truncate_after_last_tool_call,
)
from .registry import execute_call

__all__ = [
    "ToolCall",
    "ToolResult",
    "execute_call",
    "get_working_dir",
    "has_tool_calls",
    "parse_tool_calls",
    "set_working_dir",
    "strip_tool_calls",
    "truncate_after_last_tool_call",
]

