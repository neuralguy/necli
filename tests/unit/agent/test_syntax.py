"""agent/syntax.py — определение лексера для подсветки вывода инструментов."""

from agent.syntax import guess_output_lexer, _EXT_LEXER_MAP

class TestEmptyAndNonShell:
    def test_empty_output_returns_none(self):
        assert guess_output_lexer("", cmd="cat a.py") is None
        assert guess_output_lexer("   \n  ", cmd="cat a.py") is None

    def test_patch_file_diff(self):
        out = "--- FIND ---\nold\n--- REPLACE ---\nnew"
        assert guess_output_lexer(out, tool_name="patch_file") == "diff"

    def test_patch_file_without_dashes(self):
        assert guess_output_lexer("applied ok", tool_name="patch_file") is None

    def test_read_files_uses_extension(self):
        out = "[main.py · 10 lines]\nprint(1)"
        assert guess_output_lexer(out, tool_name="read_files") == "python"

    def test_read_files_unknown_ext(self):
        out = "[data.xyz · 3 lines]\nstuff"
        assert guess_output_lexer(out, tool_name="read_files") is None

    def test_read_files_no_header(self):
        assert guess_output_lexer("no header here", tool_name="read_files") is None

    def test_grep_files_none(self):
        assert guess_output_lexer("a.py:1:hit", tool_name="grep_files") is None

    def test_unknown_tool_none(self):
        assert guess_output_lexer("whatever", tool_name="some_tool") is None

class TestShellInterpreters:
    def test_python_traceback(self):
        out = "Traceback (most recent call last):\n  File x\nValueError"
        assert guess_output_lexer(out, cmd="python3 x.py") == "pytb"

    def test_python_plain_none(self):
        assert guess_output_lexer("hello", cmd="python3 x.py") is None

    def test_node_no_traceback(self):
        assert guess_output_lexer("output", cmd="node app.js") is None

    def test_sudo_prefix_stripped(self):
        out = "Traceback (most recent call last):\nboom"
        assert guess_output_lexer(out, cmd="sudo python3 x.py") == "pytb"

class TestPlainOutputCmds:
    def test_ls_returns_none(self):
        assert guess_output_lexer("file1\nfile2", cmd="ls -la") is None

    def test_git_diff(self):
        out = "diff --git a/x b/x\n+added"
        assert guess_output_lexer(out, cmd="git diff") == "diff"

    def test_git_show(self):
        out = "commit abc\n--- a/x"
        assert guess_output_lexer(out, cmd="git show HEAD") == "diff"

    def test_git_status_none(self):
        assert guess_output_lexer("on branch main", cmd="git status") is None

class TestFileViewerCmds:
    def test_cat_python_ext(self):
        assert guess_output_lexer("print(1)", cmd="cat main.py") == "python"

    def test_head_json_ext(self):
        assert guess_output_lexer('{"a":1}', cmd="head config.json") == "json"

    def test_cat_unknown_ext_falls_through(self):
        # .xyz unknown -> no viewer match, but content is not json/xml -> None
        assert guess_output_lexer("plain", cmd="cat note.xyz") is None

class TestContentSniffing:
    def test_json_object(self):
        assert guess_output_lexer('{"key": "value"}', cmd="someprog") == "json"

    def test_json_array(self):
        assert guess_output_lexer("[1, 2, 3]", cmd="someprog") == "json"

    def test_invalid_json_not_detected(self):
        assert guess_output_lexer("{not json", cmd="someprog") is None

    def test_xml_declaration(self):
        assert guess_output_lexer('<?xml version="1.0"?>\n<a/>', cmd="someprog") == "xml"

    def test_xml_namespace(self):
        out = '<root xmlns="http://example.com">x</root>'
        assert guess_output_lexer(out, cmd="someprog") == "xml"

    def test_html_doctype(self):
        assert guess_output_lexer("<!DOCTYPE html>\n<p>", cmd="someprog") == "html"

    def test_html_tag(self):
        assert guess_output_lexer("<html><body></body></html>", cmd="someprog") == "html"

    def test_diff_git_header(self):
        assert guess_output_lexer("diff --git a/x b/x", cmd="someprog") == "diff"

    def test_diff_minus_header(self):
        assert guess_output_lexer("--- a/file.py\n+++ b/file.py", cmd="someprog") == "diff"

    def test_traceback_in_arbitrary_output(self):
        out = "noise\nTraceback (most recent call last):\nErr"
        assert guess_output_lexer(out, cmd="someprog") == "pytb"

    def test_grep_returns_none(self):
        assert guess_output_lexer("match here", cmd="grep foo *.py") is None

    def test_find_returns_none(self):
        assert guess_output_lexer("./a\n./b", cmd="find . -name x") is None

    def test_plain_text_returns_none(self):
        assert guess_output_lexer("just words", cmd="someprog") is None

class TestExtLexerMap:
    def test_common_extensions(self):
        assert _EXT_LEXER_MAP["py"] == "python"
        assert _EXT_LEXER_MAP["ts"] == "typescript"
        assert _EXT_LEXER_MAP["yml"] == "yaml"
        assert _EXT_LEXER_MAP["sh"] == "bash"