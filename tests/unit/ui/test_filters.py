"""ui/_filters.py — should_ignore + ui/_emoji_width.py extra coverage."""

import pytest

from ui._emoji_width import _EMOJI_RANGES, _is_emoji_codepoint
from ui._filters import _IGNORE_SUFFIXES, should_ignore


class TestShouldIgnoreHidden:
    def test_hidden_file(self):
        assert should_ignore(".env", is_dir=False) is True

    def test_hidden_dir(self):
        assert should_ignore(".git", is_dir=True) is True

    def test_dotfile_even_if_known_suffix(self):
        # начинается с точки → игнор раньше проверки суффикса
        assert should_ignore(".hidden.py", is_dir=False) is True

class TestShouldIgnoreDirs:
    def test_node_modules(self):
        assert should_ignore("node_modules", is_dir=True) is True

    def test_pycache(self):
        assert should_ignore("__pycache__", is_dir=True) is True

    def test_venv(self):
        assert should_ignore("venv", is_dir=True) is True

    def test_normal_dir_not_ignored(self):
        assert should_ignore("src", is_dir=True) is False

    def test_ignored_name_as_file_not_dir(self):
        # node_modules как ФАЙЛ (is_dir=False) не попадает под dir-проверку
        assert should_ignore("node_modules", is_dir=False) is False

class TestShouldIgnoreSuffixes:
    @pytest.mark.parametrize("name", [
        "a.pyc", "b.so", "lib.dll", "app.exe", "x.class",
        "img.png", "pic.jpeg", "anim.gif", "arch.zip",
        "data.tar", "blob.gz", "font.woff2", "doc.pdf",
        "sheet.xlsx", "report.docx",
    ])
    def test_ignored_suffix(self, name):
        assert should_ignore(name, is_dir=False) is True

    @pytest.mark.parametrize("name", [
        "main.py", "readme.md", "data.json", "style.css",
        "index.html", "notes.txt", "Makefile",
    ])
    def test_kept_suffix(self, name):
        assert should_ignore(name, is_dir=False) is False

    def test_suffix_ignored_only_for_files_not_dirs(self):
        # директория с "суффиксным" именем не проверяется по суффиксам
        assert should_ignore("assets.zip", is_dir=True) is False

    def test_all_suffixes_start_with_dot(self):
        assert all(s.startswith(".") for s in _IGNORE_SUFFIXES)

    def test_uppercase_suffix_not_matched(self):
        # endswith чувствителен к регистру → .PNG не в наборе
        assert should_ignore("IMG.PNG", is_dir=False) is False

class TestEmojiCodepointExtra:
    @pytest.mark.parametrize("ch", ["⏺", "⭐", "🎯", "📝", "🔧", "✨", "❌", "➕"])
    def test_known_emoji(self, ch):
        assert _is_emoji_codepoint(ord(ch)) is True

    @pytest.mark.parametrize("ch", ["A", "Z", " ", "\t", "0", "9", "ё", "—", "·"])
    def test_non_emoji(self, ch):
        assert _is_emoji_codepoint(ord(ch)) is False

    def test_below_first_range(self):
        # кодпоинт ниже самого первого диапазона
        first_start = _EMOJI_RANGES[0][0]
        assert _is_emoji_codepoint(first_start - 1) is False

    def test_range_boundaries_inclusive(self):
        for start, end in _EMOJI_RANGES:
            assert _is_emoji_codepoint(start) is True
            assert _is_emoji_codepoint(end) is True

    def test_just_above_a_gap(self):
        # 0x203C — одиночный диапазон; 0x203D должен быть НЕ emoji
        assert _is_emoji_codepoint(0x203C) is True
        assert _is_emoji_codepoint(0x203D) is False

    def test_ranges_sorted_by_start(self):
        starts = [s for s, _ in _EMOJI_RANGES]
        assert starts == sorted(starts)
