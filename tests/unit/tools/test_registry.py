"""tools/registry.py — реестр инструментов и диспетчер."""

from tools.models import ToolCall
from tools.registry import (
    PLANNING_TOOLS,
    TOOL_REGISTRY,
    build_blocked_result,
    execute_call,
    is_tool_allowed,
    list_tools,
)


class TestRegistry:
    def test_core_tools_present(self):
        expected = {
            "shell", "read_files", "read_file", "grep", "patch_file",
            "create_file", "poll", "skill", "subagent", "web_search", "web_fetch",
            "create_docx", "docx_screenshot", "expand_tool_result",
        }
        missing = expected - set(TOOL_REGISTRY.keys())
        assert not missing, f"missing tools in registry: {missing}"

    def test_read_file_aliases_read_files(self):
        assert TOOL_REGISTRY["read_file"] is TOOL_REGISTRY["read_files"]

    def test_lsp_tools_registered(self):
        for t in ("lsp_references", "lsp_diagnostics"):
            assert t in TOOL_REGISTRY

    def test_list_tools_sorted(self):
        tools = list_tools()
        assert tools == sorted(tools)


class TestExecuteCall:
    def test_unknown_tool_returns_error(self):
        call = ToolCall(command="x", tool_name="not_a_real_tool")
        result = execute_call(call)
        assert result.status == "error"
        assert result.exit_code == 1
        assert "Unknown" in result.output or "Неизвестный" in result.output

    def test_handler_exception_caught(self):
        call = ToolCall(command="bad", tool_name="explode")

        def bad_handler(c):
            raise RuntimeError("kaboom")

        TOOL_REGISTRY["explode"] = bad_handler
        try:
            result = execute_call(call)
            assert result.status == "error"
            assert "RuntimeError" in result.output
            assert "kaboom" in result.output
        finally:
            del TOOL_REGISTRY["explode"]

    def test_ok_handler_passthrough(self):
        from tools.models import ToolResult
        call = ToolCall(command="echo", tool_name="myok")

        def ok(c):
            return ToolResult(name="myok", status="ok", output="done")

        TOOL_REGISTRY["myok"] = ok
        try:
            result = execute_call(call)
            assert result.status == "ok"
            assert result.output == "done"
        finally:
            del TOOL_REGISTRY["myok"]


class TestReadOnlyAndAllowed:
    def test_is_tool_allowed_agent_mode_all(self):
        assert is_tool_allowed("create_file", "agent") is True
        assert is_tool_allowed("shell", "agent") is True

    def test_skills_do_not_restrict_agent_tools(self):
        for tool in ("web_search", "image_search", "subagent"):
            assert is_tool_allowed(tool, "agent", set()) is True
            assert is_tool_allowed(tool, "agent", {"web", "ssh", "subagents"}) is True

    def test_is_tool_allowed_planning_mode_readonly_only(self):
        for t in PLANNING_TOOLS:
            assert is_tool_allowed(t, "planning") is True
        assert is_tool_allowed("create_file", "planning") is False
        assert is_tool_allowed("shell", "planning") is False

    def test_is_tool_allowed_autonomous_mode_allows_shell_not_writes(self):
        assert is_tool_allowed("shell", "autonomous") is True
        assert is_tool_allowed("shell", "auto") is True
        assert is_tool_allowed("subagent", "autonomous") is True
        assert is_tool_allowed("create_file", "autonomous") is False
        assert is_tool_allowed("patch_file", "autonomous") is False


class TestBlockedResult:
    def test_format(self):
        call = ToolCall(command="bad", tool_name="create_file")
        result = build_blocked_result(call)
        assert result.status == "error"
        assert result.exit_code == 1
        assert "create_file" in result.output
        assert "planning mode" in result.output
