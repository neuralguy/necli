"""agent/tg_format.py — md_to_tg_html: markdown → Telegram-HTML."""

from agent.tg_format import md_to_tg_html

LT = chr(38) + "lt;"
GT = chr(38) + "gt;"
AMP = chr(38) + "amp;"

class TestEmptyAndPlain:
    def test_empty_string(self):
        assert md_to_tg_html("") == ""

    def test_plain_text_unchanged(self):
        assert md_to_tg_html("hello world") == "hello world"

    def test_multiline_plain(self):
        assert md_to_tg_html("a\nb\nc") == "a\nb\nc"

class TestHtmlEscaping:
    def test_escapes_angle_brackets_and_amp(self):
        out = md_to_tg_html("a < b & c > d")
        assert LT in out
        assert GT in out
        assert AMP in out
        assert "<b" not in out  # no stray real tags

    def test_quotes_not_escaped(self):
        out = md_to_tg_html('say "hi"')
        assert '"hi"' in out

    def test_raw_html_is_neutralized(self):
        out = md_to_tg_html("<script>alert(1)</script>")
        assert LT + "script" + GT in out
        assert "<script>" not in out

class TestBold:
    def test_double_star_bold(self):
        assert md_to_tg_html("**big**") == "<b>big</b>"

    def test_double_underscore_bold(self):
        assert md_to_tg_html("__big__") == "<b>big</b>"

    def test_bold_inside_sentence(self):
        assert md_to_tg_html("this is **bold** text") == "this is <b>bold</b> text"

    def test_bold_with_special_chars_escaped(self):
        assert md_to_tg_html("**a < b**") == "<b>a " + LT + " b</b>"

class TestItalic:
    def test_single_star_italic(self):
        assert md_to_tg_html("*slanted*") == "<i>slanted</i>"

    def test_single_underscore_italic(self):
        assert md_to_tg_html("_slanted_") == "<i>slanted</i>"

    def test_italic_not_eating_bold(self):
        assert md_to_tg_html("**both**") == "<b>both</b>"

    def test_underscore_inside_word_not_italic(self):
        assert "<i>" not in md_to_tg_html("foo_bar_baz")

    def test_multiplication_stars_not_italic(self):
        assert md_to_tg_html("5 * 3 and 2 * 4") == "5 * 3 and 2 * 4"

    def test_spaced_stray_stars_not_italic(self):
        assert "<i>" not in md_to_tg_html("a * b and c * d here")

    def test_spaced_stray_underscores_not_italic(self):
        assert "<i>" not in md_to_tg_html("5 _ 3 and 2 _ 4")

class TestCode:
    def test_inline_code(self):
        assert md_to_tg_html("`x = 1`") == "<code>x = 1</code>"

    def test_inline_code_escapes_content(self):
        assert md_to_tg_html("`a < b`") == "<code>a " + LT + " b</code>"

    def test_inline_code_not_formatted_inside(self):
        assert md_to_tg_html("`**not bold**`") == "<code>**not bold**</code>"

    def test_fenced_code_block(self):
        assert md_to_tg_html("```\nline1\nline2\n```") == "<pre>line1\nline2</pre>"

    def test_fenced_code_block_with_lang(self):
        out = md_to_tg_html("```python\nx = 1\n```")
        assert out == '<pre><code class="language-python">x = 1</code></pre>'

    def test_fenced_code_escapes_html(self):
        out = md_to_tg_html("```\n<tag> & stuff\n```")
        assert LT + "tag" + GT in out
        assert AMP in out

    def test_unclosed_fence_flushed_as_pre(self):
        assert md_to_tg_html("```\ndangling") == "<pre>dangling</pre>"

class TestLinks:
    def test_simple_link(self):
        out = md_to_tg_html("[Google](https://google.com)")
        assert out == '<a href="https://google.com">Google</a>'

    def test_link_label_escaped(self):
        out = md_to_tg_html("[a < b](https://x.com)")
        assert out == '<a href="https://x.com">a ' + LT + ' b</a>'

    def test_link_inside_text(self):
        out = md_to_tg_html("see [here](http://x.io) now")
        assert out == 'see <a href="http://x.io">here</a> now'

    def test_non_http_not_linked(self):
        assert "<a " not in md_to_tg_html("[x](ftp://nope)")

class TestNestedAndMixed:
    def test_bold_and_italic_together(self):
        assert md_to_tg_html("**b** and *i*") == "<b>b</b> and <i>i</i>"

    def test_code_and_bold_together(self):
        assert md_to_tg_html("**b** then `c`") == "<b>b</b> then <code>c</code>"

    def test_strike(self):
        assert md_to_tg_html("~~gone~~") == "<s>gone</s>"

    def test_bold_with_code_inside_kept_literal(self):
        assert md_to_tg_html("text `a**b` more") == "text <code>a**b</code> more"

class TestBlockElements:
    def test_heading_becomes_bold(self):
        assert md_to_tg_html("# Title") == "<b>Title</b>"

    def test_deep_heading_has_prefix(self):
        assert md_to_tg_html("### Sub") == "<b>▸ Sub</b>"

    def test_heading_inline_formatting(self):
        assert md_to_tg_html("## Hello **world**") == "<b>Hello <b>world</b></b>"

    def test_unordered_list_bullet(self):
        assert md_to_tg_html("- item") == "• item"

    def test_ordered_list(self):
        assert md_to_tg_html("1. first") == "1. first"

    def test_blockquote(self):
        assert md_to_tg_html("> quoted") == "<blockquote>quoted</blockquote>"

    def test_horizontal_rule(self):
        assert md_to_tg_html("---") == "➖➖➖➖➖"

    def test_list_with_inline_formatting(self):
        assert md_to_tg_html("- **bold** item") == "• <b>bold</b> item"
