"""tools/json_repair.py — robust JSON parsing for LLM output."""

from tools.json_repair import (
    robust_json_loads,
    extract_field_value,
    extract_bool_field,
    extract_int_field,
    decode_json_string_value,
    greedy_extract_content_json,
)


class TestRobustJsonLoads:
    def test_empty(self):
        assert robust_json_loads("") is None
        assert robust_json_loads("   ") is None

    def test_valid_object(self):
        assert robust_json_loads('{"a": 1}') == {"a": 1}

    def test_valid_array(self):
        assert robust_json_loads("[1, 2, 3]") == [1, 2, 3]

    def test_trailing_comma_object(self):
        assert robust_json_loads('{"a": 1,}') == {"a": 1}

    def test_trailing_comma_array(self):
        assert robust_json_loads("[1, 2, 3,]") == [1, 2, 3]

    def test_single_quotes_only(self):
        assert robust_json_loads("{'a': 'b'}") == {"a": "b"}

    def test_unquoted_keys(self):
        assert robust_json_loads('{a: 1, b: 2}') == {"a": 1, "b": 2}

    def test_bom_prefix(self):
        assert robust_json_loads('\ufeff{"a": 1}') == {"a": 1}

    def test_zero_width_prefix(self):
        assert robust_json_loads('\u200b{"a": 1}') == {"a": 1}

    def test_raw_newline_in_string(self):
        s = '{"path": "a.py", "content": "line1\nline2"}'
        result = robust_json_loads(s)
        assert result == {"path": "a.py", "content": "line1\nline2"}

    def test_invalid_escape_in_string(self):
        s = r'{"content": "\d+"}'
        result = robust_json_loads(s)
        assert result is not None
        assert "content" in result

    def test_line_comment(self):
        s = '{\n"a": 1 // comment\n}'
        result = robust_json_loads(s)
        assert result == {"a": 1}

    def test_block_comment(self):
        s = '{"a": /* hi */ 1}'
        result = robust_json_loads(s)
        assert result == {"a": 1}

    def test_garbage_unrepairable_returns_none(self):
        assert robust_json_loads("just a string with no json") is None

    def test_extracts_braces_with_surrounding_garbage(self):
        s = 'sure here: {"a": 1} ok'
        result = robust_json_loads(s)
        assert result == {"a": 1}

    def test_combo_unquoted_and_trailing(self):
        result = robust_json_loads('{a: 1, b: 2,}')
        assert result == {"a": 1, "b": 2}


class TestGreedyExtractContent:
    def test_simple_content(self):
        s = '{"path": "x.py", "content": "hello"}'
        result = greedy_extract_content_json(s)
        assert result == {"path": "x.py", "content": "hello"}

    def test_content_with_unescaped_quotes_at_end(self):
        s = '{"path": "x.py", "content": "code with \\"quote\\" inside"}'
        result = greedy_extract_content_json(s)
        assert result is not None
        assert result["path"] == "x.py"

    def test_no_content_field(self):
        assert greedy_extract_content_json('{"path": "x.py"}') is None

    def test_no_path_field_returns_none(self):
        assert greedy_extract_content_json('{"content": "hi"}') is None


class TestExtractFieldValue:
    def test_simple_string(self):
        assert extract_field_value('{"path": "a.py"}', "path") == "a.py"

    def test_single_quoted(self):
        assert extract_field_value("{'path': 'a.py'}", "path") == "a.py"

    def test_escape_n_decoded(self):
        s = '{"name": "a\\nb"}'
        assert extract_field_value(s, "name") == "a\nb"

    def test_escape_quote(self):
        s = '{"name": "a\\"b"}'
        assert extract_field_value(s, "name") == 'a"b'

    def test_missing_field_returns_none(self):
        assert extract_field_value('{"a": 1}', "missing") is None


class TestExtractBoolInt:
    def test_bool_true(self):
        assert extract_bool_field('{"force": true}', "force") is True

    def test_bool_false(self):
        assert extract_bool_field('{"force": false}', "force") is False

    def test_bool_missing(self):
        assert extract_bool_field('{}', "force") is None

    def test_int_positive(self):
        assert extract_int_field('{"line": 42}', "line") == 42

    def test_int_negative(self):
        assert extract_int_field('{"depth": -1}', "depth") == -1

    def test_int_missing(self):
        assert extract_int_field('{}', "line") is None


class TestDecodeJsonStringValue:
    def test_plain(self):
        assert decode_json_string_value("hello") == "hello"

    def test_newline(self):
        assert decode_json_string_value(r"a\nb") == "a\nb"

    def test_tab(self):
        assert decode_json_string_value(r"a\tb") == "a\tb"

    def test_carriage_return(self):
        assert decode_json_string_value(r"a\rb") == "a\rb"

    def test_quote(self):
        assert decode_json_string_value(r'a\"b') == 'a"b'

    def test_backslash(self):
        assert decode_json_string_value(r"a\\b") == "a\\b"

    def test_forward_slash(self):
        assert decode_json_string_value(r"a\/b") == "a/b"

    def test_unicode_escape(self):
        assert decode_json_string_value(r"\u0041") == "A"

    def test_unknown_escape_kept_as_is(self):
        # \q — неизвестная последовательность, должна сохраниться
        result = decode_json_string_value(r"a\qb")
        assert "q" in result