"""tools/file_ops/write.py — create_file (create-or-overwrite)."""

import base64

from tools.file_ops.write import _check_unbalanced_fences, create_file
from tools.models import ToolCall


def _call(tool: str, **args) -> ToolCall:
    return ToolCall(command=tool, tool_name=tool, args=args)


class TestCreateFile:
    def test_creates_new(self, tmp_workdir):
        r = create_file(_call("create_file", path="a.py", content="print(1)"))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "print(1)"
        assert "created" in r.output.lower()

    def test_overwrites_existing(self, tmp_workdir):
        # write_file удалён — create_file теперь создаёт ИЛИ перезаписывает.
        (tmp_workdir / "a.py").write_text("old")
        r = create_file(_call("create_file", path="a.py", content="new"))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "new"
        assert "overwrit" in r.output.lower()

    def test_overwrites_existing_file_without_extension(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("old")

        r = create_file(_call("create_file", path="a", content="new"))

        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "new"
        assert not (tmp_workdir / "a").exists()

    def test_creates_parent_dirs(self, tmp_workdir):
        r = create_file(_call("create_file", path="sub/dir/a.py", content="x"))
        assert r.status == "ok"
        assert (tmp_workdir / "sub" / "dir" / "a.py").read_text() == "x"

    def test_no_path_error(self, tmp_workdir):
        r = create_file(_call("create_file", content="x"))
        assert r.status == "error"
        assert "path" in r.output.lower()

    def test_empty_content(self, tmp_workdir):
        r = create_file(_call("create_file", path="a.py", content=""))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == ""

    def test_default_empty_content(self, tmp_workdir):
        r = create_file(_call("create_file", path="empty.txt"))
        assert r.status == "ok"
        assert (tmp_workdir / "empty.txt").read_text() == ""

    def test_b64_content(self, tmp_workdir):
        encoded = base64.b64encode(b"hello").decode()
        r = create_file(_call("create_file", path="a.txt", b64=encoded))
        assert r.status == "ok"
        assert (tmp_workdir / "a.txt").read_text() == "hello"

    def test_b64_invalid(self, tmp_workdir):
        r = create_file(_call("create_file", path="a.txt", b64="not valid base64 !!!"))
        assert r.status == "error"
        assert "base64" in r.output.lower()

    def test_non_string_content_coerced(self, tmp_workdir):
        r = create_file(_call("create_file", path="a.py", content=42))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == "42"

    def test_none_content_treated_as_empty(self, tmp_workdir):
        r = create_file(_call("create_file", path="a.py", content=None))
        assert r.status == "ok"
        assert (tmp_workdir / "a.py").read_text() == ""


class TestCheckUnbalancedFences:
    def test_no_marker_ok(self):
        # Тройные backticks внутри content — нормально, маркер ~`~call не
        # встречается в реальном коде, поэтому он не нужен для защиты.
        content = "```py\ncode\n```\n"
        assert _check_unbalanced_fences(content) == ""

    def test_marker_in_content_warns(self):
        # Если в content затесался :::call/call::: — парсер словил кусок чужого блока.
        content = "some code\n:::call create_file path=\"x\"\nstuff"
        result = _check_unbalanced_fences(content)
        assert ":::call" in result or "Suspicious" in result

    def test_empty(self):
        assert _check_unbalanced_fences("") == ""

    def test_no_fences(self):
        assert _check_unbalanced_fences("plain text\nno fences") == ""

    def test_triple_backticks_alone_ok(self):
        content = "```\na\n```\n```\nb\n```\n"
        assert _check_unbalanced_fences(content) == ""
