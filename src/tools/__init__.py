"""Public tool facade with lazy imports. Importing a lightweight helper must not initialize the full tool registry."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'ToolCall': ('tools.models', 'ToolCall'),
    'ToolResult': ('tools.models', 'ToolResult'),
    'execute_call': ('tools.registry', 'execute_call'),
    'get_working_dir': ('tools._paths', 'get_working_dir'),
    'has_tool_calls': ('tools.parser', 'has_tool_calls'),
    'parse_tool_calls': ('tools.parser', 'parse_tool_calls'),
    'set_working_dir': ('tools._paths', 'set_working_dir'),
    'strip_tool_calls': ('tools.parser', 'strip_tool_calls'),
    'truncate_after_last_tool_call': ('tools.parser', 'truncate_after_last_tool_call'),
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr = target
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = list(_LAZY)
