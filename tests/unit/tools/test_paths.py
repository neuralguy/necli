"""tools/_paths.py — резолв путей и working dir."""

import os

from tools._paths import (
    WorkingDirectory,
    resolve_path,
    clean_path,
    set_working_dir,
    get_working_dir,
)


class TestWorkingDirectory:
    def test_default_is_cwd(self):
        wd = WorkingDirectory()
        assert wd.get() == os.getcwd()

    def test_explicit_path(self):
        wd = WorkingDirectory("/tmp")
        assert wd.get() == "/tmp"

    def test_set(self):
        wd = WorkingDirectory("/tmp")
        wd.set("/var")
        assert wd.get() == "/var"


class TestGlobalWorkingDir:
    def test_get_set_global(self):
        orig = get_working_dir()
        try:
            set_working_dir("/tmp")
            assert get_working_dir() == "/tmp"
        finally:
            set_working_dir(orig)


class TestResolvePath:
    def test_absolute(self, tmp_path):
        target = tmp_path / "x.py"
        target.write_text("ok")
        result = resolve_path(str(target))
        assert result == target.resolve()

    def test_relative_joined_with_workdir(self, tmp_workdir):
        result = resolve_path("foo.py")
        assert result == (tmp_workdir / "foo.py").resolve()

    def test_expands_tilde(self):
        result = resolve_path("~/file.txt")
        home = os.path.expanduser("~")
        assert str(result).startswith(home)

    def test_expands_envvar(self, monkeypatch, tmp_workdir):
        monkeypatch.setenv("TEST_DIR", str(tmp_workdir))
        result = resolve_path("$TEST_DIR/x.py")
        assert result == (tmp_workdir / "x.py").resolve()

    def test_symlink_not_followed(self, tmp_workdir):
        # resolve_path использует normpath, НЕ realpath — симлинки НЕ
        # резолвятся (критично для изоляции субагентов в git worktree).
        target = tmp_workdir / "real.txt"
        target.write_text("hi")
        link = tmp_workdir / "link.txt"
        link.symlink_to(target)
        result = resolve_path(str(link))
        assert result == link


class TestCleanPath:
    def test_strips_whitespace(self):
        assert clean_path("  foo.py  ") == "foo.py"

    def test_strips_double_quotes(self):
        assert clean_path('"a.py"') == "a.py"

    def test_strips_single_quotes(self):
        assert clean_path("'a.py'") == "a.py"

    def test_mismatched_quotes_kept(self):
        assert clean_path("'a.py\"") == "'a.py\""

    def test_short_string_no_strip(self):
        assert clean_path('"') == '"'

    def test_empty(self):
        assert clean_path("") == ""

    def test_internal_quotes_kept(self):
        assert clean_path('a"b') == 'a"b'