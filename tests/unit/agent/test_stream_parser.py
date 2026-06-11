"""agent/stream_parser.py — поиск call-блоков в стриминговом тексте."""

from rich.console import Console

from agent.stream_parser import (
    _find_next_complete_tool,
    _find_next_partial_tool,
    _find_next_tool_start,
    _clean_display_text,
)
from agent.stream_render import render_partial_tool


def _make_block(tool: str, body: str = "{}") -> str:
    return f":::call {tool}\n{body}\ncall:::\n"


class TestFindComplete:
    def test_finds_known_tool(self):
        text = _make_block("read_files", '{"path": "a.py"}')
        m = _find_next_complete_tool(text, 0)
        assert m is not None
        assert m.complete is True
        assert m.tool_name == "read_files"

    def test_skips_plan_block(self):
        text = _make_block("plan", '{"action": "create"}')
        m = _find_next_complete_tool(text, 0)
        assert m is None

    def test_skips_think_block(self):
        text = _make_block("think", "reasoning")
        m = _find_next_complete_tool(text, 0)
        assert m is None

    def test_no_match_returns_none(self):
        assert _find_next_complete_tool("plain text", 0) is None


class TestFindPartial:
    def test_unclosed_block(self):
        text = ':::call read_files\n{"path": "a'
        m = _find_next_partial_tool(text, 0)
        assert m is not None
        assert m.complete is False
        assert m.tool_name == "read_files"

    def test_skips_partial_plan(self):
        text = ':::call plan\n{"action"'
        m = _find_next_partial_tool(text, 0)
        assert m is None

    def test_no_partial_returns_none(self):
        assert _find_next_partial_tool("no block at all", 0) is None


class TestFindNextStart:
    def test_finds_position(self):
        text = "prefix\n:::call ls\n{}\ncall:::\n"
        pos = _find_next_tool_start(text, 0)
        assert pos is not None
        assert text[pos:].startswith(":::call ls")

    def test_returns_none_when_absent(self):
        assert _find_next_tool_start("nothing", 0) is None


class TestCleanDisplayText:
    def test_strips_call_blocks(self):
        text = f"before\n{_make_block('ls')}after"
        result = _clean_display_text(text)
        assert ":::call" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result

    def test_strips_plan_blocks(self):
        text = f"answer\n{_make_block('plan', '{}')}done"
        result = _clean_display_text(text)
        assert ":::call" not in result

    def test_collapses_blanks(self):
        text = "a\n\n\n\n\nb"
        result = _clean_display_text(text)
        assert "\n\n\n" not in result

class TestRenderPartialTool:
    def test_subagent_preview_hides_raw_json(self):
        body = '{"tasks":[{"role":"coder","mode":"agent","prompt":"write file"},{"role":"reviewer","depends_on":[1],"prompt":"check"}]}'
        rendered = render_partial_tool(body, "subagent", spinner_frame="⠋")
        assert rendered is not None
        console = Console(record=True, width=120)
        console.print(rendered)
        plain = console.export_text()
        assert "Subagent" in plain
        assert "coder" in plain
        assert '"tasks"' not in plain

    def test_workflow_preview_hides_raw_json(self):
        body = '{"name":"wf","isolate":true,"phases":[{"title":"Research","tasks":[{"prompt":"x"}]},{"title":"Verify","tasks":[]}]}'
        rendered = render_partial_tool(body, "workflow", spinner_frame="⠋")
        assert rendered is not None
        console = Console(record=True, width=120)
        console.print(rendered)
        plain = console.export_text()
        assert "Workflow" in plain
        assert "Research" in plain
        assert "Verify" in plain
        assert '"phases"' not in plain