"""session/tokens.py — подсчёт токенов для разных моделей."""

from session.tokens import (
    count_tokens,
    estimate_tokens,
    _normalize_model_id,
    _resolve_model_name,
    _is_opus_47,
    _is_gemini_model,
    _count_gemini_heuristic,
    _fallback_estimate,
    _apply_multiplier,
)


class TestCountTokens:
    def test_empty(self):
        assert count_tokens("", "GPT-5.4") == 0
        assert count_tokens("", "") == 0

    def test_non_empty_returns_positive(self):
        assert count_tokens("hello world", "GPT-5.4") > 0

    def test_unknown_model_uses_o200k(self):
        result = count_tokens("hello world test", "totally-unknown-model-xyz")
        assert result > 0

    def test_gemini_uses_heuristic(self):
        result = count_tokens("hello", "gemini-3.1-pro")
        assert result >= 1

    def test_opus_47_multiplier_applied(self):
        text = "the quick brown fox jumps over the lazy dog " * 10
        base = count_tokens(text, "Claude Sonnet 4.6")
        opus = count_tokens(text, "Claude Opus 4.7")
        # Opus 4.7 имеет multiplier 1.2 → результат больше при том же tiktoken
        assert opus > base

    def test_opus_47_aliased_id(self):
        text = "hello world"
        a = count_tokens(text, "Claude Opus 4.7")
        b = count_tokens(text, "claude-opus-4-7")
        assert a == b


class TestEstimateTokens:
    def test_returns_int(self):
        assert isinstance(estimate_tokens("hello"), int)

    def test_empty_zero(self):
        assert estimate_tokens("") == 0


class TestNormalizeModelId:
    def test_lowercase_and_strip_separators(self):
        assert _normalize_model_id("Claude-Opus-4.7") == "claudeopus47"

    def test_underscores(self):
        assert _normalize_model_id("Claude_Opus_4_7") == "claudeopus47"

    def test_spaces(self):
        assert _normalize_model_id("Claude Opus 4.7") == "claudeopus47"

    def test_empty(self):
        assert _normalize_model_id("") == ""

    def test_none_safe(self):
        assert _normalize_model_id(None) == ""


class TestResolveModelName:
    def test_aliased_id_maps_to_display(self):
        assert _resolve_model_name("claude-opus-4-7") == "Claude Opus 4.7"

    def test_display_name_passthrough(self):
        assert _resolve_model_name("Claude Opus 4.6") == "Claude Opus 4.6"

    def test_unknown_passthrough(self):
        assert _resolve_model_name("strange") == "strange"

    def test_empty(self):
        assert _resolve_model_name("") == ""


class TestIsOpus47:
    def test_display_name(self):
        assert _is_opus_47("Claude Opus 4.7") is True

    def test_dashed(self):
        assert _is_opus_47("claude-opus-4-7") is True

    def test_opus_46_false(self):
        assert _is_opus_47("Claude Opus 4.6") is False

    def test_sonnet_false(self):
        assert _is_opus_47("Claude Sonnet 4.6") is False


class TestIsGeminiModel:
    def test_gemini_true(self):
        assert _is_gemini_model("gemini-3.1-pro") is True
        assert _is_gemini_model("Gemini-Flash") is True

    def test_other_false(self):
        assert _is_gemini_model("claude") is False
        assert _is_gemini_model("gpt-4") is False


class TestGeminiHeuristic:
    def test_empty(self):
        assert _count_gemini_heuristic("") == 0

    def test_ascii_positive(self):
        assert _count_gemini_heuristic("hello world") >= 1

    def test_cyrillic_more_tokens_per_char(self):
        ascii_count = _count_gemini_heuristic("a" * 100)
        cyrillic_count = _count_gemini_heuristic("я" * 100)
        # Кириллица — больше токенов на символ
        assert cyrillic_count > ascii_count

    def test_digits_counted(self):
        # Цифры — отдельные токены, должны давать больше чем ASCII буквы
        digits = _count_gemini_heuristic("1234567890")
        letters = _count_gemini_heuristic("abcdefghij")
        assert digits > letters


class TestFallbackEstimate:
    def test_empty(self):
        assert _fallback_estimate("") == 0

    def test_ascii(self):
        # ~4 chars / token
        result = _fallback_estimate("a" * 40)
        assert 8 <= result <= 12

    def test_non_ascii_denser(self):
        # ~2 chars / token
        result = _fallback_estimate("я" * 40)
        assert result >= 18


class TestApplyMultiplier:
    def test_opus_47_multiplier(self):
        assert _apply_multiplier("Claude Opus 4.7", 100) == 120

    def test_no_multiplier_for_others(self):
        assert _apply_multiplier("GPT-5.4", 100) == 100
        assert _apply_multiplier("Claude Opus 4.6", 100) == 100