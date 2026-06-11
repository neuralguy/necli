"""tools/parser.py — обёртка над call_parser с лимитами."""

from tools.parser import (
    parse_tool_calls,
    strip_tool_calls,
    has_tool_calls,
    MAX_TOOL_CALLS_PER_MESSAGE,
)


def _block(tool: str = "ls") -> str:
    return ":::call " + tool + "\n{}\ncall:::\n"


class TestParseToolCalls:
    def test_within_limit(self):
        text = _block() * 5
        assert len(parse_tool_calls(text)) == 5

    def test_truncated_to_max(self):
        text = _block() * (MAX_TOOL_CALLS_PER_MESSAGE + 10)
        calls = parse_tool_calls(text)
        assert len(calls) == MAX_TOOL_CALLS_PER_MESSAGE

    def test_empty_text(self):
        assert parse_tool_calls("") == []


class TestStripToolCalls:
    def test_removes_blocks(self):
        text = "before\n" + _block() + "after"
        result = strip_tool_calls(text)
        assert ":::call" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result

    def test_collapses_blank_lines(self):
        text = "a\n\n\n\n\nb"
        assert "\n\n\n" not in strip_tool_calls(text)


class TestHasToolCalls:
    def test_true(self):
        assert has_tool_calls(_block()) is True

    def test_false(self):
        assert has_tool_calls("plain") is False