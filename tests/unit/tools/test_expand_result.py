"""tools/expand_result.py — кэш длинных tool outputs."""

from tools.expand_result import execute_expand_tool_result
from tools.models import ToolCall


def _call(**args) -> ToolCall:
    return ToolCall(command="expand", tool_name="expand_tool_result", args=args)


class TestExpand:
    def test_no_id_error(self):
        r = execute_expand_tool_result(_call())
        assert r.status == "error"
        assert "id" in r.output.lower()

    def test_unknown_id(self):
        r = execute_expand_tool_result(_call(id="nonexistent_xyz"))
        assert r.status == "error"
        assert "not found" in r.output.lower() or "не найден" in r.output.lower()

    def test_valid_id_returns_full_text(self):
        from agent.result_cache import store
        rid = store("some long text " * 100)
        r = execute_expand_tool_result(_call(id=rid))
        assert r.status == "ok"
        assert r.full_content is True
        assert "some long text" in r.output

    def test_empty_id_string(self):
        r = execute_expand_tool_result(_call(id=""))
        assert r.status == "error"

    def test_id_with_whitespace_stripped(self):
        from agent.result_cache import store
        rid = store("data")
        r = execute_expand_tool_result(_call(id=f"  {rid}  "))
        assert r.status == "ok"
        assert "data" in r.output