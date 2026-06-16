"""tools/registry.py — реестр инструментов и диспетчер."""

from tools.registry import (
    TOOL_REGISTRY,
    execute_call,
    is_tool_allowed,
    build_blocked_result,
    list_tools,
    PLANNING_TOOLS,
)
from tools.models import ToolCall


class TestRegistry:
    def test_core_tools_present(self):
        expected = {
            "shell", "read_files", "read_file", "write_file", "patch_file",
            "create_file", "delete_file", "rename_file", "copy_file", "move_file",
            "ls", "tree", "mkdir", "rmdir", "find_files", "grep_files",
            "poll", "skill", "ssh", "subagent", "web_search",
            "create_docx", "docx_screenshot", "apply_diff", "expand_tool_result",
        }
        missing = expected - set(TOOL_REGISTRY.keys())
        assert not missing, f"missing tools in registry: {missing}"

    def test_read_file_aliases_read_files(self):
        assert TOOL_REGISTRY["read_file"] is TOOL_REGISTRY["read_files"]

    def test_lsp_tools_registered(self):
        for t in ("lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics"):
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
        assert is_tool_allowed("write_file", "agent") is True
        assert is_tool_allowed("shell", "agent") is True

    def test_is_tool_allowed_planning_mode_readonly_only(self):
        for t in PLANNING_TOOLS:
            assert is_tool_allowed(t, "planning") is True
        assert is_tool_allowed("write_file", "planning") is False
        assert is_tool_allowed("shell", "planning") is False


class TestBlockedResult:
    def test_format(self):
        call = ToolCall(command="bad", tool_name="write_file")
        result = build_blocked_result(call)
        assert result.status == "error"
        assert result.exit_code == 1
        assert "write_file" in result.output
        assert "planning mode" in result.output