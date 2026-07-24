"""tools/call_parser.py — парсер :::call ... call::: блоков."""

import pytest

from tools.call_parser import (
    find_next_call_start,
    find_next_complete_call,
    find_next_partial_call,
    has_call_calls,
    iter_call_blocks,
    normalize_call_markers,
    parse_call_calls,
    strip_call_calls,
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
            + _wrap("shell", '{"command": "ls"}')
        )
        calls = parse_call_calls(text)
        assert len(calls) == 2
        assert calls[0].tool_name == "read_files"
        assert calls[1].tool_name == "shell"

    def test_inline_triple_colon_after_sentence_parses(self):
        text = 'Пробую системный Chromium.:::call shell\n{"command": "echo ok"}\ncall:::'
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert calls[0].tool_name == "shell"
        assert calls[0].args == {"command": "echo ok"}

    def test_memory_tools_parse(self):
        # Регрессия: memory_* были в TOOL_REGISTRY, но отсутствовали в
        # NAMED_TOOLS → парсер ругался "unknown tool".
        for tool in ("memory_list", "memory_read", "memory_write"):
            calls = parse_call_calls(_wrap(tool, "{}"))
            assert len(calls) == 1, tool
            assert calls[0].tool_name == tool


class TestKnownToolsCoverage:
    def test_every_registered_tool_is_parseable(self):
        # _is_known_tool обязан признавать любой инструмент из реестра, иначе
        # модель не сможет его вызвать через :::call (как было с memory_*).
        from tools.call_parser import _is_known_tool
        from tools.registry import TOOL_REGISTRY

        missing = [t for t in TOOL_REGISTRY if not _is_known_tool(t)]
        assert missing == [], f"tools in registry but unparseable: {missing}"

    def test_body_with_triple_backticks(self):
        body = 'print(1)\n```python\nnested\n```\nafter'
        text = f':::call create_file path="a.py"\n{body}\ncall:::\n'
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert "nested" in calls[0].args["content"]
        assert "```python" in calls[0].args["content"]

    def test_body_with_tildes(self):
        body = 'text\n~~~\nstuff\n~~~\nmore'
        text = f':::call create_file path="a.py"\n{body}\ncall:::\n'
        calls = parse_call_calls(text)
        assert len(calls) == 1
        assert "~~~" in calls[0].args["content"]


class TestAttrs:
    def test_path_attr(self):
        text = _wrap("create_file", "print('hi')", attrs='path="a.py"')
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
        text = _wrap("shell", '{"command": "ls"}', attrs="background=true")
        calls = parse_call_calls(text)
        assert calls[0].args.get("background") is True

    def test_bool_attr_false(self):
        text = _wrap("shell", '{"command": "ls"}', attrs="background=false")
        calls = parse_call_calls(text)
        assert calls[0].args.get("background") is False

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
    def test_create_file_requires_path(self):
        text = _wrap("create_file", "content")
        assert parse_call_calls(text) == []

    def test_create_file_with_content(self):
        text = _wrap("create_file", "print('x')\n", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["content"] == "print('x')"

    def test_content_strips_one_trailing_newline(self):
        text = _wrap("create_file", "abc\n", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].args["content"] == "abc"

    def test_create_file_command_alias(self):
        text = _wrap("create_file", "x", attrs='path="a.py"')
        calls = parse_call_calls(text)
        assert calls[0].command == "create_file a.py"


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
        text = _wrap("shell", "{}")
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
        text = "before\n" + _wrap("shell", "{}") + "after"
        result = strip_call_calls(text)
        assert ":::call" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result


class TestTruncatedAndStream:
    def test_find_next_complete(self):
        text = _wrap("shell", "{}")
        info = find_next_complete_call(text)
        assert info is not None
        assert info["tool_name"] == "shell"

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
        text = _wrap("shell", "{}") + _wrap("read_files", '{"path": "a.py"}')
        pairs = list(iter_call_blocks(text))
        assert len(pairs) == 2
        m1, call1 = pairs[0]
        assert call1.tool_name == "shell"
        assert m1.group("name") == "shell"


@pytest.mark.parametrize("tool", ["read_files", "grep", "shell", "poll", "create_file"])
def test_known_tools_parse(tool):
    text = _wrap(tool, '{"path": "."}')
    calls = parse_call_calls(text)
    assert len(calls) == 1
    assert calls[0].tool_name == tool

class TestNormalizeMalformedMarkers:
    """Модель иногда роняет одно из трёх двоеточий. Near-miss должен
    исполняться (нормализуется в :::call), а не теряться."""

    def test_two_colon_open_parses(self):
        calls = parse_call_calls("::call memory_list\n{}\ncall:::")
        assert len(calls) == 1 and calls[0].tool_name == "memory_list"

    def test_valid_triple_unchanged(self):
        calls = parse_call_calls(':::call read_files\n{"path": "a.py"}\ncall:::')
        assert len(calls) == 1 and calls[0].args == {"path": "a.py"}

    def test_code_double_colon_preserved(self):
        # std::call_once в теле create_file НЕ должен превратиться в маркер
        text = ':::call create_file path="x.cpp"\nstd::call_once(flag, fn);\ncall:::'
        assert "std::call_once" in normalize_call_markers(text)

    def test_unknown_tool_not_normalized(self):
        # ::call перед НЕизвестным тулом — не вызов, не трогаем
        assert normalize_call_markers("::call somethingweird foo") == "::call somethingweird foo"

    def test_malformed_close_fixed(self):
        assert normalize_call_markers("call::") == "call:::"

    def test_bare_call_word_untouched(self):
        assert normalize_call_markers("call") == "call"


class TestTwoColonMarkerAllPaths:
    """::call (два двоеточия) должен исполняться ОДИНАКОВО на всех путях —
    парсинг, strip, has, и стриминг (complete/partial/start) — а не только в
    финальном parse_call_calls. Регулярки сами матчат 2-3 двоеточия."""

    DOUBLE = '::call read_files\n{"path": "a.py"}\ncall::\n'

    def test_parse(self):
        calls = parse_call_calls(self.DOUBLE)
        assert len(calls) == 1
        assert calls[0].tool_name == "read_files"
        assert calls[0].args == {"path": "a.py"}

    def test_has(self):
        assert has_call_calls(self.DOUBLE) is True

    def test_strip(self):
        assert "read_files" not in strip_call_calls(self.DOUBLE)

    def test_iter(self):
        blocks = [c for _m, c in iter_call_blocks(self.DOUBLE) if c]
        assert len(blocks) == 1 and blocks[0].tool_name == "read_files"

    def test_stream_complete(self):
        info = find_next_complete_call(self.DOUBLE, 0)
        assert info is not None and info["tool_name"] == "read_files"

    def test_stream_partial(self):
        info = find_next_partial_call('::call read_files\n{"path": "a', 0)
        assert info is not None and info["tool_name"] == "read_files"

    def test_stream_start(self):
        assert find_next_call_start("prefix\n::call shell\n{}\ncall::\n", 0) is not None

    def test_mixed_two_open_three_close(self):
        calls = parse_call_calls("::call shell\n{}\ncall:::\n")
        assert len(calls) == 1 and calls[0].tool_name == "shell"

    def test_midline_double_colon_not_a_call(self):
        # foo::call ВНЕ начала строки — не вызов (якорь ^ защищает код/прозу).
        assert has_call_calls("see foo::call read_files here") is False
        assert parse_call_calls("see foo::call read_files here") == []

    def test_inline_code_double_colon_masked(self):
        assert has_call_calls("use `::call read_files` to invoke") is False
