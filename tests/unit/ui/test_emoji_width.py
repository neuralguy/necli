"""ui/_emoji_width.py — определение emoji-кодпоинтов."""

from ui._emoji_width import _is_emoji_codepoint


class TestIsEmojiCodepoint:
    def test_check_mark(self):
        assert _is_emoji_codepoint(ord("✓")) is True

    def test_cross(self):
        assert _is_emoji_codepoint(ord("✗")) is True

    def test_rocket(self):
        assert _is_emoji_codepoint(ord("🚀")) is True

    def test_smile(self):
        assert _is_emoji_codepoint(ord("😀")) is True

    def test_regular_letter(self):
        assert _is_emoji_codepoint(ord("a")) is False

    def test_cyrillic_letter(self):
        assert _is_emoji_codepoint(ord("я")) is False

    def test_digit(self):
        assert _is_emoji_codepoint(ord("5")) is False

    def test_box_drawing_not_emoji(self):
        # ├ — box drawing, не emoji
        assert _is_emoji_codepoint(ord("├")) is False