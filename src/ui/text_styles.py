"""Small Rich text helpers shared by compact terminal renderers."""

from __future__ import annotations

import re


def styled_count_text(
    message: str,
    n: int,
    *,
    prefix: str = "",
    base_style: str = "",
    number_style: str = "bold",
):
    """Build Rich Text with the count visually separated from an expand hint."""
    from rich.text import Text

    result = Text(prefix + message, style=base_style)
    marker = str(n)
    start = message.find(marker)
    if start >= 0:
        start += len(prefix)
        result.stylize(number_style, start, start + len(marker))
    return result


def inline_markdown_text(
    text: str,
    *,
    base_style: str = "",
    code_style: str = "cyan",
):
    """Render compact inline Markdown while keeping a caller-provided base style.

    This is intentionally small: collapsed one-line previews need bold/italic/
    code/link emphasis, not full block Markdown layout.
    """
    from rich.markup import escape
    from rich.text import Text

    source = text or ""
    # Links keep their visible label in the collapsed preview.
    source = re.sub(r"!?\[([^]\n]+)\]\([^)]*\)", r"\1", source)
    source = escape(source)
    source = re.sub(r"\*\*([^*\n]+?)\*\*", r"[bold]\1[/bold]", source)
    source = re.sub(r"~~([^~\n]+?)~~", r"[strike]\1[/strike]", source)
    source = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"[italic]\1[/italic]", source)
    source = re.sub(r"`([^`\n]+?)`", f"[{code_style}]\\1[/{code_style}]", source)
    try:
        return Text.from_markup(source, style=base_style)
    except Exception:
        return Text(text or "", style=base_style)
