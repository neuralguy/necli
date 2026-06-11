"""agent/diff_render.py — поиск стартовой строки find_text в файле и файловый кэш."""

import agent.diff_render as dr
from agent.diff_render import (
    _locate_find_in_file,
    _read_file_cached,
    _LOCATE_CACHE,
)

def setup_function(_):
    _LOCATE_CACHE.clear()

class TestLocateFindInFile:
    def test_empty_args_return_one(self):
        assert _locate_find_in_file("", "x") == 1
        assert _locate_find_in_file("a.py", "") == 1
        assert _locate_find_in_file("", "") == 1

    def test_missing_file_returns_one(self, tmp_workdir):
        assert _locate_find_in_file("nope.py", "anything") == 1

    def test_finds_first_line(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("line0\nline1\ntarget\nline3\n", encoding="utf-8")
        assert _locate_find_in_file("a.py", "target") == 3

    def test_first_line_is_one(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("first\nsecond\n", encoding="utf-8")
        assert _locate_find_in_file("a.py", "first") == 1

    def test_multiline_find_text(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("aaa\nbbb\ndef foo():\n    pass\n", encoding="utf-8")
        assert _locate_find_in_file("a.py", "def foo():\n    pass") == 3

    def test_not_found_returns_one(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("alpha\nbeta\n", encoding="utf-8")
        assert _locate_find_in_file("a.py", "gamma") == 1

    def test_fallback_to_first_nonblank_line(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("x\ny\nMARKER here\nz\n", encoding="utf-8")
        # exact multiline block not present; first nonblank line "MARKER here" matches at line 3
        find_text = "MARKER here\nDIFFERENT TAIL THAT IS ABSENT"
        assert _locate_find_in_file("a.py", find_text) == 3

class TestReadFileCached:
    def test_reads_content(self, tmp_workdir):
        f = tmp_workdir / "b.py"
        f.write_text("hello\nworld\n", encoding="utf-8")
        assert _read_file_cached(str(f)) == "hello\nworld\n"

    def test_missing_file_returns_none(self, tmp_workdir):
        assert _read_file_cached(str(tmp_workdir / "ghost.py")) is None

    def test_cache_hit_returns_cached(self, tmp_workdir):
        f = tmp_workdir / "c.py"
        f.write_text("v1\n", encoding="utf-8")
        first = _read_file_cached(str(f))
        assert first == "v1\n"
        assert str(f) in _LOCATE_CACHE
        # serve from cache without re-stat mismatch
        assert _read_file_cached(str(f)) == "v1\n"

    def test_cache_invalidated_on_size_change(self, tmp_workdir):
        f = tmp_workdir / "d.py"
        f.write_text("short\n", encoding="utf-8")
        assert _read_file_cached(str(f)) == "short\n"
        f.write_text("a much longer content now\n", encoding="utf-8")
        assert _read_file_cached(str(f)) == "a much longer content now\n"

    def test_cache_eviction_at_max(self, tmp_workdir):
        _LOCATE_CACHE.clear()
        orig_max = dr._LOCATE_CACHE_MAX
        dr._LOCATE_CACHE_MAX = 2
        try:
            for name in ("f1", "f2", "f3"):
                p = tmp_workdir / name
                p.write_text(name, encoding="utf-8")
                _read_file_cached(str(p))
            assert len(_LOCATE_CACHE) <= 2
        finally:
            dr._LOCATE_CACHE_MAX = orig_max