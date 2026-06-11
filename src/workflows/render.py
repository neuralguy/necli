"""Live renderer for workflow runs.

Единый framed-рендер в стиле Claude Code (как SubagentTracker): один внешний
фрейм, слева панель «Phases» со списком фаз и прогрессом done/total (активная
фаза помечена «›»), справа панель с агентами активной фазы. Сверху — хедер
«Workflow · <name> … N/M agents · время».

Важно: Live создаётся с transient=False и НЕ перепечатывает renderable вручную
после stop() — это и есть причина мерцания/наслоения нескольких хедеров в
прошлой реализации (transient=True + console.print(final)). Поведение копирует
рабочий SubagentTracker, который не мерцает.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from typing import Optional

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text

from agent.subagent_render import SubagentBuffer
from agent.subagent_render import _fmt_tokens, _spinner_frame
from config.themes import t
from config.ui import ui
from models import get_pricing as _get_pricing
from ui.formatting import format_cost as _format_cost

logger = logging.getLogger(__name__)
console = Console()


def _width() -> int:
    # Панель на всю ширину терминала. Оставляем 1 колонку запаса справа, чтобы
    # рамка не упёрлась в край (иначе возможен перенос/мерцание). Явный
    # workflow.max_width может ограничить ширину, если задан.
    term = min(shutil.get_terminal_size((100, 24)).columns, console.size.width)
    full = max(64, term - 1)
    configured = ui.get("workflow.max_width", 0)
    try:
        configured = int(configured or 0)
    except (TypeError, ValueError):
        configured = 0
    width = min(full, configured) if configured > 0 else full
    return max(64, width)


def _fmt_clock(secs: float) -> str:
    s = int(secs)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def short_agent_label(prompt: str, limit: int = 30) -> str:
    text = " ".join(str(prompt or "").split())
    if not text:
        return "agent"
    for prefix in ("Create ", "Write ", "Implement ", "Research ", "Review ", "Verify "):
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].strip() or text
            break
    return _fit(text, limit)


def _fit(text: str, width: int) -> str:
    text = str(text or "")
    if len(text) <= width:
        return text + " " * max(0, width - len(text))
    if width <= 1:
        return "…"
    return text[: width - 1].rstrip() + "…"


def _fit_plain(text: str, width: int) -> str:
    """Усечь без правого паддинга (для встраивания в строку)."""
    text = str(text or "")
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 1:
        return "…"
    return text[: width - 1].rstrip() + "…"


class WorkflowTracker:
    def __init__(self, state, buffers_by_agent_id: dict[str, SubagentBuffer]):
        self.state = state
        self.buffers_by_agent_id = buffers_by_agent_id
        self._live: Optional[Live] = None
        self._selected = 0
        self._started_at = time.monotonic()
        self._stopped = False

    def start(self) -> None:
        if self._live is not None:
            return
        # transient=False: финальный кадр остаётся в скроллбэке, Live не
        # перерисовывает поверх себя при stop(). refresh_per_second держим
        # умеренным — высокая частота на большом renderable и даёт мерцание.
        self._live = Live(
            console=console,
            refresh_per_second=int(ui.get("live_stream.refresh_per_second", 8)),
            transient=False,
            get_renderable=self.render,
        )
        self._live.start()

    def stop(self) -> None:
        self._stopped = True
        if self._live:
            try:
                # Один финальный апдейт + stop. НЕ печатаем renderable повторно
                # через console.print — это и создавало дубли хедера.
                self._live.update(self.render())
                self._live.stop()
            except Exception:
                logger.debug("WorkflowTracker.stop failed", exc_info=True)
            self._live = None

    async def wait_all_done(self) -> None:
        while self.state and self.state.status == "running":
            await asyncio.sleep(0.2)

    def select_phase(self, phase) -> None:
        phases = self.state.phases if self.state else []
        try:
            self._selected = phases.index(phase)
        except ValueError:
            self._selected = max(0, min(self._selected, len(phases) - 1))
        self._update()

    def _update(self) -> None:
        if self._live:
            try:
                self._live.update(self.render(), refresh=True)
            except Exception:
                logger.debug("WorkflowTracker live update failed", exc_info=True)

    # ── активная фаза ────────────────────────────────────────────

    def _active_index(self, phases: list) -> int:
        """Индекс активной фазы.

        Приоритет: фаза, выбранная раннером (self._selected, если она ещё не
        завершена). Иначе — первая незавершённая, иначе последняя. Это убирает
        «прыжки» подсветки и совпадает с логикой SubagentTracker.
        """
        if not phases:
            return 0
        sel = max(0, min(self._selected, len(phases) - 1))
        if self._phase_status(phases[sel]) == "running":
            return sel
        for i, ph in enumerate(phases):
            if self._phase_status(ph) not in ("done", "failed"):
                return i
        return len(phases) - 1

    @staticmethod
    def _phase_status(phase) -> str:
        status = getattr(phase, "status", "") or ""
        if status:
            return status
        agents = getattr(phase, "agents", None) or []
        if agents and all(a.status in ("done", "failed") for a in agents):
            return "done"
        return "running" if agents else "pending"

    # ── рендер ───────────────────────────────────────────────────

    def render(self) -> Group:
        width = _width()
        state = self.state
        phases = list(state.phases or [])
        active_idx = self._active_index(phases)
        self._selected = active_idx
        active = phases[active_idx] if phases else None

        total_agents = sum(len(p.agents) for p in phases)
        done_agents = sum(
            1 for p in phases for a in p.agents
            if a.status in ("done", "failed")
        )
        failed_agents = sum(1 for p in phases for a in p.agents if a.status == "failed")

        # ── хедер: имя · N/M agents · стоимость · время ──
        header = Text("  ")
        header.append("Workflow", style=f"bold {t('magenta')}")
        if state.name:
            header.append(f" · {state.name}", style=f"bold {t('accent')}")
        right = f"{done_agents}/{total_agents} agents"
        if failed_agents:
            right += f" · {failed_agents} failed"
        cost = self._total_cost()
        if cost > 0:
            right += f" · {_format_cost(cost)}"
        right += f" · {_fmt_clock(time.monotonic() - self._started_at)}"
        gap = max(1, width - len(header.plain) - len(right))
        header.append(" " * gap)
        header.append(right, style="dim")

        # ── ОДНА таблица: внешний фрейм + вертикальный разделитель «│».
        #    Слева колонка фаз, справа — агенты активной фазы. Заголовки
        #    секций («Phases», «<PhaseTitle> · N agents») — первая строка
        #    внутри фрейма, как у Claude Code (без вложенных панелей).
        inner = max(40, width - 4)  # минус рамка(2) и padding(2)
        sep = " │ "
        # Левый столбец (Phases) делаем компактным, чтобы освободить место под
        # модель и активность справа. Узкая доля + жёсткий потолок.
        left_w = max(18, min(26, int(inner * 0.22)))
        right_w = max(28, inner - left_w - len(sep))

        rows: list[Text] = []

        # Строка-заголовок секций.
        title = "Agents" if active is None else f"{active.title} · {len(active.agents or [])} agents"
        head = Text()
        head.append(_fit("Phases", left_w), style=f"bold {t('magenta')}")
        head.append(sep, style="dim")
        head.append(_fit(title, right_w), style=f"bold {t('accent')}")
        rows.append(head)

        # Тело: построчное склеивание левой (фазы) и правой (агенты) колонок.
        left_lines = self._phase_rows(phases, active_idx, left_w)
        right_lines = self._agent_rows(active, right_w)
        total = max(len(left_lines), len(right_lines), 1)
        blank_left = Text(" " * left_w)
        for i in range(total):
            left = left_lines[i] if i < len(left_lines) else blank_left
            rgt = right_lines[i] if i < len(right_lines) else Text("")
            row = Text()
            row.append_text(left)
            row.append(sep, style="dim")
            row.append_text(rgt)
            rows.append(row)

        frame = Panel(
            Group(*rows),
            border_style=t("accent"),
            padding=(0, 1),
            width=width,
        )
        return Group(header, frame)

    def _total_cost(self) -> float:
        """Суммарная стоимость по всем агентам прогона (по их буферам).

        Цена берётся из get_pricing(model) — ($/1M вход, $/1M выход).
        """
        total = 0.0
        for buf in self.buffers_by_agent_id.values():
            try:
                price_in, price_out = _get_pricing(buf.model_label or "")
            except Exception:
                continue
            total += (buf.input_tokens or 0) * price_in / 1_000_000
            total += (buf.output_tokens or 0) * price_out / 1_000_000
        return total

    def _phase_rows(self, phases, active_idx: int, width: int) -> list[Text]:
        if not phases:
            return [Text(_fit("preparing phases…", width), style="dim")]
        rows: list[Text] = []
        for idx, phase in enumerate(phases):
            agents = phase.agents or []
            done = sum(1 for a in agents if a.status in ("done", "failed"))
            is_active = idx == active_idx
            marker = "› " if is_active else "  "
            mstyle = f"bold {t('accent')}" if is_active else "dim"
            count = f"{done}/{len(agents)}"
            prefix = f"{marker}{idx + 1} "
            name_budget = max(3, width - len(prefix) - len(count) - 1)
            name = phase.title if len(phase.title) <= name_budget else phase.title[: name_budget - 1] + "…"
            gap = max(1, width - len(prefix) - len(name) - len(count))
            row = Text()
            row.append(marker, style=mstyle)
            row.append(f"{idx + 1} ", style=mstyle)
            row.append(name, style=(f"bold {t('accent')}" if is_active else ""))
            row.append(" " * gap)
            row.append(count, style="dim")
            rows.append(row)
        return rows

    def _agent_rows(self, phase, width: int) -> list[Text]:
        if phase is None:
            return [Text(_fit("waiting…", width), style="dim")]
        rows: list[Text] = []
        for agent in phase.agents or []:
            buf = self.buffers_by_agent_id.get(agent.id)
            if buf is not None:
                rows.append(self._buffer_row(buf, width))
            else:
                rows.append(self._state_row(agent, width))
        return rows or [Text(_fit("(no agents)", width), style="dim")]

    @staticmethod
    def _status_glyph(status: str) -> tuple[str, str]:
        if status == "done":
            return "✓", f"bold {t('success')}"
        if status in ("error", "failed"):
            return "✗", "bold red"
        return _spinner_frame(), f"bold {t('magenta')}"

    def _buffer_row(self, buf, width: int) -> Text:
        """Одна строка на агента: <глиф> <label> · <что делает сейчас>  <метрики>.

        «Что делает сейчас» — реальный вызываемый инструмент (эмодзи + имя +
        короткий аргумент), либо итог (done · N iter / error). Метрики прижаты
        вправо.
        """
        glyph, gstyle = self._status_glyph(buf.status)
        name_style = (
            f"bold {t('success')}" if buf.status == "done"
            else "bold red" if buf.status == "error"
            else f"bold {t('magenta')}"
        )

        # Метрики справа: токены · инструменты · время.
        metrics = Text()
        metrics.append(f"{_fmt_tokens(buf.total_tokens)} tok" if buf.total_tokens else "0 tok", style="dim")
        n_tools = len(buf.tool_events)
        if n_tools:
            metrics.append(f" · {n_tools} tool{'s' if n_tools != 1 else ''}", style="dim")
        metrics.append(f" · {buf.elapsed:.0f}s", style="dim")

        # Левая часть: глиф + label + модель.
        left = Text()
        left.append(f"{glyph} ", style=gstyle)
        left.append(buf.label or f"Sub{buf.index + 1}", style=name_style)
        if buf.model_label:
            left.append(f"  {buf.model_label}", style="dim")

        # Текущая активность (реальный инструмент) — отдельным сегментом, чтобы
        # покрасить имя инструмента акцентом. Усекаем под доступную ширину.
        activity, act_style, act_tool = self._activity(buf)
        avail = width - len(left.plain) - len(metrics.plain) - 1  # 1 — минимум разрыв
        if activity and avail >= 6:
            seg = Text()
            seg.append(" · ", style="dim")
            shown = _fit_plain(activity, avail - 3)
            if act_tool:
                # «<emoji> <tool>» красим акцентом, хвост-аргумент — dim.
                seg.append(shown, style=act_style)
            else:
                seg.append(shown, style="dim")
            left.append_text(seg)

        gap = width - len(left.plain) - len(metrics.plain)
        if gap < 1:
            avail2 = max(4, width - len(metrics.plain) - 1)
            left.truncate(avail2, overflow="ellipsis")
            gap = max(1, width - len(left.plain) - len(metrics.plain))
        left.append(" " * gap)
        left.append_text(metrics)
        left.truncate(width, overflow="ellipsis")
        return left

    @staticmethod
    def _activity(buf) -> tuple[str, str, bool]:
        """(текст, стиль, это_имя_инструмента?) — что агент делает прямо сейчас.

        Приоритет — РЕАЛЬНЫЙ текущий инструмент: «<emoji> <tool> <короткий arg>».
        Иначе — последний инструмент / итог / стартовое состояние.
        """
        status = getattr(buf, "status", "")
        if status == "done":
            tail = f"done · {buf.iteration} iter"
            if getattr(buf, "files_changed", 0):
                tail += f" · {buf.files_changed} files"
            return tail, "dim", False
        if status == "error":
            return f"error — {(buf.error or 'unknown')[:60]}", "red", False

        events = getattr(buf, "tool_events", None) or []
        last = events[-1] if events else None
        if last is not None:
            # Без эмодзи: эмодзи занимают 2 терминальные колонки, а len() считает
            # их как 1 — из-за этого ломался расчёт ширины строки.
            tool = getattr(last, "tool_name", "") or "tool"
            cmd = (getattr(last, "command", "") or "").strip()
            if cmd:
                prefix = "$ " if tool == "shell" else ""
                head = f"{tool} {prefix}{cmd[:48]}"
            else:
                head = tool
            running = getattr(last, "status", "") == "running"
            return head, (t("magenta") if running else "dim"), True

        if status == "streaming":
            return "thinking…", "dim", False
        return "starting", "dim", False

    def _state_row(self, agent, width: int) -> Text:
        glyph = "✓" if agent.status == "done" else "✗" if agent.status == "failed" else "·"
        style = (
            f"bold {t('success')}" if agent.status == "done"
            else "bold red" if agent.status == "failed"
            else "dim"
        )
        left = Text()
        left.append(f"{glyph} ", style=style)
        left.append(agent.label or short_agent_label(agent.prompt), style=style)
        model = getattr(agent, "model", "") or ""
        if model:
            left.append(f"  {model}", style="dim")
        if agent.cached:
            left.append("  cached", style="dim")
        metrics = Text(agent.status, style="dim")
        gap = max(1, width - len(left.plain) - len(metrics.plain))
        left.append(" " * gap)
        left.append_text(metrics)
        left.truncate(width, overflow="ellipsis")
        return left
