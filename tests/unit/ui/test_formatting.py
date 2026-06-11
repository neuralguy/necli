"""ui/formatting.py — format_elapsed, format_tokens, format_cost, latex_to_unicode."""

from ui.formatting import (
    format_elapsed,
    format_tokens,
    format_cost,
    progress_bar,
    latex_to_unicode,
    BAR_FILLED_START,
    BAR_EMPTY_START,
)


class TestFormatElapsed:
    def test_none(self):
        assert format_elapsed(None) == "\u2014"

    def test_milliseconds(self):
        result = format_elapsed(0.5)
        assert "ms" in result

    def test_seconds(self):
        assert format_elapsed(5.5) == "5.5s"

    def test_minutes(self):
        assert format_elapsed(90) == "1m 30s"


class TestFormatTokens:
    def test_small(self):
        assert format_tokens(42) == "42"

    def test_kilo(self):
        assert format_tokens(2500) == "2.5K"

    def test_million(self):
        assert format_tokens(1_500_000) == "1.50M"


class TestFormatCost:
    def test_micro(self):
        assert format_cost(0.0001) == "$0.000100"

    def test_small(self):
        assert format_cost(0.5) == "$0.5000"

    def test_large(self):
        assert format_cost(15.5) == "$15.50"


class TestProgressBar:
    def test_zero(self):
        bar = progress_bar(0, 10, width=10)
        assert BAR_EMPTY_START in bar
        # все ▯
        assert "▯" * 10 in bar

    def test_full(self):
        bar = progress_bar(10, 10, width=10)
        assert "▮" * 10 in bar

    def test_half(self):
        bar = progress_bar(5, 10, width=10)
        assert BAR_FILLED_START in bar
        assert BAR_EMPTY_START in bar
        assert "▮" * 5 in bar
        assert "▯" * 5 in bar

    def test_zero_total(self):
        bar = progress_bar(0, 0, width=10)
        # ratio=0 → all empty
        assert "▯" * 10 in bar


class TestLatexToUnicode:
    def test_passthrough_no_math(self):
        text = "hello world without math"
        assert latex_to_unicode(text) == text

    def test_inline_dollar(self):
        result = latex_to_unicode("formula $x^2$ here")
        assert "$" not in result or "x^2" not in result
        # либо unicode replacement, либо оставлено как есть — проверяем что без $...$
        assert "formula" in result and "here" in result

    def test_display_dollar(self):
        result = latex_to_unicode("Block: $$y^2$$ done")
        assert "Block:" in result and "done" in result

    def test_paren_inline(self):
        result = latex_to_unicode(r"text \(a+b\) ok")
        assert "text" in result and "ok" in result

    def test_bracket_display(self):
        result = latex_to_unicode(r"start \[c+d\] end")
        assert "start" in result and "end" in result

    def test_preserves_python_code_block(self):
        text = "```python\nx = 1\n```"
        assert latex_to_unicode(text) == text

    def test_preserves_inline_backticks(self):
        text = "code `x = 1` ok"
        assert latex_to_unicode(text) == text

    def test_latex_fence(self):
        text = "```latex\n\\alpha\n```"
        result = latex_to_unicode(text)
        # fence удалён, alpha заменена
        assert "```" not in result
        assert "alpha" not in result or "α" in result