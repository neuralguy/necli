"""ui/file_context.py — парсинг @-ссылок и сборка контекста файлов/папок."""

from pathlib import Path

from ui.file_context import (
    FileReference,
    _build_tree,
    _collect_dir_files,
    _read_file_content,
    expand_at_references,
    parse_at_references,
)

class TestReadFileContent:
    def test_normal_file(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello world")
        content, truncated = _read_file_content(f)
        assert content == "hello world"
        assert truncated is False

    def test_truncation(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 200)
        content, truncated = _read_file_content(f, max_size=50)
        assert truncated is True
        assert content.startswith("x" * 50)
        assert "truncated" in content
        assert "200 bytes total" in content

    def test_missing_file(self, tmp_path):
        f = tmp_path / "nope.txt"
        content, truncated = _read_file_content(f)
        assert content is None
        assert truncated is False

class TestCollectDirFiles:
    def test_collects_files(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("c")
        files = _collect_dir_files(tmp_path)
        rels = {rel for rel, _ in files}
        assert "a.py" in rels
        assert "b.py" in rels
        assert str(Path("sub") / "c.py") in rels

    def test_ignores_hidden_and_binary(self, tmp_path):
        (tmp_path / "keep.txt").write_text("k")
        (tmp_path / ".secret").write_text("s")
        (tmp_path / "img.png").write_text("p")
        files = _collect_dir_files(tmp_path)
        rels = {rel for rel, _ in files}
        assert "keep.txt" in rels
        assert ".secret" not in rels
        assert "img.png" not in rels

    def test_max_files_cap(self, tmp_path):
        for i in range(10):
            (tmp_path / f"f{i}.txt").write_text("x")
        files = _collect_dir_files(tmp_path, max_files=3)
        assert len(files) == 3

    def test_missing_dir_returns_empty(self, tmp_path):
        assert _collect_dir_files(tmp_path / "nope") == []

class TestBuildTree:
    def test_basic_tree(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "b.py").write_text("b")
        tree = _build_tree(tmp_path)
        assert "a.py" in tree
        assert "sub/" in tree
        assert "b.py" in tree
        assert "├──" in tree or "└──" in tree

    def test_ignores_hidden(self, tmp_path):
        (tmp_path / "visible.py").write_text("x")
        (tmp_path / ".hidden").write_text("x")
        tree = _build_tree(tmp_path)
        assert "visible.py" in tree
        assert ".hidden" not in tree

    def test_empty_dir(self, tmp_path):
        assert _build_tree(tmp_path) == ""

class TestParseAtReferences:
    def test_simple_file(self, tmp_path):
        f = tmp_path / "main.py"
        f.write_text("code")
        refs = parse_at_references("see @main.py here", str(tmp_path))
        assert len(refs) == 1
        assert refs[0].path_str == "main.py"
        assert refs[0].is_dir is False
        assert refs[0].error is None

    def test_directory_ref(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        refs = parse_at_references("look @src here", str(tmp_path))
        assert len(refs) == 1
        assert refs[0].is_dir is True

    def test_all_keyword(self, tmp_path):
        refs = parse_at_references("@all please", str(tmp_path))
        assert len(refs) == 1
        assert refs[0].raw == "@all"
        assert refs[0].path_str == "."
        assert refs[0].is_dir is True
        assert refs[0].resolved_path == Path(tmp_path).resolve()

    def test_not_found(self, tmp_path):
        refs = parse_at_references("@ghost.py", str(tmp_path))
        assert len(refs) == 1
        assert refs[0].error is not None
        assert "not found" in refs[0].error

    def test_dedup_same_path(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("x")
        refs = parse_at_references("@dup.py and @dup.py", str(tmp_path))
        assert len(refs) == 1

    def test_no_references(self, tmp_path):
        assert parse_at_references("nothing here", str(tmp_path)) == []

    def test_email_not_matched(self, tmp_path):
        # @ preceded by non-whitespace (e.g. email) is not a reference
        f = tmp_path / "foo"
        f.write_text("x")
        refs = parse_at_references("user@foo", str(tmp_path))
        assert refs == []

    def test_skips_reserved_words(self, tmp_path):
        refs = parse_at_references("@tool @plan @param", str(tmp_path))
        assert refs == []

    def test_trailing_slash_dir(self, tmp_path):
        sub = tmp_path / "pkg"
        sub.mkdir()
        refs = parse_at_references("@pkg/ done", str(tmp_path))
        assert len(refs) == 1
        assert refs[0].is_dir is True

    def test_multiple_distinct_refs(self, tmp_path):
        (tmp_path / "a.py").write_text("a")
        (tmp_path / "b.py").write_text("b")
        refs = parse_at_references("@a.py and @b.py", str(tmp_path))
        paths = {r.path_str for r in refs}
        assert paths == {"a.py", "b.py"}

class TestExpandAtReferences:
    def test_no_refs_returns_original(self, tmp_path):
        text = "just text"
        expanded, ctx, refs = expand_at_references(text, str(tmp_path))
        assert expanded == text
        assert ctx == ""
        assert refs == []

    def test_single_file_context(self, tmp_path):
        f = tmp_path / "hello.py"
        f.write_text("print('hi')")
        text, ctx, refs = expand_at_references("@hello.py", str(tmp_path))
        assert "print('hi')" in ctx
        assert "--- @hello.py ---" in ctx
        assert "--- end @hello.py ---" in ctx
        assert len(refs) == 1
        assert refs[0].content is not None

    def test_directory_context(self, tmp_path):
        sub = tmp_path / "src"
        sub.mkdir()
        (sub / "x.py").write_text("X")
        text, ctx, refs = expand_at_references("@src", str(tmp_path))
        assert "(directory)" in ctx
        assert "Tree:" in ctx
        assert "X" in ctx
        assert "--- end @src ---" in ctx

    def test_error_ref_skipped(self, tmp_path):
        text, ctx, refs = expand_at_references("@missing.py", str(tmp_path))
        assert ctx == ""
        assert len(refs) == 1
        assert refs[0].error is not None

    def test_mixed_valid_and_invalid(self, tmp_path):
        (tmp_path / "real.py").write_text("real")
        text, ctx, refs = expand_at_references("@real.py @missing.py", str(tmp_path))
        assert "real" in ctx
        assert len(refs) == 2
        errors = [r for r in refs if r.error]
        assert len(errors) == 1

class TestFileReference:
    def test_init_defaults(self):
        ref = FileReference("@x", "x", Path("/tmp/x"))
        assert ref.raw == "@x"
        assert ref.path_str == "x"
        assert ref.is_dir is False
        assert ref.content is None
        assert ref.error is None