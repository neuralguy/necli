"""Split-screen рендер для режима side-by-side.

DuoTracker — один rich.Live, делящий экран по центру на 2 колонки. Каждая
колонка отображает поток одного субагент-буфера (SubagentBuffer): заголовок
(модель + статус), хвост стримящегося текста, текущее действие и трейл
инструментов. Существующая система стриминга (agent/stream.py LiveStream)
НЕ задействуется — это отдельный единственный Live.

Колонки склеиваются ПОСТРОЧНО в плоский Group из Text (как SubagentTracker):
никаких Panel/Table и никакого резерва пустых строк под весь экран — именно
полноэкранный renderable заставлял Live дублировать кадры в скроллбэк.
Ширина строк меряется через rich.cells.cell_len (корректно для эмодзи).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Optional

from rich.cells import cell_len
from rich.console import Console, Group
from rich.live import Live
from rich.text import Text

from config.themes import t
from config.ui import ui
from agent.subagent_render import SubagentBuffer, _wrap_words

logger = logging.getLogger(__name__)
console = Console()

_MAX_BODY_LINES = 18

class DuoTracker:
    """Один Live на 2 колонки — по одному SubagentBuffer в каждой."""

    def __init__(self, buffers: list[SubagentBuffer]):
        if len(buffers) != 2:
            raise ValueError("DuoTracker requires exactly 2 buffers")
        self._buffers = buffers
        self._live: Optional[Live] = None

    def start(self) -> None:
        self._live = Live(
            console=console,
            refresh_per_second=int(ui.get("live_stream.refresh_per_second", 8)),
            transient=False,
            get_renderable=self._render,
        )
        self._live.start()

    def stop(self) -> None:
        if self._live:
            try:
                self._live.update(self._render())
                self._live.stop()
            except Exception:
                logger.debug("DuoTracker.stop() failed", exc_info=True)
            self._live = None

    async def wait_all_done(self) -> None:
        while not all(b.status in ("done", "error") for b in self._buffers):
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.3)

    def _column_lines(self, buf: SubagentBuffer, w: int) -> list[Text]:
        lines: list[Text] = []
        glyph, gstyle = buf._status_glyph()
        head_style = (
            f"bold {t('success')}" if buf.status == "done"
            else "bold red" if buf.status == "error"
            else f"bold {t('magenta')}"
        )
        head = Text()
        head.append(f"{glyph} ", style=gstyle)
        head.append(f"\U0001f916 {buf.model_label or 'model'}", style=head_style)
        lines.append(head)
        lines.append(Text("\u2500" * w, style="dim"))

        wrapped: list[str] = []
        text = buf.streaming_text or ""
        for ln in (text.splitlines() or [""]):
            if ln:
                wrapped.extend(_wrap_words(ln, w))
            else:
                wrapped.append("")
        if len(wrapped) > _MAX_BODY_LINES:
            lines.append(Text("\u2026", style="dim"))
            wrapped = wrapped[-_MAX_BODY_LINES:]
        for ln in wrapped:
            lines.append(Text(ln))

        action = Text()
        action.append_text(buf._action_line())
        lines.append(action)

        trail = buf._emoji_trail(w)
        if trail.plain:
            lines.append(trail)

        lines.append(Text(
            f"\u23f1 {buf.elapsed:.0f}s \u00b7 iter {buf.iteration + 1}",
            style="dim",
        ))
        return lines

    def _render(self) -> Group:
        width = shutil.get_terminal_size((80, 24)).columns
        col_w = max(20, (width // 2) - 2)
        left = self._column_lines(self._buffers[0], col_w)
        right = self._column_lines(self._buffers[1], col_w)
        n = max(len(left), len(right))
        rows: list[Text] = []
        for i in range(n):
            lft = left[i] if i < len(left) else Text("")
            rgt = right[i] if i < len(right) else Text("")
            lft.truncate(col_w)
            rgt.truncate(col_w)
            row = Text()
            row.append_text(lft)
            pad = col_w - cell_len(lft.plain)
            if pad > 0:
                row.append(" " * pad)
            row.append(" \u2502 ", style="dim")
            row.append_text(rgt)
            rows.append(row)
        return Group(*rows)