"""Consistent Markdown rendering for assistant responses."""

from typing import ClassVar

from rich import box
from rich.align import Align
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, TableElement
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
