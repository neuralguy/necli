"""tools/file_ops/patch.py — patch_file."""

from tools.file_ops.patch import patch_file
from tools.models import ToolCall


def _call(**args) -> ToolCall:
    return ToolCall(command="patch_file", tool_name="patch_file", args=args)


class TestFindReplace:
    def test_basic(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x = 1\ny = 2\n")
        r = patch_file(_call(path="a.py", find="x = 1", replace="x = 100"))
        assert r.status == "ok"
        assert "x = 100" in (tmp_workdir / "a.py").read_text()

    def test_not_found_returns_error(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("foo\n")
        r = patch_file(_call(path="a.py", find="missing", replace="x"))
        assert r.status == "error"
        assert "not found" in r.output.lower()

    def test_fuzzy_fallback(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x  =   1\n")
        r = patch_file(_call(path="a.py", find="x = 1", replace="x = 2"))
        assert r.status == "ok"
        assert "x = 2" in (tmp_workdir / "a.py").read_text()

    def test_only_first_occurrence(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("foo\nfoo\nfoo\n")
        r = patch_file(_call(path="a.py", find="foo", replace="bar"))
        assert r.status == "ok"
        text = (tmp_workdir / "a.py").read_text()
        assert text.count("foo") == 2
        assert text.count("bar") == 1


class TestPatches:
    def test_multiple_patches(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a = 1\nb = 2\n")
        patches = [
            {"find": "a = 1", "replace": "a = 10"},
            {"find": "b = 2", "replace": "b = 20"},
        ]
        r = patch_file(_call(path="a.py", patches=patches))
        # patches в одном вызове больше не поддерживается — возвращается error.
        assert r.status == "error"
        assert "not allowed" in r.output.lower()

    def test_patches_as_json_string(self, tmp_workdir):
        import json
        (tmp_workdir / "a.py").write_text("a = 1\n")
        patches_str = json.dumps([{"find": "a = 1", "replace": "a = 2"}])
        r = patch_file(_call(path="a.py", patches=patches_str))
        # patches-аргумент отклоняется независимо от формата.
        assert r.status == "error"
        assert "not allowed" in r.output.lower()

    def test_patches_invalid_string(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py", patches="not json"))
        assert r.status == "error"

    def test_patches_not_list_error(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py", patches={"find": "x"}))
        assert r.status == "error"

    def test_empty_find_in_patch_skipped(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py", patches=[{"find": "", "replace": "y"}]))
        # patches-аргумент отклоняется целиком.
        assert r.status == "error"
        assert "not allowed" in r.output.lower()

    def test_path_mismatch_in_patch_item_skipped(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x = 1\n")
        patches = [
            {"path": "different.py", "find": "x = 1", "replace": "x = 2"},
            {"find": "x = 1", "replace": "x = 9"},
        ]
        r = patch_file(_call(path="a.py", patches=patches))
        # patches-аргумент отклоняется целиком; файл не меняется.
        assert r.status == "error"
        assert (tmp_workdir / "a.py").read_text() == "x = 1\n"


class TestInsert:
    def test_insert_after_line(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("line1\nline2\nline3\n")
        r = patch_file(_call(path="a.py", line=1, insert="new_line"))
        assert r.status == "ok"
        content = (tmp_workdir / "a.py").read_text()
        lines = content.split("\n")
        assert lines[1] == "new_line"

    def test_invalid_line_value(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py", line="abc", insert="y"))
        assert r.status == "error"

    def test_line_out_of_range(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py", line=999, insert="y"))
        assert r.status == "error"
        assert "out of range" in r.output.lower()


class TestDeleteLines:
    def test_range(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a\nb\nc\nd\ne\n")
        r = patch_file(_call(path="a.py", delete_lines="2-4"))
        assert r.status == "ok"
        content = (tmp_workdir / "a.py").read_text()
        # Удалили строки 2-4 (b, c, d)
        assert "b" not in content.split("\n")
        assert "c" not in content.split("\n")
        assert "a" in content
        assert "e" in content

    def test_single_line(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a\nb\nc\n")
        r = patch_file(_call(path="a.py", delete_lines="2"))
        assert r.status == "ok"
        content = (tmp_workdir / "a.py").read_text()
        assert "b" not in content.split("\n")

    def test_invalid_format(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a\n")
        r = patch_file(_call(path="a.py", delete_lines="abc"))
        assert r.status == "error"


class TestErrors:
    def test_no_path(self, tmp_workdir):
        r = patch_file(_call(find="x", replace="y"))
        assert r.status == "error"

    def test_file_not_found(self, tmp_workdir):
        r = patch_file(_call(path="missing.py", find="x", replace="y"))
        assert r.status == "error"
        assert "not found" in r.output.lower()

    def test_no_operation(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x\n")
        r = patch_file(_call(path="a.py"))
        assert r.status == "error"


class TestNoChanges:
    def test_same_content(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x = 1\n")
        r = patch_file(_call(path="a.py", find="x = 1", replace="x = 1"))
        assert r.status == "ok"
        assert "No changes" in r.output
