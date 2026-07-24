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

    def test_updates_existing_file_without_extension(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x = 1\n")

        r = patch_file(_call(path="a", find="x = 1", replace="x = 2"))

        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "x = 2\n"

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
