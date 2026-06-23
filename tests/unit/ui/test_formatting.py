"""ui/formatting.py — format_elapsed, format_tokens, format_cost, latex_to_unicode."""

from ui.formatting import (
    format_elapsed,
    format_tokens,
    format_cost,
    progress_bar,
    latex_to_unicode,
    BAR_FILLED_START,
    BAR_FILLED_END,
    BAR_EMPTY_START,
    BAR_EMPTY_END,
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

    def test_marker_layout_half(self):
        bar = progress_bar(5, 10, width=10)
        # Маркеры должны идти строго: FS …▮ FE ES …▯ EE
        expected = (
            BAR_FILLED_START + "▮" * 5 + BAR_FILLED_END
            + BAR_EMPTY_START + "▯" * 5 + BAR_EMPTY_END
        )
        assert bar == expected

    def test_marker_layout_full(self):
        bar = progress_bar(10, 10, width=10)
        # При полном баре пустой сегмент пуст, но маркеры всё равно есть
        expected = (
            BAR_FILLED_START + "▮" * 10 + BAR_FILLED_END
            + BAR_EMPTY_START + BAR_EMPTY_END
        )
        assert bar == expected
        assert bar.index(BAR_FILLED_END) < bar.index(BAR_EMPTY_START)

    def test_marker_layout_zero(self):
        bar = progress_bar(0, 10, width=10)
        expected = (
            BAR_FILLED_START + BAR_FILLED_END
            + BAR_EMPTY_START + "▯" * 10 + BAR_EMPTY_END
        )
        assert bar == expected

    def test_ratio_clamped_overflow(self):
        # current > total → ratio clamps to 1.0, бар полностью заполнен
        bar = progress_bar(20, 10, width=10)
        assert bar == (
            BAR_FILLED_START + "▮" * 10 + BAR_FILLED_END
            + BAR_EMPTY_START + BAR_EMPTY_END
        )


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

    def test_currency_not_treated_as_math(self):
        text = "It costs $5 and $10 total"
        assert latex_to_unicode(text) == text

    def test_real_inline_math_still_converts(self):
        result = latex_to_unicode("formula $a^2 + b^2$ end")
        assert "$" not in result
        assert "formula" in result and "end" in result

    def test_bare_fence_math_unwrapped(self):
        # Голый ```-fence, содержащий только $$...$$, разворачивается:
        # fence-маркеры убираются, математика конвертируется.
        text = "```\n$$x^2$$\n```"
        result = latex_to_unicode(text)
        assert "```" not in result
        assert "$" not in result

    def test_bare_fence_math_with_surrounding_whitespace(self):
        # Текущее поведение: внутренние пробелы вокруг $$ допускаются
        # регэкспом _BARE_FENCE_MATH_RE и съедаются .strip().
        text = "```\n  $$y^2$$  \n```"
        result = latex_to_unicode(text)
        assert "```" not in result
        assert "$" not in result

    def test_bare_fence_non_math_preserved(self):
        # Голый fence без $$...$$ внутри — это обычный код, не трогаем.
        text = "```\nx = 1\n```"
        assert latex_to_unicode(text) == text