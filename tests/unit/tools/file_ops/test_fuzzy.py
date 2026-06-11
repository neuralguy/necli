"""tools/file_ops/_fuzzy.py — fuzzy find/replace для patch_file."""

from tools.file_ops._fuzzy import _normalize_line, _fuzzy_find_replace


class TestNormalizeLine:
    def test_strips_double_spaces(self):
        assert _normalize_line("a   b") == "a b"

    def test_tabs_to_spaces(self):
        result = _normalize_line("a\tb")
        assert result == "a b"

    def test_leading_trailing(self):
        assert _normalize_line("  a b  ") == "a b"

    def test_empty(self):
        assert _normalize_line("") == ""


class TestFuzzyFindReplace:
    def test_exact_match(self):
        text = "line1\nold\nline3\n"
        result, found = _fuzzy_find_replace(text, "old", "new")
        assert found is True
        assert "new" in result
        assert "old" not in result

    def test_indented_block(self):
        text = "def foo():\n    old_code\n    rest\n"
        # find без отступа, в тексте — с отступом
        result, found = _fuzzy_find_replace(text, "old_code", "new_code")
        assert found is True
        assert "new_code" in result

    def test_whitespace_difference(self):
        text = "x  =   1\n"
        result, found = _fuzzy_find_replace(text, "x = 1", "x = 2")
        assert found is True
        assert "x = 2" in result

    def test_not_found(self):
        text = "abc\ndef\n"
        result, found = _fuzzy_find_replace(text, "xyz", "qqq")
        assert found is False
        assert result == text

    def test_empty_find(self):
        result, found = _fuzzy_find_replace("abc", "", "x")
        assert found is False
        assert result == "abc"

    def test_multiline_with_indent_pattern(self):
        text = "if cond:\n    a = 1\n    b = 2\n"
        find_block = "a = 1\nb = 2"
        replace_block = "a = 10\nb = 20"
        result, found = _fuzzy_find_replace(text, find_block, replace_block)
        assert found is True
        assert "a = 10" in result
        assert "b = 20" in result

    def test_preserves_trailing_newline(self):
        text = "old\n"
        result, found = _fuzzy_find_replace(text, "old", "new")
        assert found is True
        assert result == "new\n"

class TestFuzzyEdgeCases:
    def test_first_occurrence_only(self):
        text = "old\nmid\nold\n"
        result, found = _fuzzy_find_replace(text, "old", "new")
        assert found is True
        assert result == "new\nmid\nold\n"

    def test_indent_added_when_replace_dedented(self):
        # find без отступа, текст с отступом → replace получает тот же отступ.
        text = "if x:\n        body\n"
        result, found = _fuzzy_find_replace(text, "body", "newbody")
        assert found is True
        assert "        newbody" in result

    def test_blank_lines_in_replace_not_indented(self):
        text = "def f():\n    a\n    b\n"
        result, found = _fuzzy_find_replace(text, "a\nb", "x\n\ny")
        assert found is True
        lines = result.splitlines()
        # пустая строка между x и y остаётся без отступа
        assert "" in lines

    def test_no_trailing_newline_when_source_lacks_it(self):
        text = "old"
        result, found = _fuzzy_find_replace(text, "old", "new")
        assert found is True
        assert result == "new"

    def test_strip_fallback_matches_ignoring_indent(self):
        # Первая стратегия не совпадёт построчно по нормализации с разным
        # числом значащих токенов; fallback по strip всё равно найдёт блок.
        text = "class C:\n    def m(self):\n        return 1\n"
        find_block = "def m(self):\nreturn 1"
        result, found = _fuzzy_find_replace(text, find_block, "def m(self):\nreturn 2")
        assert found is True
        assert "return 2" in result

    def test_multiline_not_found_returns_original(self):
        text = "a\nb\nc\n"
        result, found = _fuzzy_find_replace(text, "x\ny", "p\nq")
        assert found is False
        assert result == text

    def test_replace_empty_removes_block(self):
        text = "keep\nremove\nkeep2\n"
        result, found = _fuzzy_find_replace(text, "remove", "")
        assert found is True
        assert "remove" not in result
        assert "keep" in result
        assert "keep2" in result

    def test_whitespace_only_find_treated_as_nonempty_lines(self):
        # find из одних пробелов → splitlines даёт [" "], не пустой список.
        result, found = _fuzzy_find_replace("abc\n", "   ", "x")
        assert found is False
        assert result == "abc\n"

class TestNormalizeLineExtra:
    def test_multiple_tabs(self):
        assert _normalize_line("\t\tx\t\ty") == "x y"

    def test_only_whitespace(self):
        assert _normalize_line("   \t  ") == ""

    def test_mixed_tabs_and_spaces(self):
        assert _normalize_line("a\t  \tb") == "a b"