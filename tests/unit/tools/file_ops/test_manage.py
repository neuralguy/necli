"""tools/file_ops/manage.py — delete/rename/copy/move."""

from tools.file_ops.manage import delete_file, rename_file, copy_file, move_file
from tools.models import ToolCall


def _call(tool: str, **args) -> ToolCall:
    return ToolCall(command=tool, tool_name=tool, args=args)


class TestDeleteFile:
    def test_existing(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = delete_file(_call("delete_file", path="a.py"))
        assert r.status == "ok"
        assert not (tmp_workdir / "a.py").exists()

    def test_missing(self, tmp_workdir):
        r = delete_file(_call("delete_file", path="missing.py"))
        assert r.status == "error"

    def test_directory_rejected(self, tmp_workdir):
        (tmp_workdir / "subdir").mkdir()
        r = delete_file(_call("delete_file", path="subdir"))
        assert r.status == "error"
        assert "rmdir" in r.output.lower()

    def test_no_path(self, tmp_workdir):
        r = delete_file(_call("delete_file"))
        assert r.status == "error"


class TestRenameFile:
    def test_basic(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = rename_file(_call("rename_file", path="a.py", new_path="b.py"))
        assert r.status == "ok"
        assert not (tmp_workdir / "a.py").exists()
        assert (tmp_workdir / "b.py").read_text() == "x"

    def test_creates_parent_dir(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = rename_file(_call("rename_file", path="a.py", new_path="sub/b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "sub" / "b.py").read_text() == "x"

    def test_missing_source(self, tmp_workdir):
        r = rename_file(_call("rename_file", path="nope.py", new_path="b.py"))
        assert r.status == "error"

    def test_no_args(self, tmp_workdir):
        r = rename_file(_call("rename_file", path="a.py"))
        assert r.status == "error"


class TestCopyFile:
    def test_file(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = copy_file(_call("copy_file", path="a.py", dest="b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "data"
        assert (tmp_workdir / "b.py").read_text() == "data"

    def test_directory(self, tmp_workdir):
        (tmp_workdir / "src").mkdir()
        (tmp_workdir / "src" / "inner.py").write_text("x")
        r = copy_file(_call("copy_file", path="src", dest="dst"))
        assert r.status == "ok"
        assert (tmp_workdir / "dst" / "inner.py").exists()

    def test_missing(self, tmp_workdir):
        r = copy_file(_call("copy_file", path="nope", dest="x"))
        assert r.status == "error"


class TestMoveFile:
    def test_basic(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = move_file(_call("move_file", path="a.py", dest="sub/b.py"))
        assert r.status == "ok"
        assert not (tmp_workdir / "a.py").exists()
        assert (tmp_workdir / "sub" / "b.py").read_text() == "data"

    def test_missing(self, tmp_workdir):
        r = move_file(_call("move_file", path="nope.py", dest="b.py"))
        assert r.status == "error"


class TestCacheInvalidation:
    def test_delete_invalidates(self, tmp_workdir):
        from tools.file_ops.read import _READ_CACHE, _cache_record

        f = tmp_workdir / "a.py"
        f.write_text("x")
        _cache_record(f, str(f.resolve()), 1, 1)
        assert any(str(f.resolve()) in b for b in _READ_CACHE.values())

        delete_file(_call("delete_file", path="a.py"))
        # После delete запись должна быть удалена из всех session-buckets
        for bucket in _READ_CACHE.values():
            assert str(f.resolve()) not in bucket

    def test_rename_invalidates_source(self, tmp_workdir):
        from tools.file_ops.read import _READ_CACHE, _cache_record

        f = tmp_workdir / "a.py"
        f.write_text("x")
        _cache_record(f, str(f.resolve()), 1, 1)
        rename_file(_call("rename_file", path="a.py", new_path="b.py"))
        for bucket in _READ_CACHE.values():
            assert str(f.resolve()) not in bucket

    def test_move_invalidates_source(self, tmp_workdir):
        from tools.file_ops.read import _READ_CACHE, _cache_record

        f = tmp_workdir / "a.py"
        f.write_text("x")
        _cache_record(f, str(f.resolve()), 1, 1)
        move_file(_call("move_file", path="a.py", dest="b.py"))
        for bucket in _READ_CACHE.values():
            assert str(f.resolve()) not in bucket

class TestArgAliases:
    def test_rename_src_dst_aliases(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = rename_file(_call("rename_file", src="a.py", dst="b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "b.py").read_text() == "data"

    def test_rename_source_dest_aliases(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = rename_file(_call("rename_file", source="a.py", dest="b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "b.py").read_text() == "data"

    def test_copy_src_destination_aliases(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = copy_file(_call("copy_file", src="a.py", destination="b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "b.py").read_text() == "data"

    def test_move_source_dst_aliases(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = move_file(_call("move_file", source="a.py", dst="b.py"))
        assert r.status == "ok"
        assert not (tmp_workdir / "a.py").exists()
        assert (tmp_workdir / "b.py").read_text() == "data"

class TestMoreErrorCases:
    def test_copy_no_args(self, tmp_workdir):
        r = copy_file(_call("copy_file", path="a.py"))
        assert r.status == "error"

    def test_move_no_args(self, tmp_workdir):
        r = move_file(_call("move_file", path="a.py"))
        assert r.status == "error"

    def test_delete_empty_path_string(self, tmp_workdir):
        r = delete_file(_call("delete_file", path=""))
        assert r.status == "error"

    def test_rename_creates_nested_parents(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        r = rename_file(_call("rename_file", path="a.py", new_path="x/y/z/b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "x" / "y" / "z" / "b.py").read_text() == "x"

    def test_move_directory(self, tmp_workdir):
        (tmp_workdir / "src").mkdir()
        (tmp_workdir / "src" / "inner.py").write_text("y")
        r = move_file(_call("move_file", path="src", dest="dst"))
        assert r.status == "ok"
        assert not (tmp_workdir / "src").exists()
        assert (tmp_workdir / "dst" / "inner.py").read_text() == "y"

    def test_copy_into_new_parent_dir(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("data")
        r = copy_file(_call("copy_file", path="a.py", dest="deep/nested/b.py"))
        assert r.status == "ok"
        assert (tmp_workdir / "deep" / "nested" / "b.py").read_text() == "data"