"""tools/models.py — ToolCall / ToolResult."""

from pathlib import Path

from tools.models import ToolCall, ToolResult


class TestToolCall:
    def test_default_name_for_shell(self):
        call = ToolCall(command="ls -la")
        assert call.tool_name == "shell"
        assert call.name == "ls"

    def test_shell_name_empty_command(self):
        call = ToolCall(command="")
        assert call.name == "shell"

    def test_named_tool_returns_tool_name(self):
        call = ToolCall(command="ignored", tool_name="read_files")
        assert call.name == "read_files"

    def test_repr_truncates_long_command(self):
        long = "x" * 200
        call = ToolCall(command=long)
        r = repr(call)
        assert "x" * 80 in r
        assert "x" * 200 not in r

    def test_repr_escapes_newlines(self):
        call = ToolCall(command="line1\nline2")
        r = repr(call)
        assert "\\n" in r

    def test_default_args_factory(self):
        c1 = ToolCall(command="a")
        c2 = ToolCall(command="b")
        c1.args["x"] = 1
        assert "x" not in c2.args


class TestToolResult:
    def test_minimal(self):
        r = ToolResult(name="shell", status="ok", output="hi")
        assert r.exit_code == 0
        assert r.command == ""
        assert r.image_path is None
        assert r.full_content is False
        assert r.fatal is False

    def test_to_dict_no_full_content(self):
        r = ToolResult(name="ls", status="ok", output="x")
        d = r.to_dict()
        assert d == {"name": "ls", "status": "ok", "output": "x", "exit_code": 0, "command": ""}
        assert "full_content" not in d

    def test_to_dict_with_full_content(self):
        r = ToolResult(name="read_files", status="ok", output="x", full_content=True)
        d = r.to_dict()
        assert d["full_content"] is True

    def test_image_path_attr(self):
        p = Path("/tmp/x.png")
        r = ToolResult(name="read_files", status="ok", output="hi", image_path=p)
        assert r.image_path == p

    def test_error_status(self):
        r = ToolResult(name="shell", status="error", output="boom", exit_code=1)
        assert r.status == "error"
        assert r.exit_code == 1