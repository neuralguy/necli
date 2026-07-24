"""tools/arg_validation.py — нормализация и валидация args по схеме."""

from tools.arg_validation import validate_and_normalize
from tools.models import ToolCall
from tools.registry import execute_call


class TestAliases:
    def test_source_aliased_to_path(self):
        args, err = validate_and_normalize("create_file", {"source": "x.py", "content": "hi"})
        assert err is None
        assert args == {"path": "x.py", "content": "hi"}

    def test_cmd_aliased_to_command(self):
        args, err = validate_and_normalize("shell", {"cmd": "ls"})
        assert err is None
        assert args == {"command": "ls"}

    def test_canonical_not_overwritten_by_synonym(self):
        # path уже задан — синоним source не должен его затирать
        args, err = validate_and_normalize("create_file", {"path": "real", "source": "wrong", "content": "x"})
        assert err is None
        assert args["path"] == "real"


class TestCoercion:
    def test_line_string_to_int(self):
        args, err = validate_and_normalize("lsp_references", {"path": "f", "line": "42", "character": "7"})
        assert err is None
        assert args["line"] == 42
        assert args["character"] == 7

    def test_background_string_to_bool(self):
        args, err = validate_and_normalize("shell", {"command": "ls", "background": "true"})
        assert err is None
        assert args["background"] is True

    def test_uncoercible_int_left_as_is(self):
        args, err = validate_and_normalize("lsp_references", {"path": "f", "line": "abc", "character": "7"})
        assert err is None
        assert args["line"] == "abc"
        assert args["character"] == 7


class TestRequired:
    def test_missing_required_reports_precisely(self):
        _, err = validate_and_normalize("create_file", {"path": "a"})
        assert err is not None
        assert "content" in err
        assert "missing required" in err.lower()

    def test_empty_string_counts_as_missing(self):
        _, err = validate_and_normalize("create_file", {"path": "", "content": "x"})
        assert err is not None
        assert "path" in err

    def test_unexpected_param_hinted(self):
        _, err = validate_and_normalize("create_file", {"path": "a", "weird": "b"})
        assert err is not None
        assert "weird" in err
        assert "content" in err


class TestEnum:
    def test_invalid_enum_rejected(self):
        _, err = validate_and_normalize(
            "memory_write", {"name": "x", "body": "y", "type": "banana"}
        )
        assert err is not None
        assert "banana" in err

    def test_valid_enum_ok(self):
        _, err = validate_and_normalize(
            "memory_write", {"name": "x", "body": "y", "type": "project"}
        )
        assert err is None


class TestNoSchema:
    def test_unknown_tool_passthrough(self):
        args, err = validate_and_normalize("mcp__server__thing", {"whatever": 1})
        assert err is None
        assert args == {"whatever": 1}


class TestExecuteCallIntegration:
    def test_missing_required_blocks_handler(self):
        # create_file без content должен отлавливаться слоем валидации
        call = ToolCall(command="x", tool_name="create_file", args={"path": "a"})
        result = execute_call(call)
        assert result.status == "error"
        assert "content" in result.output
