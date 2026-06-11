"""ui/completer.py — slash commands и @-references."""

from prompt_toolkit.document import Document

from ui.completer import (
    _find_at_reference,
    _format_size,
    _format_tokens,
    _slash_commands,
    SlashCommandCompleter,
    FileAtCompleter,
)

_SLASH_COMMANDS = _slash_commands()


class TestFindAtReference:
    def test_at_start(self):
        assert _find_at_reference("@foo", 4) == "foo"

    def test_after_space(self):
        assert _find_at_reference("hi @foo", 7) == "foo"

    def test_no_at(self):
        assert _find_at_reference("hello", 5) is None

    def test_at_with_no_space_before(self):
        # эл.почта: x@foo — НЕ должно триггерить @-completion
        assert _find_at_reference("x@foo", 5) is None

    def test_at_with_path_slash(self):
        assert _find_at_reference("@src/main.py", 12) == "src/main.py"

    def test_space_after_at_breaks(self):
        # @ followed by space → не активный reference
        result = _find_at_reference("@hi there", 9)
        assert result is None

    def test_empty_after_at(self):
        assert _find_at_reference("@", 1) == ""


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500B"

    def test_kilo(self):
        assert _format_size(2048) == "2.0K"

    def test_mega(self):
        assert _format_size(2 * 1024 * 1024) == "2.0M"


class TestFormatTokens:
    def test_small(self):
        assert _format_tokens(1000)  # 250 tok ≈ "~250 tok"

    def test_kilo(self):
        result = _format_tokens(40000)
        assert "K" in result and "tok" in result

    def test_million(self):
        result = _format_tokens(40_000_000)
        assert "M" in result


class TestSlashCommandsList:
    def test_all_have_help_text(self):
        for name, desc_key, _args_hint, _toggle_key, _order in _SLASH_COMMANDS:
            assert name.startswith("/")
            assert desc_key

    def test_no_duplicates(self):
        cmds = [item[0] for item in _SLASH_COMMANDS]
        assert len(cmds) == len(set(cmds))


class TestSlashCompleter:
    def test_completes_slash(self):
        comp = SlashCommandCompleter()
        doc = Document("/co", cursor_position=3)
        results = list(comp.get_completions(doc, None))
        texts = [r.text for r in results]
        assert any(t.startswith("/co") for t in texts)

    def test_no_completion_for_non_slash(self):
        comp = SlashCommandCompleter()
        doc = Document("hello", cursor_position=5)
        assert list(comp.get_completions(doc, None)) == []

    def test_no_completion_after_space(self):
        comp = SlashCommandCompleter()
        doc = Document("/cd /tmp", cursor_position=8)
        assert list(comp.get_completions(doc, None)) == []


class TestFileAtCompleter:
    def test_no_at_no_completion(self, tmp_workdir):
        c = FileAtCompleter(working_dir=str(tmp_workdir))
        doc = Document("hello", cursor_position=5)
        assert list(c.get_completions(doc, None)) == []

    def test_all_keyword(self, tmp_workdir):
        c = FileAtCompleter(working_dir=str(tmp_workdir))
        doc = Document("@", cursor_position=1)
        results = list(c.get_completions(doc, None))
        texts = [r.text for r in results]
        assert "all" in texts

    def test_file_listing(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        c = FileAtCompleter(working_dir=str(tmp_workdir))
        doc = Document("@", cursor_position=1)
        results = list(c.get_completions(doc, None))
        texts = [r.text for r in results]
        assert "a.py" in texts

    def test_filter(self, tmp_workdir):
        (tmp_workdir / "alpha.py").write_text("x")
        (tmp_workdir / "beta.py").write_text("x")
        c = FileAtCompleter(working_dir=str(tmp_workdir))
        doc = Document("@al", cursor_position=3)
        results = list(c.get_completions(doc, None))
        texts = [r.text for r in results]
        assert "alpha.py" in texts
        assert "beta.py" not in texts