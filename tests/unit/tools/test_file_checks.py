"""tools/file_checks.py — syntax-проверки Python/JS файлов."""

from tools.file_checks import (
    _check_js_syntax,
    _check_py_syntax,
    _run_ruff_on_python_file,
)


class TestPySyntax:
    def test_valid(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("x = 1\n")
        result = _check_py_syntax(f, "a.py")
        assert result == ""

    def test_syntax_error(self, tmp_workdir):
        f = tmp_workdir / "bad.py"
        f.write_text("def x(:\n")
        result = _check_py_syntax(f, "bad.py")
        assert "SyntaxError" in result

    def test_non_py_returns_empty(self, tmp_workdir):
        f = tmp_workdir / "a.txt"
        f.write_text("not python")
        assert _check_py_syntax(f, "a.txt") == ""


class TestJsSyntax:
    def test_returns_string(self, tmp_workdir):
        # node может быть или не быть — функция должна не падать
        f = tmp_workdir / "a.js"
        f.write_text("const x = 1;")
        result = _check_js_syntax(f, "a.js")
        # либо валидный (пусто), либо node нет (тоже пусто)
        assert isinstance(result, str)


class TestRunRuffWrapper:
    def test_py_valid(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("x = 1\n")
        result = _run_ruff_on_python_file(f, "a.py")
        assert result == ""

    def test_py_syntax_error_caught(self, tmp_workdir):
        f = tmp_workdir / "bad.py"
        f.write_text("def x(:\n")
        result = _run_ruff_on_python_file(f, "bad.py")
        assert "SyntaxError" in result

    def test_other_extension_returns_empty(self, tmp_workdir):
        f = tmp_workdir / "a.txt"
        f.write_text("text")
        assert _run_ruff_on_python_file(f, "a.txt") == ""
