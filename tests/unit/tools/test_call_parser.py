"""tools/call_parser.py — парсер :::call ... call::: блоков."""

import pytest

from tools.call_parser import (
    parse_call_calls,
    strip_call_calls,
    has_call_calls,
    iter_call_blocks,
    find_next_complete_call,
    find_next_partial_call,
    find_next_call_start,
)


def _wrap(tool: str, body: str, attrs: str = "") -> str:
    header = f":::call {tool}"
    if attrs:
        header += " " + attrs
    return f"{header}\n{body}\ncall:::\n"


class TestBasicParsing:
    def test_empty(self):
        assert parse_call_calls("") == []

    def test_no_call_blocks(self):
        assert parse_call_calls("just text\n```python\nprint(1)\n```") == []

    def test_single_json_tool(self):
        text = _wrap("read_files", '{"path": "a.py"}')
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert calls[0].tool_name == "read_files"
        assert calls[0].args == {"path": "a.py"}

    def test_multiple_blocks(self):
        text = (
            _wrap("read_files", '{"path": "a.py"}')
            + "between\n"
            + _wrap("ls", '{"path": "."}')
        )
        calls = parse_call_calls(text)
        assert len(calls) == 2
        assert calls[0].tool_name == "read_files"
        assert calls[1].tool_name == "ls"

    def test_body_with_triple_backticks(self):
        body = 'print(1)\n```python\nnested\n```\nafter'
        text = f':::call write_file path="a.py"\n{body}\ncall:::\n'
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert "nested" in calls[0].args["content"]
        assert "```python" in calls[0].args["content"]

    def test_body_with_tildes(self):
        body = 'text\n~~~\nstuff\n~~~\nmore'
        text = f':::call write_file path="a.py"\n{body}\ncall:::\n'
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert "~~~" in calls[0].args["content"]


class TestAttrs:
    def test_path_attr(self):
        text = _wrap("write_file", "print('hi')", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert calls[0].args["path"] == "a.py"
        assert calls[0].args["content"] == "print('hi')"

    def test_int_attr_coerced(self):
        body = "--- INSERT ---\nnew line"
        text = _wrap("patch_file", body, attrs='path="x.py" line=42')
        calls = parse_call_calls(text)
        assert calls[0].args["line"] == 42
        assert isinstance(calls[0].args["line"], int)

    def test_bool_attr_true(self):
        text = _wrap("find_files", '{"pattern":"*.py"}', attrs="all=true")
        calls = parse_call_calls(text)
        assert calls[0].args.get("all") is True

    def test_bool_attr_false(self):
        text = _wrap("find_files", '{"pattern":"*.py"}', attrs="all=false")
        calls = parse_call_calls(text)
        assert calls[0].args.get("all") is False

    def test_unquoted_value(self):
        text = _wrap("read_files", "{}", attrs="path=a.py")
        calls = parse_call_calls(text)
        assert calls[0].args["path"] == "a.py"

    def test_attrs_merged_with_json(self):
        text = _wrap("read_files", '{"lines": "1-10"}', attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["path"] == "a.py"
        assert calls[0].args["lines"] == "1-10"


class TestContentTools:
    def test_write_file_requires_path(self):
        text = _wrap("write_file", "content")
        assert parse_call_calls(text) == []

    def test_create_file_with_content(self):
        text = _wrap("create_file", "print('x')\n", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["content"] == "print('x')"

    def test_content_strips_one_trailing_newline(self):
        text = _wrap("write_file", "abc\n", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["content"] == "abc"

    def test_write_file_command_alias(self):
        text = _wrap("write_file", "x", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].command == "write_file a.py"


class TestPatchFile:
    def test_single_find_replace(self):
        body = "--- FIND ---\nold\n--- REPLACE ---\nnew"
        text = _wrap("patch_file", body, attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["find"] == "old"
        assert calls[0].args["replace"] == "new"

    def test_multiple_patches(self):
        body = (
            "--- FIND ---\nold1\n--- REPLACE ---\nnew1\n"
            "--- FIND ---\nold2\n--- REPLACE ---\nnew2"
        )
        text = _wrap("patch_file", body, attrs='path="a.py"')
        calls = parse_call_calls(text)
        patches = calls[0].args["patches"]
        assert len(patches) == 2
        assert patches[0] == {"find": "old1", "replace": "new1"}
        assert patches[1] == {"find": "old2", "replace": "new2"}

    def test_insert_section(self):
        body = "--- INSERT ---\nnew line"
        text = _wrap("patch_file", body, attrs='path="a.py" line=5')
        calls = parse_call_calls(text)
        assert calls[0].args["insert"] == "new line"
        assert calls[0].args["line"] == 5

    def test_delete_lines_attr(self):
        text = _wrap("patch_file", "", attrs='path="a.py" delete_lines="3-5"')
        calls = parse_call_calls(text)
        assert calls[0].args["delete_lines"] == "3-5"

    def test_no_path_returns_none(self):
        body = "--- FIND ---\nold\n--- REPLACE ---\nnew"
        text = _wrap("patch_file", body)
        assert parse_call_calls(text) == []


class TestApplyDiff:
    def test_diff_body(self):
        diff = "--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new"
        text = _wrap("apply_diff", diff)
        calls = parse_call_calls(text)
        assert calls[0].args["diff"] == diff


class TestUnknownTool:
    def test_unknown_returns_no_calls(self):
        text = _wrap("totally_unknown_tool", "{}")
        assert parse_call_calls(text) == []


class TestShellAlias:
    def test_cmd_renamed_to_command(self):
        text = _wrap("shell", '{"cmd": "ls -la"}')
        calls = parse_call_calls(text)
        assert calls[0].args["command"] == "ls -la"
        assert "cmd" not in calls[0].args


class TestStripAndDetect:
    def test_has_call_calls_true(self):
        text = _wrap("ls", "{}")
        assert has_call_calls(text) is True

    def test_has_call_calls_false(self):
        assert has_call_calls("plain text") is False

    def test_old_backtick_fence_not_detected(self):
        text = "```call ls\n{}\n```\n"
        assert has_call_calls(text) is False
        assert parse_call_calls(text) == []

    def test_old_tilde_fence_not_detected(self):
        text = "~`~call ls\n{}\n~`~call\n"
        assert has_call_calls(text) is False
        assert parse_call_calls(text) == []

    def test_strip_removes_blocks(self):
        text = "before\n" + _wrap("ls", "{}") + "after"
        result = strip_call_calls(text)
        assert ":::call" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result


class TestTruncatedAndStream:
    def test_find_next_complete(self):
        text = _wrap("ls", "{}")
        info = find_next_complete_call(text)
        assert info is not None
        assert info["tool_name"] == "ls"

    def test_find_next_partial_unclosed(self):
        text = ':::call read_files\n{"path": "a'
        info = find_next_partial_call(text)
        assert info is not None
        assert info["tool_name"] == "read_files"

    def test_find_next_call_start(self):
        text = "preamble\n:::call ls\n{}\ncall:::\n"
        pos = find_next_call_start(text)
        assert text[pos:].startswith(":::call ls")


class TestIterCallBlocks:
    def test_yields_match_and_call(self):
        text = _wrap("ls", "{}") + _wrap("read_files", '{"path": "a.py"}')
        pairs = list(iter_call_blocks(text))
        assert len(pairs) == 2
        m1, call1 = pairs[0]
        assert call1.tool_name == "ls"
        assert m1.group("name") == "ls"


@pytest.mark.parametrize("tool", ["read_files", "ls", "tree", "shell", "grep_files"])
def test_known_tools_parse(tool):
    text = _wrap(tool, '{"path": "."}')
    calls = parse_call_calls(text)
    assert len(calls) == 1
    assert calls[0].tool_name == tool