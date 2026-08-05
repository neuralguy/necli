"""Строки и полноэкранный просмотр фоновых shell-задач."""

from __future__ import annotations

import asyncio
import time

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from config.i18n import t as tr
from config.themes import t
from ui.shell import Overlay, RowGroup, get_shell, visible_width

_VIEWS: dict[str, BackgroundTaskView] = {}


def _short(text: str, width: int) -> str:
    text = " ".join((text or "").split())
    if visible_width(text) <= width:
        return text
    return text[:max(1, width - 1)].rstrip() + "…"


def _elapsed(job) -> float:
    end = job.finished_at or time.monotonic()
    return max(0.0, end - job.started_at)


def _status(job) -> tuple[str, str]:
    if job.status == "done":
        return "✓", t("success")
    if job.status in ("error", "timeout"):
        return "✗", t("error")
    if job.status == "cancelled":
        return "■", t("warning")
    return "◯", t("warning")


class BackgroundTaskView:
    def __init__(self, job) -> None:
        self.job = job
        self.key = f"background-{job.id}"
        self.shell = None
        self.overlay_task: asyncio.Task | None = None

    def attach(self) -> None:
        if getattr(self.job, "delivered", False):
            _VIEWS.pop(self.job.id, None)
            return
        shell = get_shell()
        if shell is None:
            return
        self.shell = shell
        shell.attach_rows(
            self.key,
            RowGroup(self.label, self.open, kind="task"),
        )

    def detach(self) -> None:
        if self.shell is not None:
            self.shell.detach_rows(self.key)
        self.shell = None

    def label(self) -> str:
        glyph, _style = _status(self.job)
        tail = f"{self.job.id} · {_elapsed(self.job):.0f}s"
        budget = max(12, 80 - visible_width(tail) - 8)
        if self.shell is not None:
            budget = max(12, self.shell._width() - visible_width(tail) - 8)
        return f"{glyph} ⚙ {_short(self.job.command, budget)}  {tail}"

    def open(self) -> None:
        shell = self.shell
        if shell is None or (self.overlay_task is not None and not self.overlay_task.done()):
            return
        self.overlay_task = asyncio.create_task(self._show(shell))

    async def _show(self, shell) -> None:
        ticker = asyncio.create_task(_overlay_ticker(shell))
        try:
            await shell.run_overlay(BackgroundTaskOverlay(self.job))
        finally:
            ticker.cancel()

    def invalidate(self) -> None:
        if self.shell is not None:
            self.shell.invalidate()


class BackgroundTaskOverlay(Overlay):
    expand_height = True
    restore_input_to_bottom = True

    def __init__(self, job) -> None:
        super().__init__()
        self.job = job
        self.offset = 0
        self.page = 1
        self.follow = True
        self.total = 0

    def version(self):
        tick = int(time.monotonic() * 5) if self.job.status == "running" else 0
        return (getattr(self.job, "revision", 0), self.offset, self.follow, tick)

    def _lines(self, width: int) -> list[Text]:
        inner = max(20, width - 6)
        lines = [Text("Command", style=f"bold {t('accent')}")]
        command = self.job.command or ""
        for raw in command.splitlines() or [""]:
            while len(raw) > inner:
                lines.append(Text("  " + raw[:inner], style=t("fg_primary")))
                raw = raw[inner:]
            lines.append(Text("  " + raw, style=t("fg_primary")))
        lines.extend([Text(""), Text("Output", style=f"bold {t('warning')}")])
        output = self.job.output or ""
        if not output:
            lines.append(Text("  " + tr("background.no_output"), style="dim"))
        else:
            for raw in output.rstrip().splitlines():
                if not raw:
                    lines.append(Text(""))
                    continue
                while len(raw) > inner:
                    lines.append(Text("  " + raw[:inner], style=t("dim_text")))
                    raw = raw[inner:]
                lines.append(Text("  " + raw, style=t("dim_text")))
        return lines

    def render(self, width: int):
        budget = self.shell.overlay_budget() if self.shell is not None else 20
        lines = self._lines(width)
        self.total = len(lines)
        self.page = max(1, budget - 4)
        max_offset = max(0, len(lines) - self.page)
        self.offset = max_offset if self.follow else max(0, min(self.offset, max_offset))
        visible = lines[self.offset:self.offset + self.page]
        while len(visible) < self.page:
            visible.append(Text(""))
        glyph, color = _status(self.job)
        title = f" {glyph} Background · {self.job.id} "
        subtitle = f" {self.job.status} · {_elapsed(self.job):.0f}s "
        return Panel(
            Group(*visible),
            title=title,
            title_align="left",
            subtitle=subtitle,
            subtitle_align="right",
            border_style=color,
            padding=(0, 1),
            width=width,
            height=max(3, budget),
        )

    def hint(self) -> str:
        hint = tr("background.task_hint")
        if self.job.status == "running":
            hint += f" · {tr('background.cancel_hint')}"
        return hint

    def handle_key(self, key: str, event) -> bool:
        del event
        if key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
        elif key == "c-x" and self.job.status == "running":
            from tools.background import cancel_background
            cancel_background(self.job.id)
        elif key in ("up", "k", "c-p"):
            self.follow = False
            self.offset = max(0, self.offset - 1)
        elif key in ("down", "j", "c-n"):
            self.offset += 1
            if self.offset >= max(0, self.total - self.page):
                self.follow = True
        elif key == "pageup":
            self.follow = False
            self.offset = max(0, self.offset - self.page)
        elif key == "pagedown":
            self.offset += self.page
        elif key == "home":
            self.follow = False
            self.offset = 0
        elif key == "end":
            self.follow = True
        return True


async def _overlay_ticker(shell) -> None:
    while True:
        await asyncio.sleep(0.1)
        shell.invalidate()


def attach_background_job(job) -> None:
    if getattr(job, "delivered", False):
        return
    view = BackgroundTaskView(job)
    _VIEWS[job.id] = view
    shell = get_shell()
    loop = getattr(shell, "_loop", None) if shell is not None else None
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(view.attach)
    else:
        view.attach()


def update_background_job(job) -> None:
    view = _VIEWS.get(job.id)
    if view is not None:
        view.invalidate()


def detach_background_job(job_id: str) -> None:
    view = _VIEWS.pop(job_id, None)
    if view is None:
        return
    shell = view.shell or get_shell()
    loop = getattr(shell, "_loop", None) if shell is not None else None
    if loop is not None and loop.is_running():
        loop.call_soon_threadsafe(view.detach)
    else:
        view.detach()
