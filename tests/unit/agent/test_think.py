"""agent/think.py — парсинг think-блоков, рендер, ThinkLog."""

import pytest

import agent.think as think
from agent.think import (
    ThinkLog,
    ThoughtStep,
    _extract_partial_thought,
    _parse_one,
    has_think_blocks,
    parse_partial_thought,
    parse_think_blocks,
    strip_think_blocks,
)


@pytest.fixture(autouse=True)
def enable_think(monkeypatch):
    """think-функции работают только при think_enabled=True. Принудительно включаем
    и сбрасываем кэш _THINK_CACHE между тестами."""
    monkeypatch.setattr(think, "_think_enabled", lambda: True)
    think._THINK_CACHE = None
    yield
    think._THINK_CACHE = None

def _block(body: str) -> str:
    return f":::call think\n{body}\ncall:::\n"

class TestParseThinkBlocks:
    def test_json_thought(self):
        text = _block('{"thought": "step one"}')
        assert parse_think_blocks(text) == ["step one"]

    def test_json_text_key(self):
        text = _block('{"text": "via text key"}')
        assert parse_think_blocks(text) == ["via text key"]

    def test_json_content_key(self):
        text = _block('{"content": "via content"}')
        assert parse_think_blocks(text) == ["via content"]

    def test_plain_body_not_json(self):
        text = _block("just plain reasoning")
        assert parse_think_blocks(text) == ["just plain reasoning"]

    def test_multiple_blocks(self):
        text = _block('{"thought": "a"}') + "filler\n" + _block('{"thought": "b"}')
        assert parse_think_blocks(text) == ["a", "b"]

    def test_single_quotes_fixed(self):
        text = _block("{'thought': 'fixme'}")
        assert parse_think_blocks(text) == ["fixme"]

    def test_trailing_comma_fixed(self):
        text = _block('{"thought": "trail",}')
        assert parse_think_blocks(text) == ["trail"]

    def test_whitespace_thought_falls_back_to_raw_body(self):
        # JSON с пустым thought не проходит strip-проверку в _parse_one и
        # возвращается как сырое тело блока.
        text = _block('{"thought": "   "}')
        assert parse_think_blocks(text) == ['{"thought": "   "}']

    def test_empty_text_returns_empty_list(self):
        assert parse_think_blocks("") == []

    def test_disabled_returns_empty(self, monkeypatch):
        monkeypatch.setattr(think, "_think_enabled", lambda: False)
        text = _block('{"thought": "x"}')
        assert parse_think_blocks(text) == []

class TestStripThinkBlocks:
    def test_removes_block(self):
        text = "before\n" + _block('{"thought": "x"}') + "after"
        result = strip_think_blocks(text)
        assert ":::call think" not in result
        assert "call:::" not in result
        assert "before" in result
        assert "after" in result

    def test_no_block_unchanged(self):
        text = "no think here"
        assert strip_think_blocks(text) == text

    def test_empty_input(self):
        assert strip_think_blocks("") == ""
        assert strip_think_blocks(None) == ""

    def test_disabled_keeps_block(self, monkeypatch):
        monkeypatch.setattr(think, "_think_enabled", lambda: False)
        text = _block('{"thought": "x"}')
        assert strip_think_blocks(text) == text

class TestHasThinkBlocks:
    def test_true_when_present(self):
        assert has_think_blocks(_block('{"thought": "x"}')) is True

    def test_false_when_absent(self):
        assert has_think_blocks("plain text") is False

    def test_false_on_empty(self):
        assert has_think_blocks("") is False

    def test_false_when_disabled(self, monkeypatch):
        monkeypatch.setattr(think, "_think_enabled", lambda: False)
        assert has_think_blocks(_block('{"thought": "x"}')) is False

class TestParsePartialThought:
    def test_unclosed_json_block(self):
        text = ':::call think\n{"thought": "half wri'
        assert parse_partial_thought(text) == "half wri"

    def test_unclosed_plain_body(self):
        text = ":::call think\nstreaming plain"
        assert parse_partial_thought(text) == "streaming plain"

    def test_key_not_yet_arrived(self):
        text = ':::call think\n{"th'
        assert parse_partial_thought(text) is None

    def test_no_open_block(self):
        assert parse_partial_thought("just text") is None

    def test_empty_text(self):
        assert parse_partial_thought("") is None

    def test_after_closed_block_returns_partial_of_open(self):
        text = _block('{"thought": "done"}') + ':::call think\n{"thought": "ongo'
        assert parse_partial_thought(text) == "ongo"

    def test_closed_block_only_returns_none(self):
        text = _block('{"thought": "done"}')
        assert parse_partial_thought(text) is None

    def test_disabled_returns_none(self, monkeypatch):
        monkeypatch.setattr(think, "_think_enabled", lambda: False)
        text = ':::call think\n{"thought": "x"}'
        assert parse_partial_thought(text) is None

class TestExtractPartialThought:
    def test_plain_body(self):
        assert _extract_partial_thought("raw thought") == "raw thought"

    def test_empty_body(self):
        assert _extract_partial_thought("") is None
        assert _extract_partial_thought(None) is None

    def test_escape_newline(self):
        assert _extract_partial_thought('{"thought": "a\\nb"}') == "a\nb"

    def test_unicode_escape(self):
        assert _extract_partial_thought('{"thought": "\\u0041BC"}') == "ABC"

    def test_stops_at_closing_quote(self):
        assert _extract_partial_thought('{"thought": "abc", "x": 1}') == "abc"

    def test_no_key_returns_none(self):
        assert _extract_partial_thought('{"other": "v"}') is None

class TestParseOne:
    def test_json(self):
        assert _parse_one('{"thought": "t"}') == "t"

    def test_plain(self):
        assert _parse_one("plain") == "plain"

    def test_empty(self):
        assert _parse_one("") is None
        assert _parse_one("   ") is None

class TestThinkLog:
    def test_add_and_total(self):
        log = ThinkLog()
        log.add("first")
        log.add("second")
        assert log.total == 2

    def test_add_strips_and_skips_empty(self):
        log = ThinkLog()
        log.add("  trimmed  ")
        log.add("")
        log.add("   ")
        assert log.total == 1
        assert log.steps[0].text == "trimmed"

    def test_current_is_last(self):
        log = ThinkLog()
        assert log.current is None
        log.add("a")
        log.add("b")
        assert log.current.text == "b"

    def test_step_dataclass(self):
        step = ThoughtStep(text="hi")
        assert step.text == "hi"
        assert isinstance(step.created_at, float)

class TestRenderLine:
    def test_empty_log_no_partial(self):
        log = ThinkLog()
        assert str(log.render_line()) == ""

    def test_shows_current_text(self):
        log = ThinkLog()
        log.add("the thought")
        line = log.render_line()
        assert "the thought" in line.plain
        assert "1" in line.plain

    def test_partial_overrides_and_increments_counter(self):
        log = ThinkLog()
        log.add("done one")
        line = log.render_line(partial="being typed")
        assert "being typed" in line.plain
        assert "2" in line.plain

    def test_long_snippet_truncated(self):
        log = ThinkLog()
        log.add("x" * 500)
        line = log.render_line()
        assert len(line.plain) < 400

    def test_streaming_adds_cursor(self):
        log = ThinkLog()
        line = log.render_line(partial="typing", streaming=True)
        assert "typing" in line.plain
