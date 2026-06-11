"""tools/file_ops/read.py — read_files и кэш чтений."""

from tools.file_ops.read import (
    read_files,
    _merge_ranges,
    _range_covered,
    _parse_lines_range,
    invalidate_read_cache,
    clear_read_cache,
    _READ_CACHE,
)
from tools.models import ToolCall


def _call(**args) -> ToolCall:
    return ToolCall(command="read_files", tool_name="read_files", args=args)


class TestMergeRanges:
    def test_empty(self):
        assert _merge_ranges([]) == []

    def test_single(self):
        assert _merge_ranges([(1, 10)]) == [(1, 10)]

    def test_overlapping(self):
        assert _merge_ranges([(1, 5), (4, 10)]) == [(1, 10)]

    def test_adjacent(self):
        assert _merge_ranges([(1, 5), (6, 10)]) == [(1, 10)]

    def test_disjoint(self):
        assert _merge_ranges([(1, 5), (10, 15)]) == [(1, 5), (10, 15)]

    def test_unsorted(self):
        assert _merge_ranges([(10, 20), (1, 5)]) == [(1, 5), (10, 20)]


class TestRangeCovered:
    def test_covered(self):
        assert _range_covered([(1, 100)], 10, 50) is True

    def test_not_covered(self):
        assert _range_covered([(1, 10)], 5, 20) is False

    def test_exact(self):
        assert _range_covered([(1, 10)], 1, 10) is True

    def test_disjoint(self):
        assert _range_covered([(1, 5), (20, 30)], 10, 15) is False


class TestParseLinesRange:
    def test_single(self):
        assert _parse_lines_range("5", 100) == (5, 5)

    def test_range(self):
        assert _parse_lines_range("10-20", 100) == (10, 20)

    def test_clamps_end(self):
        assert _parse_lines_range("10-200", 100) == (10, 100)

    def test_start_clamped_min_1(self):
        assert _parse_lines_range("0", 100) == (1, 1)

    def test_invalid(self):
        assert _parse_lines_range("abc", 100) is None

    def test_empty(self):
        assert _parse_lines_range("", 100) is None

    def test_inverted_returns_none(self):
        assert _parse_lines_range("50-10", 100) is None


class TestReadFilesBasic:
    def test_simple_file(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("print(1)\n")
        r = read_files(_call(path="a.py"))
        assert r.status == "ok"
        assert "print(1)" in r.output
        assert r.full_content is True

    def test_missing_file(self, tmp_workdir):
        r = read_files(_call(path="missing.py"))
        assert r.status == "error"

    def test_directory_rejected(self, tmp_workdir):
        (tmp_workdir / "sub").mkdir()
        r = read_files(_call(path="sub"))
        assert r.status == "error"
        assert "not a file" in r.output.lower()

    def test_no_path(self, tmp_workdir):
        r = read_files(_call())
        assert r.status == "error"

    def test_truncate_over_1000_lines(self, tmp_workdir):
        big = "\n".join(f"line{i}" for i in range(2000))
        (tmp_workdir / "big.py").write_text(big)
        r = read_files(_call(path="big.py"))
        assert r.status == "ok"
        assert "truncat" in r.output.lower()
        assert r.full_content is False

    def test_lines_range(self, tmp_workdir):
        content = "\n".join(f"l{i}" for i in range(20))
        (tmp_workdir / "a.py").write_text(content)
        r = read_files(_call(path="a.py", lines="5-7"))
        assert r.status == "ok"
        assert "l4" in r.output  # 1-based: строка 5 это индекс 4
        assert "l5" in r.output
        assert "l6" in r.output

    def test_lines_single(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a\nb\nc\n")
        r = read_files(_call(path="a.py", lines="2"))
        assert r.status == "ok"
        assert "b" in r.output

    def test_lines_out_of_range(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("a\nb\n")
        r = read_files(_call(path="a.py", lines="100-200"))
        # за пределами → возвращает весь файл с предупреждением
        assert r.status == "ok"
        assert "a" in r.output


class TestMultiplePaths:
    def test_paths_list_strings(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("AAA")
        (tmp_workdir / "b.py").write_text("BBB")
        r = read_files(_call(paths=["a.py", "b.py"]))
        assert r.status == "ok"
        assert "AAA" in r.output
        assert "BBB" in r.output

    def test_paths_list_dicts(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("\n".join(f"l{i}" for i in range(10)))
        (tmp_workdir / "b.py").write_text("XYZ")
        r = read_files(_call(paths=[
            {"path": "a.py", "lines": "2-3"},
            {"path": "b.py"},
        ]))
        assert r.status == "ok"
        assert "XYZ" in r.output

    def test_paths_max_20(self, tmp_workdir):
        for i in range(25):
            (tmp_workdir / f"f{i}.py").write_text(f"data{i}")
        paths = [f"f{i}.py" for i in range(25)]
        r = read_files(_call(paths=paths))
        # MAX_READ_FILES = 20 — лишние срезаются
        assert r.status == "ok"


class TestReadCache:
    def test_second_read_returns_not_changed(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("hello")
        r1 = read_files(_call(path="a.py"))
        assert r1.status == "ok"
        assert "hello" in r1.output

        r2 = read_files(_call(path="a.py"))
        assert r2.status == "ok"
        # Повторное чтение неизменённого файла → короткий маркер NOT CHANGED.
        assert "NOT CHANGED" in r2.output

    def test_modification_invalidates(self, tmp_workdir):
        import time
        f = tmp_workdir / "a.py"
        f.write_text("first")
        read_files(_call(path="a.py"))
        time.sleep(0.02)
        f.write_text("second very different content here")
        r = read_files(_call(path="a.py"))
        assert "NOT CHANGED" not in r.output
        assert "second" in r.output

    def test_invalidate_cache_explicit(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("hi")
        read_files(_call(path="a.py"))
        invalidate_read_cache(f)
        r = read_files(_call(path="a.py"))
        assert "NOT CHANGED" not in r.output

    def test_clear_all_sessions(self, tmp_workdir):
        f = tmp_workdir / "a.py"
        f.write_text("hi")
        read_files(_call(path="a.py"))
        cleared = clear_read_cache("*")
        assert cleared >= 1
        assert _READ_CACHE == {}

    def test_partial_range_covered(self, tmp_workdir):
        content = "\n".join(f"l{i}" for i in range(50))
        (tmp_workdir / "a.py").write_text(content)
        # Первое чтение — полное (1..50)
        read_files(_call(path="a.py"))
        # Тот же диапазон уже виден → NOT CHANGED, контент не пересылается.
        r = read_files(_call(path="a.py"))
        assert "NOT CHANGED" in r.output