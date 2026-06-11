"""
Инструменты агента: shell + нативные файловые операции.

Публичный API — обратная совместимость с остальным кодом.
"""

from .models import ToolCall, ToolResult
from .parser import (
    parse_tool_calls,
    strip_tool_calls,
    has_tool_calls,
)
from .registry import execute_call
from .shell import set_working_dir, get_working_dir

__all__ = [
    "ToolCall",
    "ToolResult",
    "parse_tool_calls",
    "strip_tool_calls",
    "has_tool_calls",
    "execute_call",
    "set_working_dir",
    "get_working_dir",
]

