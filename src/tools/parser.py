"""Parser for :::call ... call::: tool blocks from LLM responses."""

from .call_parser import (
    parse_call_calls as _parse_call_calls,
    strip_call_calls as _strip_call_calls,
    has_call_calls as _has_call_calls,
)

MAX_TOOL_CALLS_PER_MESSAGE = 50

def parse_tool_calls(text: str) -> list:
    if not text:
        return []
    calls = _parse_call_calls(text)
    if len(calls) > MAX_TOOL_CALLS_PER_MESSAGE:
        calls = calls[:MAX_TOOL_CALLS_PER_MESSAGE]
    return calls

def strip_tool_calls(text: str) -> str:
    if not text:
        return ""
    import re
    result = _strip_call_calls(text)
    # Уберём пустые :::call ... call::: обёртки.
    result = re.sub(r":::call[^\n]*\n\s*\n?call:::", "", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()

def has_tool_calls(text: str) -> bool:
    return _has_call_calls(text)