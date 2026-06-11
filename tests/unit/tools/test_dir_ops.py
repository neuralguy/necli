"""tools/dir_ops.py — ls, tree, mkdir, rmdir, find_files, grep_files."""

import os

from tools.dir_ops import (
    ls, tree, mkdir, rmdir, find_files, grep_files,
    _format_size,
)
from tools.models import ToolCall


def _call(tool: str, **args) -> ToolCall:
    return ToolCall(command=tool, tool_name=tool, args=args)


class TestFormatSize:
    def test_bytes(self):
        assert _format_size(500) == "500B"

    def test_kilobytes(self):
        assert _format_size(2048) == "2.0K"

    def test_megabytes(self):
        assert _format_size(2 * 1024 * 1024) == "2.0M"

    def test_gigabytes(self):
        assert _format_size(2 * 1024 * 1024 * 1024) == "2.0G"


class TestLs:
    def test_basic(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        (tmp_workdir / "sub").mkdir()
        r = ls(_call("ls", path="."))
        assert r.status == "ok"
        assert "a.py" in r.output
        assert "sub" in r.output

    def test_missing(self, tmp_workdir):
        r = ls(_call("ls", path="missing_dir"))
        assert r.status == "error"

    def test_file_arg_returns_single_entry(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = ls(_call("ls", path="a.py"))
        assert r.status == "ok"
        assert "a.py" in r.output

    def test_hidden_excluded_by_default(self, tmp_workdir):
        (tmp_workdir / ".hidden").write_text("x")
        (tmp_workdir / "visible.py").write_text("y")
        r = ls(_call("ls", path="."))
        assert ".hidden" not in r.output
        assert "visible.py" in r.output

    def test_hidden_included_with_all(self, tmp_workdir):
        (tmp_workdir / ".hidden").write_text("x")
        r = ls(_call("ls", path=".", all=True))
        assert ".hidden" in r.output


class TestTree:
    def test_basic(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        (tmp_workdir / "sub").mkdir()
        (tmp_workdir / "sub" / "b.py").write_text("y")
        r = tree(_call("tree", path=".", depth=3))
        assert r.status == "ok"
        assert "a.py" in r.output
        assert "sub" in r.output
        assert "b.py" in r.output

    def test_depth_limit(self, tmp_workdir):
        (tmp_workdir / "sub").mkdir()
        (tmp_workdir / "sub" / "nested").mkdir()
        (tmp_workdir / "sub" / "nested" / "deep.py").write_text("x")
        r = tree(_call("tree", path=".", depth=1))
        assert "sub" in r.output
        assert "deep.py" not in r.output

    def test_missing(self, tmp_workdir):
        r = tree(_call("tree", path="missing"))
        assert r.status == "error"

    def test_file_not_dir(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = tree(_call("tree", path="a.py"))
        assert r.status == "error"


class TestMkdir:
    def test_creates(self, tmp_workdir):
        r = mkdir(_call("mkdir", path="newdir"))
        assert r.status == "ok"
        assert (tmp_workdir / "newdir").is_dir()

    def test_with_parents(self, tmp_workdir):
        r = mkdir(_call("mkdir", path="a/b/c"))
        assert r.status == "ok"
        assert (tmp_workdir / "a" / "b" / "c").is_dir()

    def test_already_exists_ok(self, tmp_workdir):
        (tmp_workdir / "x").mkdir()
        r = mkdir(_call("mkdir", path="x"))
        assert r.status == "ok"

    def test_file_in_way(self, tmp_workdir):
        (tmp_workdir / "x").write_text("file")
        r = mkdir(_call("mkdir", path="x"))
        assert r.status == "error"

    def test_no_path(self, tmp_workdir):
        r = mkdir(_call("mkdir"))
        assert r.status == "error"


class TestRmdir:
    def test_empty(self, tmp_workdir):
        (tmp_workdir / "x").mkdir()
        r = rmdir(_call("rmdir", path="x"))
        assert r.status == "ok"
        assert not (tmp_workdir / "x").exists()

    def test_nonempty_without_force(self, tmp_workdir):
        d = tmp_workdir / "x"
        d.mkdir()
        (d / "child").write_text("y")
        r = rmdir(_call("rmdir", path="x"))
        assert r.status == "error"
        assert d.exists()

    def test_nonempty_with_force(self, tmp_workdir):
        d = tmp_workdir / "x"
        d.mkdir()
        (d / "child").write_text("y")
        r = rmdir(_call("rmdir", path="x", force=True))
        assert r.status == "ok"
        assert not d.exists()

    def test_dangerous_path_rejected(self, tmp_workdir):
        r = rmdir(_call("rmdir", path="/", force=True))
        assert r.status == "error"
        assert "/" in r.output

    def test_home_path_rejected(self, tmp_workdir):
        home = os.path.expanduser("~")
        r = rmdir(_call("rmdir", path=home, force=True))
        assert r.status == "error"

    def test_not_dir(self, tmp_workdir):
        (tmp_workdir / "x.py").write_text("y")
        r = rmdir(_call("rmdir", path="x.py"))
        assert r.status == "error"

    def test_no_path(self, tmp_workdir):
        r = rmdir(_call("rmdir"))
        assert r.status == "error"


class TestFindFiles:
    def test_by_pattern(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        (tmp_workdir / "b.txt").write_text("y")
        r = find_files(_call("find_files", path=".", pattern="*.py"))
        assert r.status == "ok"
        assert "a.py" in r.output
        assert "b.txt" not in r.output

    def test_by_name_exact(self, tmp_workdir):
        (tmp_workdir / "exact.py").write_text("x")
        (tmp_workdir / "other.py").write_text("y")
        r = find_files(_call("find_files", path=".", name="exact.py"))
        assert "exact.py" in r.output
        assert "other.py" not in r.output

    def test_type_file(self, tmp_workdir):
        (tmp_workdir / "match").mkdir()
        (tmp_workdir / "match.py").write_text("x")
        r = find_files(_call("find_files", path=".", pattern="match*", type="file"))
        assert "match.py" in r.output
        # match (dir) не должен присутствовать как match с emoji 📁
        lines = [ln for ln in r.output.splitlines() if "match" in ln and "📁" in ln]
        assert lines == []

    def test_type_dir(self, tmp_workdir):
        (tmp_workdir / "d").mkdir()
        (tmp_workdir / "d.py").write_text("x")
        r = find_files(_call("find_files", path=".", pattern="d*", type="dir"))
        assert "d" in r.output

    def test_no_pattern_or_name(self, tmp_workdir):
        r = find_files(_call("find_files", path="."))
        assert r.status == "error"

    def test_missing_dir(self, tmp_workdir):
        r = find_files(_call("find_files", path="missing", pattern="*.py"))
        assert r.status == "error"

    def test_no_match(self, tmp_workdir):
        r = find_files(_call("find_files", path=".", pattern="*.nonexistent"))
        assert r.status == "ok"
        assert "Ничего не найдено" in r.output or "not found" in r.output.lower()


class TestGrepFiles:
    def test_literal(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("hello world\nfoo bar\n")
        (tmp_workdir / "b.py").write_text("nothing here\n")
        r = grep_files(_call("grep_files", path=".", pattern="hello"))
        assert r.status == "ok"
        assert "a.py" in r.output
        assert "hello world" in r.output

    def test_regex(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("foo123\nfoo\n")
        r = grep_files(_call("grep_files", path=".", pattern=r"foo\d+"))
        assert r.status == "ok"
        assert "foo123" in r.output

    def test_invalid_regex(self, tmp_workdir):
        r = grep_files(_call("grep_files", path=".", pattern="(unclosed"))
        assert r.status == "error"

    def test_literal_with_special_chars(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x.y(z)\n")
        r = grep_files(_call("grep_files", path=".", pattern="x.y(z)", literal=True))
        assert r.status == "ok"
        assert "x.y(z)" in r.output

    def test_ignore_case(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("Hello World\n")
        r = grep_files(_call("grep_files", path=".", pattern="hello", ignore_case=True))
        assert "Hello World" in r.output

    def test_glob_filter(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("target\n")
        (tmp_workdir / "b.txt").write_text("target\n")
        r = grep_files(_call("grep_files", path=".", pattern="target", glob="*.py"))
        assert "a.py" in r.output
        assert "b.txt" not in r.output

    def test_skips_binary(self, tmp_workdir):
        (tmp_workdir / "image.png").write_bytes(b"target text\x00binary\n")
        r = grep_files(_call("grep_files", path=".", pattern="target"))
        assert "image.png" not in r.output

    def test_no_match(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("hello\n")
        r = grep_files(_call("grep_files", path=".", pattern="missing"))
        assert r.status == "ok"
        out = r.output.lower()
        assert "no matches" in out or "совпадений нет" in out

    def test_empty_pattern(self, tmp_workdir):
        r = grep_files(_call("grep_files", path=".", pattern=""))
        assert r.status == "error"

    def test_single_file_target(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("hello\n")
        r = grep_files(_call("grep_files", path="a.py", pattern="hello"))
        assert "hello" in r.output

    def test_context(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("line1\nline2\nMATCH\nline4\nline5\n")
        r = grep_files(_call("grep_files", path=".", pattern="MATCH", context=1))
        assert "MATCH" in r.output
        assert "line2" in r.output
        assert "line4" in r.output