"""tools/file_ops/grep.py -- safe project search."""

from tools.file_ops.grep import execute_grep
from tools.models import ToolCall


def _call(**args):
    return ToolCall(command="grep", tool_name="grep", args=args)


class TestGrep:
    def test_searches_contents_with_line_numbers(self, tmp_path, monkeypatch):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("one\nNeedle here\n", encoding="utf-8")
        monkeypatch.setattr("tools.file_ops.grep.resolve_path", lambda _: tmp_path)

        result = execute_grep(_call(pattern="needle"))

        assert result.status == "ok"
        assert "src/app.py:2:Needle here" in result.output

    def test_searches_a_single_file(self, tmp_path, monkeypatch):
        target = tmp_path / "src" / "app.py"
        target.parent.mkdir()
        target.write_text("one\nNeedle here\n", encoding="utf-8")
        (tmp_path / "other.py").write_text("needle", encoding="utf-8")
        monkeypatch.setattr("tools.file_ops.grep.resolve_path", lambda _: target)

        result = execute_grep(_call(path="src/app.py", pattern="needle"))

        assert result.status == "ok"
        assert "app.py:2:Needle here" in result.output
        assert "other.py" not in result.output

    def test_searches_existing_file_without_extension(self, tmp_workdir):
        (tmp_workdir / "app.py").write_text("Needle here\n", encoding="utf-8")

        result = execute_grep(_call(path="app", pattern="needle"))

        assert result.status == "ok"
        assert "app.py:1:Needle here" in result.output

    def test_lists_files_by_include_glob(self, tmp_path, monkeypatch):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("", encoding="utf-8")
        (tmp_path / "README.md").write_text("", encoding="utf-8")
        monkeypatch.setattr("tools.file_ops.grep.resolve_path", lambda _: tmp_path)

        result = execute_grep(_call(include="*.py"))

        assert result.status == "ok"
        assert "src/app.py" in result.output
        assert "README.md" not in result.output

    def test_skips_hidden_and_ignored_directories(self, tmp_path, monkeypatch):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "app.py").write_text("needle", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("needle", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "pkg.js").write_text("needle", encoding="utf-8")
        monkeypatch.setattr("tools.file_ops.grep.resolve_path", lambda _: tmp_path)

        result = execute_grep(_call(pattern="needle"))

        assert "src/app.py" in result.output
        assert ".git" not in result.output
        assert "node_modules" not in result.output

    def test_requires_pattern_or_include(self, tmp_path, monkeypatch):
        monkeypatch.setattr("tools.file_ops.grep.resolve_path", lambda _: tmp_path)

        result = execute_grep(_call())

        assert result.status == "error"
        assert "Provide pattern" in result.output
