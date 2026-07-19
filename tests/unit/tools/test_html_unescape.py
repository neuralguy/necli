"""tools/_html_unescape.py — декодирование HTML-сущностей от прокси."""

from tools._html_unescape import has_html_entities, maybe_unescape, unescape_nested

QUOT = chr(38) + "quot;"  # "
AMP = chr(38) + "amp;"
LT = chr(38) + "lt;"
GT = chr(38) + "gt;"
APOS = chr(38) + "#39;"


class TestHasHtmlEntities:
    def test_empty_string(self):
        assert has_html_entities("") is False

    def test_plain_text(self):
        assert has_html_entities("hello world") is False

    def test_amp_entity(self):
        assert has_html_entities(f"foo {AMP} bar") is True

    def test_lt_gt(self):
        assert has_html_entities(f"a {LT} b {GT} c") is True

    def test_quot(self):
        assert has_html_entities(f"{QUOT}hi{QUOT}") is True

    def test_apos(self):
        assert has_html_entities(f"it{APOS}s") is True

    def test_numeric_entity(self):
        assert has_html_entities(chr(38) + "#1055;") is True

    def test_hex_entity(self):
        assert has_html_entities(chr(38) + "#x41;") is True

    def test_bare_ampersand_not_entity(self):
        assert has_html_entities("Tom & Jerry") is False


class TestMaybeUnescape:
    def test_empty(self):
        assert maybe_unescape("") == ""

    def test_no_entities_passthrough(self):
        text = "обычный текст без сущностей"
        assert maybe_unescape(text) is text

    def test_decodes_lt_gt(self):
        assert maybe_unescape(f"{LT}div{GT}") == "<div>"

    def test_decodes_amp(self):
        assert maybe_unescape(f"foo {AMP} bar") == "foo & bar"

    def test_decodes_quot(self):
        src = f"{QUOT}test{QUOT}"
        assert maybe_unescape(src) == chr(34) + "test" + chr(34)


class TestUnescapeNested:
    def test_string(self):
        assert unescape_nested(f"{LT}x{GT}") == "<x>"

    def test_int_passthrough(self):
        assert unescape_nested(42) == 42

    def test_none_passthrough(self):
        assert unescape_nested(None) is None

    def test_list_of_strings(self):
        result = unescape_nested([f"{LT}a{GT}", "plain", AMP])
        assert result == ["<a>", "plain", "&"]

    def test_dict_values(self):
        result = unescape_nested({"path": f"{LT}file{GT}", "n": 1})
        assert result == {"path": "<file>", "n": 1}

    def test_deeply_nested(self):
        data = {"a": [{"b": f"{LT}x{GT}", "c": [42, AMP]}]}
        assert unescape_nested(data) == {"a": [{"b": "<x>", "c": [42, "&"]}]}
