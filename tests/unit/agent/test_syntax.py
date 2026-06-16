"""agent/syntax.py — карта расширение→лексер для подсветки."""

from agent.syntax import _EXT_LEXER_MAP


class TestExtLexerMap:
    def test_common_extensions(self):
        assert _EXT_LEXER_MAP["py"] == "python"
        assert _EXT_LEXER_MAP["ts"] == "typescript"
        assert _EXT_LEXER_MAP["yml"] == "yaml"
        assert _EXT_LEXER_MAP["sh"] == "bash"
        assert _EXT_LEXER_MAP["json"] == "json"
