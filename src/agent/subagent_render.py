"""Отображение субагентов.

SubagentBuffer — буфер событий одного субагента с рендерингом.
SubagentTracker — лёгкий Rich Live без захвата stdin.

Единый framed-рендер (стиль Claude Code) используется всегда. Слева панель
«Phases» со списком фаз и прогрессом done/total (активная фаза помечена «›»),
справа панель с агентами активной фазы: строка
«<глиф> <label>  <модель>   <Ntok · Mt · Ns>». Если фаз нет, создаётся
синтетическая фаза «Agents», чтобы внешний вид не переключался.
Сверху хедер «Subagents … N/M agents · общее_время».
"""

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agent.display import SPINNER_FRAMES as _SPINNER_FRAMES
from config.themes import t
from config.ui import ui

logger = logging.getLogger(__name__)
console = Console()


def _w() -> int:
    cap = int(ui.get("subagent.max_width", 0))
    term = shutil.get_terminal_size((80, 24)).columns
    return term if cap <= 0 else min(cap, term)


def _spinner_frame() -> str:
    idx = int(time.monotonic() * 8) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[idx]


def _fmt_tokens(n: int) -> str:
    """Компактный формат счётчика токенов: 940, 25.8k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000.0
        return f"{v:.1f}k" if v < 100 else f"{v:.0f}k"
    v = n / 1_000_000.0
    return f"{v:.1f}M" if v < 100 else f"{v:.0f}M"


def _tool_emoji(tool_name: str) -> str:
    try:
        return (ui.tool(tool_name).get("emoji") or "•").strip() or "•"
    except Exception:
        logger.debug("tool emoji lookup failed for %r", tool_name, exc_info=True)
        return "•"


@dataclass
class ToolEvent:
    tool_name: str
    command: str
    emoji: str = "•"
    status: str = "running"
    elapsed: float = 0.0


class SubagentBuffer:
    """Буфер вывода одного субагента с компактным рендерингом."""

    def __init__(
        self, index: int, mode: str, prompt: str, model_label: str = "",
        role: str = "", preset: str = "", depends_on: Optional[list] = None,
        phase: str = "", label: str = "",
    ):
        self.index = index
        self.mode = mode
        self.prompt = prompt
        self.model_label = model_label or ""
        self.role = role or ""
        self.preset = preset or ""
        self.depends_on = list(depends_on or [])
        self.phase = phase or ""
        self.label = label or ""
        self.streaming_text = ""
        self.tool_events: list[ToolEvent] = []
        self.iteration = 0
        self.status = "starting"
        self.error: Optional[str] = None
        self.activity_start_time: Optional[float] = None
        self.activity_end_time: Optional[float] = None
        self.final_response = ""
        self.files_changed = 0
        # Накопленное потребление токенов (по последнему non-empty usage каждого
        # вызова модели — провайдеры шлют usage финальным чанком стрима).
        # АКТИВНЫЙ контекст последнего вызова модели (как видит модель сейчас) —
        # input последнего обмена + его output. Это та же метрика, что у обычного
        # агента: «сколько занято в окне», а НЕ сумма по итерациям.
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        # Кумулятивно по всем вызовам (для справки/биллинга), НЕ для строки статуса.
        self.cumulative_tokens = 0

    def _mark_activity(self) -> None:
        now = time.monotonic()
        if self.activity_start_time is None:
            self.activity_start_time = now
        self.activity_end_time = now

    @property
    def elapsed(self) -> float:
        if self.activity_start_time is None:
            return 0.0
        end = self.activity_end_time or time.monotonic()
        return end - self.activity_start_time

    def on_chunk(self, text: str):
        self._mark_activity()
        self.streaming_text = text
        self.status = "streaming"

    def on_tool_start(self, tool_name: str, command: str, args: Optional[dict] = None):
        self._mark_activity()
        self.status = "tools"
        hint = ""
        if args:
            if tool_name == "shell":
                cmd = args.get("command") or command or ""
                hint = cmd.splitlines()[0] if cmd else ""
            else:
                path = args.get("path")
                if isinstance(path, (list, tuple)):
                    hint = f"{len(path)} files" if len(path) != 1 else str(path[0])
                elif path:
                    hint = str(path)
        self.tool_events.append(
            ToolEvent(
                tool_name=tool_name,
                command=hint or command,
                emoji=_tool_emoji(tool_name),
            )
        )

    def on_tool_done(self, output_preview: str = "", elapsed: float = 0.0, error: bool = False):
        self._mark_activity()
        if self.tool_events:
            ev = self.tool_events[-1]
            ev.status = "error" if error else "done"
            ev.elapsed = elapsed

    def on_iteration(self, n: int):
        self.iteration = n

    def on_usage(self, usage_metadata: Optional[dict]) -> None:
        """Аккумулирует потребление токенов за один вызов модели.

        usage_metadata имеет форму _convert_usage (см. apis/base.py):
        {input_tokens, output_tokens, total_tokens, ...}.

        Каждый вызов модели шлёт ВЕСЬ растущий контекст заново, поэтому
        input_tokens N-го вызова ≈ весь активный контекст на тот момент.
        Суммировать их по итерациям нельзя — это даёт O(N²) и «1M за 10
        вызовов». Берём метрики ПОСЛЕДНЕГО вызова как активный контекст
        (та же семантика, что у обычного агента), а сумму держим отдельно
        в cumulative_tokens для справки.
        """
        if not usage_metadata:
            return
        inp = int(usage_metadata.get("input_tokens", 0) or 0)
        out = int(usage_metadata.get("output_tokens", 0) or 0)
        tot = int(usage_metadata.get("total_tokens", 0) or 0) or (inp + out)
        self.input_tokens = inp
        self.output_tokens = out
        self.total_tokens = tot
        self.cumulative_tokens += tot

    def on_done(self, response: str):
        self._mark_activity()
        self.status = "done"
        self.final_response = response

    def on_error(self, error: str):
        self._mark_activity()
        self.status = "error"
        self.error = error

    # ── вспомогательные части шапки ──────────────────────────────

    def _icon(self) -> str:
        return "\U0001f916"

    def _status_glyph(self) -> tuple[str, str]:
        """(глиф, стиль) — статус-индикатор слева в шапке."""
        if self.status == "done":
            return "\u2713", f"bold {t('success')}"
        if self.status == "error":
            return "\u2717", "bold red"
        return _spinner_frame(), f"bold {t('magenta')}"

    def _head_left(self) -> Text:
        """Левая часть шапки: глиф · SubN · роль/preset · модель · deps."""
        glyph, gstyle = self._status_glyph()
        head_style = (
            f"bold {t('success')}" if self.status == "done"
            else "bold red" if self.status == "error"
            else f"bold {t('magenta')}"
        )
        txt = Text()
        txt.append(f"{glyph} ", style=gstyle)
        name = self.label or f"Sub{self.index + 1}"
        txt.append(f"{self._icon()} {name}", style=head_style)
        label = self.preset or self.role
        if self.phase:
            txt.append(f" \u00b7 {self.phase}", style=t("accent"))
        if label:
            txt.append(f" \u00b7 {label}", style=t("purple"))
        if self.model_label:
            txt.append(f" \u00b7 {self.model_label}", style="dim")
        if self.depends_on:
            deps = ",".join(str(d) for d in self.depends_on)
            txt.append(f" \u00b7 \u2937 {deps}", style="dim")
        return txt

    def _timer(self) -> str:
        return f"\u23f1 {self.elapsed:.0f}s"

    def _emoji_trail(self, budget: int = 36) -> Text:
        """Трейл эмодзи завершённых инструментов — столько, сколько влезает в budget."""
        evs = self.tool_events
        if not evs:
            return Text("")
        budget = max(0, budget)
        token_w = 3  # "✓" + emoji + пробел
        used = 0
        count = 0
        for _ in reversed(evs):
            if used + token_w > budget:
                break
            used += token_w
            count += 1
        # Если показаны не все — резервируем место под ведущий "… " (2 символа).
        if count < len(evs):
            while count > 0 and used + 2 > budget:
                used -= token_w
                count -= 1
        shown = evs[-count:] if count else []
        trail = Text()
        if len(evs) > len(shown):
            trail.append("\u2026 ", style="dim")
        for ev in shown:
            if ev.status == "done":
                trail.append(f"\u2713{ev.emoji} ", style=t("success"))
            elif ev.status == "error":
                trail.append(f"\u2717{ev.emoji} ", style="red")
            else:
                trail.append(f"{_spinner_frame()}{ev.emoji} ", style=t("magenta"))
        return trail

    def _action_line(self) -> Text:
        """Третья строка: что субагент делает сейчас / итог."""
        txt = Text()
        if self.status == "done":
            tail = f"done \u00b7 {self.iteration} iter"
            if self.files_changed:
                tail += f" \u00b7 {self.files_changed} files"
            txt.append(tail, style="dim")
        elif self.status == "error":
            err = (self.error or "unknown")[:80]
            txt.append(f"error \u2014 {err}", style="red")
        elif self.status == "streaming":
            lines = self.streaming_text.count("\n") + 1
            txt.append(f"iter {self.iteration + 1} \u2014 streaming ({lines} lines)", style="dim")
        elif self.status == "tools":
            last = self.tool_events[-1] if self.tool_events else None
            if last and last.status == "running":
                cmd = ""
                if last.command:
                    prefix = "$ " if last.tool_name == "shell" else ""
                    cmd = f" {prefix}{last.command.strip()[:48]}"
                txt.append(f"iter {self.iteration + 1} \u2014 ", style="dim")
                txt.append(f"{last.emoji} {last.tool_name}", style=t("magenta"))
                txt.append(cmd, style="dim")
            else:
                txt.append(f"iter {self.iteration + 1}", style="dim")
        else:
            txt.append("starting", style="dim")
        return txt

    # ── рендеры ──────────────────────────────────────────────────

    def render_block(self, width: int) -> list[Text]:
        """Многострочный блок: шапка(+таймер справа), задача, действие(+трейл)."""
        lines: list[Text] = []

        # 1. Шапка с правым секундомером.
        head = self._head_left()
        timer = self._timer()
        gap = width - len(head.plain) - len(timer)
        if gap < 1:
            gap = 1
        head.append(" " * gap)
        head.append(timer, style="dim")
        lines.append(head)

        # 2. Задача (prompt) — до prompt_lines строк с переносом по словам.
        n_prompt = max(1, int(ui.get("subagent.prompt_lines", 2)))
        wrapped = _wrap_words(self.prompt.strip(), width - 2)
        shown = wrapped[:n_prompt]
        truncated = len(wrapped) > n_prompt
        for idx, ln in enumerate(shown):
            # Если строк больше лимита — на последней показанной ставим эллипсис.
            if truncated and idx == len(shown) - 1:
                ln = ln[: max(0, width - 3)] + "\u2026"
            lines.append(Text(f"  {ln}", style="dim"))

        # 3. Действие + трейл эмодзи (трейл прижат вправо).
        action = self._action_line()
        action_prefixed = Text("  ")
        action_prefixed.append_text(action)
        trail = self._emoji_trail(width - len(action_prefixed.plain) - 1)
        if trail.plain:
            gap = width - len(action_prefixed.plain) - len(trail.plain)
            if gap < 1:
                gap = 1
            action_prefixed.append(" " * gap)
            action_prefixed.append_text(trail)
        lines.append(action_prefixed)
        return lines

    def render_agent_row(self, width: int) -> Text:
        """Строка агента для правой панели двухпанельного вида (стиль Claude Code).

        Формат: <глиф> <label>   <модель>   <Ntok · Mt · Ns>
        Метрики прижаты вправо; label усекается под доступную ширину.
        """
        glyph, gstyle = self._status_glyph()

        # Правая часть — метрики: токены · инструменты · время.
        metrics = Text()
        if self.total_tokens:
            metrics.append(f"{_fmt_tokens(self.total_tokens)} tok", style="dim")
        else:
            metrics.append("0 tok", style="dim")
        n_tools = len(self.tool_events)
        if n_tools:
            metrics.append(f" · {n_tools} tool{'s' if n_tools != 1 else ''}", style="dim")
        metrics.append(f" · {self.elapsed:.0f}s", style="dim")

        # Левая часть — глиф + label + модель.
        name = self.label or f"Sub{self.index + 1}"
        left = Text()
        left.append(f"{glyph} ", style=gstyle)
        name_style = (
            f"bold {t('success')}" if self.status == "done"
            else "bold red" if self.status == "error"
            else f"bold {t('magenta')}"
        )
        left.append(name, style=name_style)
        if self.model_label:
            left.append(f"  {self.model_label}", style="dim")

        # Собираем с выравниванием метрик вправо.
        gap = width - len(left.plain) - len(metrics.plain)
        if gap < 1:
            # Не влезает — режем label, оставляя место под метрики и 1 пробел.
            avail = max(4, width - len(metrics.plain) - 1)
            left.truncate(avail, overflow="ellipsis")
            gap = max(1, width - len(left.plain) - len(metrics.plain))
        left.append(" " * gap)
        left.append_text(metrics)
        left.truncate(width, overflow="ellipsis")
        return left

    def render_compact(self, width: int) -> Text:
        """Однострочный вид (когда субагентов много / для финального лога)."""
        head = self._head_left()
        action = self._action_line()
        head.append(" \u2014 ", style="dim")
        head.append_text(action)
        timer = f" \u00b7 {self.elapsed:.0f}s"
        head.append(timer, style="dim")
        trail = self._emoji_trail(width - len(head.plain) - 2)
        if trail.plain:
            gap = width - len(head.plain) - len(trail.plain) - 2
            if gap < 1:
                gap = 1
            head.append(" " * gap)
            head.append_text(trail)
        head.truncate(width, overflow="ellipsis")
        return head


def _wrap_words(text: str, width: int) -> list[str]:
    """Простой перенос по словам. Очень длинные слова режутся жёстко."""
    if not text:
        return [""]
    width = max(8, width)
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        while len(w) > width:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:width])
            w = w[width:]
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


class SubagentTracker:
    """Лёгкий мультистрочный Live без захвата stdin.

    transient=False — после stop() финальные строки остаются в скроллбэке.
    """

    def __init__(self, buffers: list[SubagentBuffer]):
        self._buffers = buffers
        self._live: Optional[Live] = None

    def start(self):
        self._live = Live(
            console=console,
            refresh_per_second=int(ui.get("live_stream.refresh_per_second", 8)),
            transient=False,
            get_renderable=self._render,
        )
        self._live.start()

    def stop(self):
        if self._live:
            try:
                self._live.update(self._render())
                self._live.stop()
            except Exception:
                logger.debug("Live.stop() failed in tracker", exc_info=True)
            self._live = None

    async def wait_all_done(self):
        while not all(b.status in ("done", "error") for b in self._buffers):
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.3)

    def _seen_phases(self) -> list[str]:
        """Фазы в порядке первого появления."""
        seen: list[str] = []
        for b in self._buffers:
            if b.phase and b.phase not in seen:
                seen.append(b.phase)
        return seen

    def _total_elapsed(self) -> float:
        """Стенные часы всего запуска: от первой активности до сейчас/последней."""
        starts = [b.activity_start_time for b in self._buffers if b.activity_start_time is not None]
        if not starts:
            return 0.0
        start = min(starts)
        all_done = all(b.status in ("done", "error") for b in self._buffers)
        if all_done:
            ends = [b.activity_end_time for b in self._buffers if b.activity_end_time is not None]
            end = max(ends) if ends else time.monotonic()
        else:
            end = time.monotonic()
        return max(0.0, end - start)

    @staticmethod
    def _fmt_clock(secs: float) -> str:
        s = int(secs)
        if s < 60:
            return f"{s}s"
        m, s = divmod(s, 60)
        if m < 60:
            return f"{m}m{s:02d}s"
        h, m = divmod(m, 60)
        return f"{h}h{m:02d}m{s:02d}s"

    def _render(self) -> Group:
        return self._render_panels()

    def _render_panels(self) -> Group:
        width = _w()
        n = len(self._buffers)
        done = sum(1 for b in self._buffers if b.status in ("done", "error"))
        real_phases = self._seen_phases()
        phases = real_phases or ["Agents"]

        def phase_buffers(phase: str) -> list[SubagentBuffer]:
            if real_phases:
                return [b for b in self._buffers if b.phase == phase]
            return self._buffers

        # Активная фаза — первая незавершённая, иначе последняя.
        active = phases[-1]
        for ph in phases:
            ph_bufs = phase_buffers(ph)
            if not all(b.status in ("done", "error") for b in ph_bufs):
                active = ph
                break

        # Хедер: N/M agents · общее время (прижато вправо).
        header = Text("  ")
        header.append("Subagents", style=f"bold {t('magenta')}")
        right = f"{done}/{n} agents · {self._fmt_clock(self._total_elapsed())}"
        gap = width - len(header.plain) - len(right)
        if gap < 1:
            gap = 1
        header.append(" " * gap)
        header.append(right, style="dim")

        # Левая панель: список фаз. Каждая фаза — ровно одна строка:
        # "<маркер><номер> <имя…>   done/total" — имя усекается под ширину.
        frame_width = max(40, width - 4)
        left_w = max(24, int(frame_width * 0.32))
        inner = left_w - 4  # минус рамка(2) и padding(2)
        phase_lines: list[Text] = []
        for i, ph in enumerate(phases):
            ph_bufs = phase_buffers(ph)
            ph_done = sum(1 for b in ph_bufs if b.status in ("done", "error"))
            marker = "› " if ph == active else "  "
            mstyle = f"bold {t('accent')}" if ph == active else "dim"
            count = f"{ph_done}/{len(ph_bufs)}"
            prefix = f"{marker}{i + 1} "
            # Бюджет под имя = inner − префикс − счётчик − разделитель(1 пробел).
            name_budget = max(3, inner - len(prefix) - len(count) - 1)
            name = ph if len(ph) <= name_budget else ph[: name_budget - 1] + "…"
            gap = max(1, inner - len(prefix) - len(name) - len(count))
            row = Text()
            row.append(marker, style=mstyle)
            row.append(f"{i + 1} ", style=mstyle)
            row.append(name, style=(f"bold {t('accent')}" if ph == active else ""))
            row.append(" " * gap)
            row.append(count, style="dim")
            phase_lines.append(row)
        left_panel = Panel(
            Group(*phase_lines),
            title="Phases",
            title_align="left",
            border_style=t("magenta"),
            padding=(0, 1),
            width=left_w,
        )

        # Правая панель: агенты активной фазы.
        active_bufs = phase_buffers(active)
        right_w = max(28, frame_width - left_w - 3)
        agent_lines = [b.render_agent_row(max(20, right_w - 4)) for b in active_bufs]
        if not agent_lines:
            agent_lines = [Text("(no agents)", style="dim")]
        right_panel = Panel(
            Group(*agent_lines),
            title=f"{active} · {len(active_bufs)} agents",
            title_align="left",
            border_style=t("accent"),
            padding=(0, 1),
            width=right_w,
        )

        grid = Table.grid(padding=(0, 1))
        grid.add_column()
        grid.add_column()
        grid.add_row(left_panel, right_panel)

        frame = Panel(
            grid,
            border_style=t("accent"),
            padding=(0, 0),
            width=width,
        )

        return Group(header, frame)

    def _render_flat(self) -> Group:
        width = _w()
        n = len(self._buffers)
        threshold = int(ui.get("subagent.block_threshold", 8))
        compact = threshold > 0 and n > threshold

        lines: list[Text] = []
        header = Text("  ")
        done = sum(1 for b in self._buffers if b.status in ("done", "error"))
        header.append(f"Subagents {done}/{n}", style=f"bold {t('magenta')}")
        lines.append(header)

        if compact:
            for buf in self._buffers:
                row = Text("  ")
                row.append_text(buf.render_compact(width - 2))
                lines.append(row)
            return Group(*lines)

        sep_char = str(ui.get("subagent.block_separator", "\u2500"))
        sep = Text(sep_char * width, style="dim")
        for buf in self._buffers:
            lines.append(sep)
            for ln in buf.render_block(width):
                row = Text("  ")
                row.append_text(ln)
                lines.append(row)
        lines.append(sep)
        return Group(*lines)