"""agent/project_stats.py — подсчёт файлов/строк и трекинг изменений за шаг."""

from agent.project_stats import (
    StepTracker,
    build_stats_line,
    count_project_stats,
    format_project_stats,
)


class TestCountProjectStats:
    def test_nonexistent_dir(self, tmp_path):
        assert count_project_stats(str(tmp_path / "nope")) == (0, 0)

    def test_path_is_file(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("x\n")
        assert count_project_stats(str(f)) == (0, 0)

    def test_empty_dir(self, tmp_path):
        assert count_project_stats(str(tmp_path)) == (0, 0)

    def test_single_code_file(self, tmp_path):
        (tmp_path / "a.py").write_text("line1\nline2\nline3\n")
        assert count_project_stats(str(tmp_path)) == (1, 3)

    def test_file_without_trailing_newline(self, tmp_path):
        (tmp_path / "a.py").write_text("line1\nline2")
        assert count_project_stats(str(tmp_path)) == (1, 2)

    def test_empty_file_counts_zero_lines(self, tmp_path):
        (tmp_path / "a.py").write_text("")
        files, lines = count_project_stats(str(tmp_path))
        assert files == 1
        assert lines == 0

    def test_multiple_files_summed(self, tmp_path):
        (tmp_path / "a.py").write_text("1\n2\n")
        (tmp_path / "b.js").write_text("a\nb\nc\n")
        files, lines = count_project_stats(str(tmp_path))
        assert files == 2
        assert lines == 5

    def test_unknown_extension_skipped(self, tmp_path):
        (tmp_path / "a.py").write_text("x\n")
        (tmp_path / "image.bin").write_text("garbage\n")
        assert count_project_stats(str(tmp_path)) == (1, 1)

    def test_extensionless_known_names_counted(self, tmp_path):
        (tmp_path / "Makefile").write_text("all:\n\techo hi\n")
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        files, lines = count_project_stats(str(tmp_path))
        assert files == 2
        assert lines == 3

    def test_ignored_dir_excluded(self, tmp_path):
        (tmp_path / "keep.py").write_text("x\n")
        nested = tmp_path / "__pycache__"
        nested.mkdir()
        (nested / "skip.py").write_text("y\ny\ny\n")
        files, lines = count_project_stats(str(tmp_path))
        assert files == 1
        assert lines == 1

    def test_nested_subdirs_walked(self, tmp_path):
        sub = tmp_path / "src" / "deep"
        sub.mkdir(parents=True)
        (sub / "x.py").write_text("a\nb\n")
        (tmp_path / "top.py").write_text("c\n")
        files, lines = count_project_stats(str(tmp_path))
        assert files == 2
        assert lines == 3

class TestFormatProjectStats:
    def test_basic(self):
        assert format_project_stats(12, 6340) == "Project: 12 files, 6,340 lines"

    def test_zero(self):
        assert format_project_stats(0, 0) == "Project: 0 files, 0 lines"

    def test_large_number_grouped(self):
        assert format_project_stats(1, 1234567) == "Project: 1 files, 1,234,567 lines"

class TestStepTrackerRecord:
    def test_create_file_records_path(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        assert "a.py" in t.files_changed

    def test_non_file_tool_ignored(self):
        t = StepTracker()
        t.record("shell", "some output", {"command": "ls"})
        assert t.files_changed == set()
        assert not t.has_changes

    def test_patch_file_counts_added_and_removed(self):
        t = StepTracker()
        output = "✓ a.py updated (3 changed, +42 added, -17 removed)\n+ 99  code\n"
        t.record("patch_file", output, {"path": "a.py"})
        assert t.lines_added == 42
        assert t.lines_removed == 17

    def test_patch_file_added_only(self):
        t = StepTracker()
        t.record("patch_file", "✓ a.py updated (+5 added)", {"path": "a.py"})
        assert t.lines_added == 5
        assert t.lines_removed == 0

    def test_patch_file_removed_only(self):
        t = StepTracker()
        t.record("patch_file", "✓ a.py updated (-8 removed)", {"path": "a.py"})
        assert t.lines_added == 0
        assert t.lines_removed == 8

    def test_patch_file_changed_only_no_delta(self):
        t = StepTracker()
        t.record("patch_file", "✓ a.py updated (3 changed)", {"path": "a.py"})
        assert t.lines_added == 0
        assert t.lines_removed == 0

    def test_patch_file_single_line_singular(self):
        t = StepTracker()
        t.record("patch_file", "✓ a.py updated (+1 added, -1 removed)", {"path": "a.py"})
        assert t.lines_added == 1
        assert t.lines_removed == 1

    def test_create_file_parses_lines(self):
        t = StepTracker()
        t.record("create_file", "✓ Created: a.py (7 lines)", {"path": "a.py"})
        assert t.lines_added == 7

    def test_create_file_single_line_singular(self):
        t = StepTracker()
        t.record("create_file", "✓ Created: a.py (1 line)", {"path": "a.py"})
        assert t.lines_added == 1

    def test_create_file_overwrite_no_line_delta(self):
        t = StepTracker()
        t.record("create_file", "✓ Overwritten: a.py (5 lines)", {"path": "a.py"})
        assert "a.py" in t.files_changed
        assert t.lines_added == 0

class TestStepTrackerState:
    def test_has_changes_false_when_empty(self):
        assert StepTracker().has_changes is False

    def test_has_changes_true_with_file(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        assert t.has_changes is True

    def test_has_changes_true_with_added_lines(self):
        t = StepTracker()
        t.lines_added = 3
        assert t.has_changes is True

    def test_reset_clears_everything(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        t.lines_added = 5
        t.lines_removed = 2
        t.reset()
        assert t.files_changed == set()
        assert t.lines_added == 0
        assert t.lines_removed == 0
        assert t.has_changes is False

class TestFormatStepStats:
    def test_empty_returns_empty_string(self):
        assert StepTracker().format_step_stats() == ""

    def test_single_file_singular(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        assert t.format_step_stats() == "1 file changed"

    def test_multiple_files_plural(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        t.record("create_file", "", {"path": "b.py"})
        assert t.format_step_stats() == "2 files changed"

    def test_with_added_and_removed(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        t.lines_added = 380
        t.lines_removed = 15
        assert t.format_step_stats() == "1 file changed, +380 -15"

    def test_only_added(self):
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        t.lines_added = 10
        assert t.format_step_stats() == "1 file changed, +10"

class TestBuildStatsLine:
    def test_no_step_changes(self, tmp_path):
        (tmp_path / "a.py").write_text("x\n")
        line = build_stats_line(str(tmp_path), StepTracker())
        assert line == "Project: 1 files, 1 lines"

    def test_with_step_changes(self, tmp_path):
        (tmp_path / "a.py").write_text("x\n")
        t = StepTracker()
        t.record("create_file", "", {"path": "a.py"})
        t.lines_added = 5
        line = build_stats_line(str(tmp_path), t)
        assert line == "Project: 1 files, 1 lines | This step: 1 file changed, +5"
