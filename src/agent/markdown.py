"""Consistent Markdown rendering for assistant responses."""

from typing import ClassVar

from rich import box
from rich.align import Align
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, TableElement
from rich.segment import Segment
from rich.style import Style
from rich.table import Table

from config.themes import t


class ResponseTable(TableElement):
    """Compact, high-contrast table used in AI responses."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        table = Table(
            box=box.SQUARE,
            border_style=t("muted"),
            header_style=f"bold {t('accent')}",
            pad_edge=True,
            padding=(0, 1),
            collapse_padding=True,
            show_edge=True,
            show_lines=True,
        )

        # ponytail: markdown-it-py does not expose column alignment to Rich here.
        # Upgrade when tables need exact Markdown alignment semantics.

        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                table.add_column(Align.left(column.content.copy()), justify="left")

        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*(Align.left(cell.content) for cell in row.cells))

        yield table


class ResponseMarkdown(Markdown):
    """Markdown with the response-specific table element."""

    elements: ClassVar = {**Markdown.elements, "table_open": ResponseTable}


class ThoughtMarkdown(ResponseMarkdown):
    """Markdown for thoughts that preserves soft line breaks and stays muted."""

    def __init__(self, markup: str, *, style: str) -> None:
        super().__init__(
            markup,
            style=style,
            code_theme="monokai",
            inline_code_theme="monokai",
        )
        self._muted_style = style
        for token in self.parsed:
            for child in token.children or ():
                if child.type == "softbreak":
                    child.type = "hardbreak"

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        muted = console.get_style(self._muted_style)
        pale = Style(dim=True)
        for segment in super().__rich_console__(console, options):
            if segment.control:
                yield segment
                continue
            current = console.get_style(segment.style or "none")
            if current.color == muted.color:
                if current.bold:
                    current += Style(color=t("accent"))
                elif current.italic:
                    current += Style(color=t("purple"))
                elif current.strike:
                    current += Style(color=t("warning"))
            yield Segment(segment.text, current + pale)
