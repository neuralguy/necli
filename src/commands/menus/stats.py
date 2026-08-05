"""Чистый, иерархичный оверлей статистики команды /stats.

На первом экране остаются только четыре главных числа, заполнение контекста и
разбивка токенов. Подробности вынесены в два соседних раздела: ходы текущего
диалога и общая история. Так статистику можно прочитать за несколько секунд,
а редкие данные не конкурируют с главным итогом.

Все токены и цены берутся из usage провайдера по тем же правилам, что и
`Session._compute_cost`. Длительность хода считается по timestamp user →
последний assistant: поле `Message.duration` в истории не заполняется.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import models as app_models
import session.storage as storage
from config.i18n import get_lang
from config.i18n import t as tr
from logger import logger
from session import Session
from ui.formatting import format_cost, format_tokens
from ui.overlays import (
    BOLD,
    DIM,
    RESET,
    cell_width,
    clip,
    key_hints,
    more_note,
    pad,
    paint,
    role_bg,
    role_fg,
    row,
    scroll_window,
    section,
    spacer,
    strip_ansi,
)
from ui.shell import Overlay, get_shell

# `row` занимает четыре ячейки слева и одну справа. Все внутренние колонки
# считаются с тем же запасом, чтобы ни одна строка не переполняла терминал.
GUTTER = 5
NAME_CAP = 34


def _cell(text: str, width: int, right: bool = False) -> str:
    """Обрезать и выровнять строку в колонке с учётом ANSI и Unicode."""
    return pad(clip(text, width), width, "right" if right else "left")


def _bar_plain(ratio: float, width: int) -> str:
    ratio = max(0.0, min(float(ratio), 1.0))
    filled = round(width * ratio)
    if ratio > 0 and filled == 0:
        filled = 1
    return "█" * filled + "░" * (width - filled)


def _bar(ratio: float, width: int) -> str:
    plain = _bar_plain(ratio, width)
    filled = plain.count("█")
    return (
        f"{role_fg('bar_filled')}{'█' * filled}{RESET}"
        f"{DIM}{'░' * (width - filled)}{RESET}"
    )


def _plural(n: int, noun: str) -> str:
    if get_lang() == "ru":
        if n % 10 == 1 and n % 100 != 11:
            form = "one"
        elif n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
            form = "few"
        else:
            form = "many"
    else:
        form = "one" if n == 1 else "many"
    return tr(f"stats.count_{noun}_{form}", n=n)


def _dur(seconds: float) -> str:
    value = max(0.0, float(seconds))
    if value < 1:
        return f"{value * 1000:.0f}{tr('stats.unit_ms')}"
    if value < 10:
        return f"{value:.1f}{tr('stats.unit_s')}"
    if value < 60:
        return f"{int(value)}{tr('stats.unit_s')}"
    if value < 3600:
        return (
            f"{int(value) // 60}{tr('stats.unit_min')} "
            f"{int(value) % 60:02d}{tr('stats.unit_s')}"
        )
    return (
        f"{int(value) // 3600}{tr('stats.unit_h')} "
        f"{(int(value) % 3600) // 60:02d}{tr('stats.unit_min')}"
    )


def _money(cost: float) -> str:
    return "$0" if not cost else format_cost(cost)


def _metric_grid(
    metrics: list[tuple[str, str, str]],
    width: int,
    *,
    max_columns: int,
) -> list[str]:
    """Двухстрочная сетка: крупные значения сверху, тихие подписи снизу."""
    if not metrics:
        return []
    inner = max(1, width - GUTTER)
    columns = min(len(metrics), max_columns, max(1, inner // 14))
    column_width = max(1, inner // columns)
    lines: list[str] = []
    for start in range(0, len(metrics), columns):
        chunk = metrics[start : start + columns]
        values = "".join(
            _cell(paint(value, role, bold=True), column_width)
            for _label, value, role in chunk
        )
        labels = "".join(
            _cell(f"{DIM}{label}{RESET}", column_width)
            for label, _value, _role in chunk
        )
        lines.extend((values, labels))
    return lines


def _inline_metrics(metrics: list[tuple[str, str, str]], width: int) -> list[str]:
    """Компактные показатели `подпись значение`, с переносом целыми блоками."""
    separator = f"{DIM}  ·  {RESET}"
    lines: list[str] = []
    current = ""
    for label, value, role in metrics:
        label = label[:1].upper() + label[1:]
        metric = f"{DIM}{label}{RESET} {paint(value, role, bold=True)}"
        candidate = metric if not current else current + separator + metric
        if current and cell_width(candidate) > width:
            lines.append(current)
            current = metric
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _body_rows(lines: list[str]) -> list[tuple[str, str]]:
    return [(strip_ansi(line), line) for line in lines]


@dataclass
class Turn:
    """User-реплика и все ответы модели до следующей user-реплики."""

    num: int
    ts: float
    prompt: str
    model: str
    prompt_tokens: int
    input_tokens: int
    output_tokens: int
    reasoning: int
    cache_read: int
    cost: float
    wall: float
    replies: int


@dataclass
class Snapshot:
    """Снимок текущей сессии, который не пересчитывается на каждом кадре."""

    title: str = ""
    model: str = ""
    context_used: int = 0
    context_limit: int = 200_000
    billed_input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    total_cost: float = 0.0
    elapsed: float = 0.0
    turns: list[Turn] = field(default_factory=list)


def _usage_int(msg, key: str) -> int:
    usage = msg.usage if isinstance(msg.usage, dict) else None
    if not usage:
        return 0
    try:
        return int(usage.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _msg_cost(msg, input_buffer: list[int]) -> tuple[float, int, int]:
    price_in, price_out = app_models.get_pricing(msg.model or "unknown")
    input_tokens = _usage_int(msg, "input")
    output_tokens = _usage_int(msg, "output")
    if not input_tokens and not output_tokens:
        input_tokens = sum(input_buffer)
        output_tokens = msg.tokens
    elif not output_tokens:
        output_tokens = msg.tokens
    cost = input_tokens * price_in / 1_000_000 + output_tokens * price_out / 1_000_000
    return cost, input_tokens, output_tokens


def _collect_turns(session: Session) -> list[Turn]:
    turns: list[Turn] = []
    current: Turn | None = None
    input_buffer: list[int] = []
    for msg in session.messages:
        if msg.role == "user":
            current = Turn(
                num=len(turns) + 1,
                ts=msg.timestamp,
                prompt=" ".join((msg.content or "").split()),
                model="",
                prompt_tokens=0,
                input_tokens=0,
                output_tokens=0,
                reasoning=0,
                cache_read=0,
                cost=0.0,
                wall=0.0,
                replies=0,
            )
            turns.append(current)
            input_buffer = [msg.tokens]
            continue
        if msg.role in ("system", "tool_result"):
            input_buffer.append(msg.tokens)
            continue
        if msg.role != "assistant" or current is None:
            continue
        cost, input_tokens, output_tokens = _msg_cost(msg, input_buffer)
        input_buffer = []
        current.replies += 1
        current.cost += cost
        current.input_tokens += input_tokens
        current.output_tokens += output_tokens
        current.prompt_tokens = max(current.prompt_tokens, input_tokens)
        current.reasoning += _usage_int(msg, "reasoning")
        current.cache_read += _usage_int(msg, "cache_read")
        current.model = msg.model or current.model
        current.wall = max(0.0, msg.timestamp - current.ts)
    return turns


def collect(session: Session) -> Snapshot:
    snap = Snapshot()
    snap.title = session.title or session.id
    snap.model = session.last_model or ""
    snap.context_used = session.context_tokens
    snap.context_limit = app_models.get_context_limit(snap.model) or 200_000
    snap.turns = _collect_turns(session)

    snap.total_cost = float(session.total_cost or 0.0)
    snap.billed_input = sum(turn.input_tokens for turn in snap.turns)
    snap.output = sum(turn.output_tokens for turn in snap.turns)
    snap.reasoning = sum(turn.reasoning for turn in snap.turns)
    snap.cache_read = sum(turn.cache_read for turn in snap.turns)
    snap.elapsed = max(0.0, session.updated_at - session.created_at)
    return snap


@dataclass
class Body:
    """Неподвижная шапка, прокручиваемые строки и деталь выбранной строки."""

    head: list[str] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)
    foot: list[str] = field(default_factory=list)
    selectable: bool = True


SECTIONS = ("session", "turns", "history")
SECTION_LABELS = {name: f"stats.tab_{name}" for name in SECTIONS}
PERIODS: tuple[int | None, ...] = (None, 1, 7, 30)


def _section_session(snap: Snapshot, width: int, _selected: int) -> Body:
    """Компактный обзор, который не растягивается на весь широкий терминал."""
    body = Body(selectable=False)
    total_tokens = snap.billed_input + snap.output
    inner = max(12, min(width - GUTTER, 64))
    lines = _inline_metrics(
        [
            (tr("stats.metric_cost"), _money(snap.total_cost), "accent"),
            (tr("stats.metric_tokens"), format_tokens(total_tokens), ""),
            (tr("stats.tab_turns"), str(len(snap.turns)), ""),
            (tr("stats.metric_elapsed"), _dur(snap.elapsed), ""),
        ],
        inner,
    )

    lines.append(spacer())
    ratio = snap.context_used / snap.context_limit if snap.context_limit else 0.0
    lines.append(
        section(
            tr("stats.metric_context").upper(),
            right=paint(f"{ratio * 100:.0f}%", "accent", bold=True),
            width=inner,
            indent=0,
        )
    )
    lines.append(_bar(ratio, inner))
    context_free = max(0, snap.context_limit - snap.context_used)
    context_detail = tr(
        "stats.context_detail",
        used=format_tokens(snap.context_used),
        limit=format_tokens(snap.context_limit),
        free=format_tokens(context_free),
    )
    model_suffix = f"  ·  {snap.model}" if snap.model else ""
    if model_suffix and cell_width(context_detail + model_suffix) <= inner:
        context_detail += f"{DIM}  ·  {snap.model}{RESET}"
    lines.append(clip(context_detail, inner))

    lines.append(spacer())
    usage = [
        (tr("stats.col_input"), format_tokens(snap.billed_input), ""),
        (tr("stats.col_output"), format_tokens(snap.output), ""),
    ]
    if snap.cache_read:
        usage.append((tr("stats.metric_cache"), format_tokens(snap.cache_read), "success"))
    if snap.reasoning:
        usage.append((tr("stats.metric_reasoning"), format_tokens(snap.reasoning), ""))
    lines.extend(_inline_metrics(usage, inner))

    body.rows = _body_rows(lines)
    return body


def _section_turns(snap: Snapshot, width: int, selected: int) -> Body:
    body = Body()
    if not snap.turns:
        body.selectable = False
        body.head.append(section(tr("stats.no_data"), indent=GUTTER - 1))
        return body

    inner = max(20, width - GUTTER)
    show_tokens = inner >= 54
    number_width = 3
    tokens_width = 8 if show_tokens else 0
    cost_width = 9
    fixed = number_width + 1 + cost_width + (1 + tokens_width if show_tokens else 0)
    prompt_width = max(8, inner - fixed - 1)

    def line(number: str, prompt: str, tokens: str, cost: str) -> str:
        result = f"{_cell(number, number_width, True)} {_cell(prompt, prompt_width)}"
        if show_tokens:
            result += f" {_cell(tokens, tokens_width, True)}"
        return result + f" {_cell(cost, cost_width, True)}"

    body.head.append(
        " " * (GUTTER - 1)
        + paint(
            f"{tr('stats.tab_turns').capitalize()}  {DIM}{len(snap.turns)}{RESET}",
            "accent",
            bold=True,
        )
    )
    body.head.append(
        section(
            line(
                "#",
                tr("stats.col_prompt"),
                tr("stats.metric_tokens") if show_tokens else "",
                tr("stats.col_cost"),
            ),
            indent=GUTTER - 1,
        )
    )

    peak = max((turn.cost for turn in snap.turns), default=0.0)
    for turn in snap.turns:
        tokens = format_tokens(turn.input_tokens + turn.output_tokens)
        cost = _money(turn.cost)
        plain = line(str(turn.num), turn.prompt or "—", tokens, cost)
        cost_color = role_fg("accent") if peak > 0 and turn.cost >= peak * 0.75 else ""
        styled = (
            f"{DIM}{_cell(str(turn.num), number_width, True)}{RESET} "
            f"{_cell(turn.prompt or '—', prompt_width)}"
        )
        if show_tokens:
            styled += f" {DIM}{_cell(tokens, tokens_width, True)}{RESET}"
        styled += f" {cost_color}{_cell(cost, cost_width, True)}{RESET}"
        body.rows.append((plain, styled))

    turn = snap.turns[max(0, min(selected, len(snap.turns) - 1))]
    lead = " " * (GUTTER - 1) + paint("❯ ", "accent", bold=True)
    body.foot.append(lead + clip(turn.prompt or "—", width - GUTTER - 2))
    details = [
        turn.model or "—",
        time.strftime("%H:%M", time.localtime(turn.ts)),
        _dur(turn.wall),
        _plural(turn.replies, "step"),
        tr("stats.prompt_tokens", n=format_tokens(turn.prompt_tokens)),
        f"↓{format_tokens(turn.output_tokens)}",
    ]
    if turn.cache_read:
        details.append(tr("stats.cached_tokens", n=format_tokens(turn.cache_read)))
    body.foot.append(
        section(clip(" · ".join(details), width - GUTTER - 2), indent=GUTTER + 1)
    )
    return body


def _section_history(
    _snap: Snapshot,
    width: int,
    selected: int,
    period: int | None,
    stats: dict,
) -> Body:
    body = Body()
    title = (
        tr("stats.overall")
        if period is None
        else tr("stats.last_n_days", n=period, s="" if period == 1 else "s")
    )
    if not stats or not stats.get("total_sessions"):
        body.selectable = False
        body.head.append(section(f"{title} · {tr('stats.no_data')}", indent=GUTTER - 1))
        return body

    total_tokens = int(stats["total_input_tokens"]) + int(stats["total_output_tokens"])
    body.head.append(" " * (GUTTER - 1) + paint(title, "accent", bold=True))
    body.head.extend(
        " " * (GUTTER - 1) + line
        for line in _metric_grid(
            [
                (tr("stats.metric_cost"), _money(stats["total_cost"]), "accent"),
                (tr("stats.metric_tokens"), format_tokens(total_tokens), ""),
                (tr("stats.col_sessions"), str(stats["total_sessions"]), ""),
                (tr("stats.col_msgs"), str(stats["total_messages"]), ""),
            ],
            width,
            max_columns=4,
        )
    )
    body.head.append(spacer())

    items = sorted(
        stats["by_model"].items(),
        key=lambda item: (
            -float(item[1].get("cost") or 0),
            -(
                int(item[1].get("input_tokens") or 0)
                + int(item[1].get("output_tokens") or 0)
            ),
            item[0].casefold(),
        ),
    )
    if not items:
        body.selectable = False
        return body
    total_cost = sum(float(data.get("cost") or 0) for _name, data in items) or 1.0
    inner = max(20, width - GUTTER)
    show_bar = inner >= 58
    show_share = inner >= 38
    bar_width = 10 if inner < 78 else 14
    tokens_width = 8
    cost_width = 8
    percent_width = 4
    fixed = 1 + tokens_width + 1 + cost_width
    if show_bar:
        fixed += bar_width + 1
    if show_share:
        fixed += percent_width + 1
    name_width = max(6, min(NAME_CAP, inner - fixed))

    def line(name: str, tokens: str, cost: str, bar: str, percent: str) -> str:
        result = (
            f"{_cell(name, name_width)} {_cell(tokens, tokens_width, True)}"
            f" {_cell(cost, cost_width, True)}"
        )
        if show_bar:
            result += f" {_cell(bar, bar_width)}"
        if show_share:
            result += f" {_cell(percent, percent_width, True)}"
        return result

    body.head.append(
        section(
            line(
                tr("stats.col_model"),
                tr("stats.metric_tokens"),
                tr("stats.col_cost"),
                tr("stats.col_share") if show_bar else "",
                "",
            ),
            indent=GUTTER - 1,
        )
    )

    for name, data in items:
        model_tokens = int(data.get("input_tokens") or 0) + int(
            data.get("output_tokens") or 0
        )
        cost_value = float(data.get("cost") or 0.0)
        share = cost_value / total_cost
        tokens = format_tokens(model_tokens)
        cost = _money(cost_value)
        percent = f"{share * 100:.0f}%"
        plain = line(name, tokens, cost, _bar_plain(share, bar_width), percent)
        styled = (
            f"{_cell(name, name_width)} {DIM}{_cell(tokens, tokens_width, True)}{RESET}"
            f" {role_fg('accent')}{_cell(cost, cost_width, True)}{RESET}"
        )
        if show_bar:
            styled += f" {_bar(share, bar_width)}"
        if show_share:
            styled += f" {DIM}{_cell(percent, percent_width, True)}{RESET}"
        body.rows.append((plain, styled))

    _name, data = items[max(0, min(selected, len(items) - 1))]
    details = " · ".join(
        (
            _plural(int(data.get("sessions") or 0), "session"),
            _plural(int(data.get("messages") or 0), "message"),
            f"↑{format_tokens(int(data.get('input_tokens') or 0))}",
            f"↓{format_tokens(int(data.get('output_tokens') or 0))}",
        )
    )
    body.foot.append(section(clip(details, width - GUTTER), indent=GUTTER - 1))
    return body


class StatsOverlay(Overlay):
    """Три уровня статистики в нижней зоне Shell."""

    def __init__(self, session: Session, period: int | None = None) -> None:
        super().__init__()
        self.snap = collect(session)
        self.periods: list[int | None] = list(PERIODS)
        if period is not None and period not in self.periods:
            self.periods.insert(1, period)
        self.period = period
        self._stats_cache: dict[int | None, dict] = {}
        start = "history" if (period is not None or not self.snap.turns) else "session"
        self.section = SECTIONS.index(start)
        self.sel = dict.fromkeys(SECTIONS, 0)
        self._page = 1
        self._cache_key: tuple | None = None
        self._cache_text = ""

    def _stats(self) -> dict:
        if self.period not in self._stats_cache:
            try:
                self._stats_cache[self.period] = storage.get_statistics(days=self.period)
            except Exception:
                logger.warning("stats: get_statistics failed", exc_info=True)
                self._stats_cache[self.period] = {}
        return self._stats_cache[self.period]

    def _body(self, width: int) -> Body:
        name = SECTIONS[self.section]
        selected = self.sel[name]
        if name == "session":
            return _section_session(self.snap, width, selected)
        if name == "turns":
            return _section_turns(self.snap, width, selected)
        return _section_history(self.snap, width, selected, self.period, self._stats())

    def _tabs(self, width: int) -> str:
        selected_bg = role_bg("bg_select")
        title = paint(tr("stats.title"), "accent", bold=True)
        labels = [tr(SECTION_LABELS[name]) for name in SECTIONS]
        cells = [
            (
                f"{selected_bg}{BOLD} {label} {RESET}"
                if index == self.section
                else f"{DIM} {label} {RESET}"
            )
            for index, label in enumerate(labels)
        ]
        navigation = "  ".join(cells)
        line = f"  {title}   {navigation}"
        return clip(line, max(1, width - 1))

    def render(self, width: int) -> str:
        name = SECTIONS[self.section]
        key = (width, self.section, self.sel[name], self.period)
        if key != self._cache_key:
            self._cache_key = key
            self._cache_text = self._render(width)
        return self._cache_text

    def version(self):
        name = SECTIONS[self.section]
        return self.section, self.sel[name], self.period

    def _render(self, width: int) -> str:
        budget = 20
        if self.shell is not None:
            try:
                budget = self.shell.overlay_budget()
            except Exception:
                logger.debug("overlay_budget failed", exc_info=True)
        body = self._body(width)
        name = SECTIONS[self.section]

        available = max(1, budget - 2 - len(body.head) - len(body.foot))
        if body.rows and available < 3 and body.foot:
            body.foot = []
            available = max(1, budget - 2 - len(body.head))
        self._page = max(1, available - 1)

        selected = max(0, min(self.sel[name], len(body.rows) - 1)) if body.rows else 0
        self.sel[name] = selected
        start, end, above, below = scroll_window(len(body.rows), selected, available)

        output = [self._tabs(width), spacer(), *body.head]
        if above:
            output.append(more_note(above, up=True))
        for index in range(start, end):
            plain, styled = body.rows[index]
            picked = index == selected and body.selectable
            output.append(row(plain if picked else styled, selected=picked, width=width))
        if below:
            output.append(more_note(below, up=False))
        output.extend(body.foot)
        return "\n".join(output[: max(1, budget)])

    def hint(self) -> str:
        name = SECTIONS[self.section]
        pairs = [("←→", tr("stats.hint_section"))]
        if name == "history":
            pairs.extend(
                (("↑↓", tr("stats.hint_row")), ("p", tr("stats.hint_period")))
            )
        elif name == "turns":
            pairs.extend(
                (("↑↓", tr("stats.hint_row")), ("pgup/pgdn", tr("stats.hint_page")))
            )
        pairs.append(("esc", tr("stats.hint_close")))
        return key_hints(*pairs)

    def _move(self, delta: int) -> None:
        name = SECTIONS[self.section]
        self.sel[name] = max(0, self.sel[name] + delta)

    def handle_key(self, key: str, event) -> bool:
        if key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
            return True
        if key in ("left", "h"):
            self.section = (self.section - 1) % len(SECTIONS)
        elif key in ("right", "l", "tab"):
            self.section = (self.section + 1) % len(SECTIONS)
        elif key in ("up", "k"):
            self._move(-1)
        elif key in ("down", "j"):
            self._move(1)
        elif key == "pageup":
            self._move(-self._page)
        elif key == "pagedown":
            self._move(self._page)
        elif key == "home":
            self.sel[SECTIONS[self.section]] = 0
        elif key == "end":
            self._move(10_000)
        elif key in ("p", "P") and SECTIONS[self.section] == "history":
            index = self.periods.index(self.period) if self.period in self.periods else 0
            self.period = self.periods[(index + 1) % len(self.periods)]
            self.sel["history"] = 0
        elif len(key) == 1 and key.isdigit() and 1 <= int(key) <= len(SECTIONS):
            self.section = int(key) - 1
        self._cache_key = None
        return True


def _flat(body: Body) -> list[str]:
    prefix = " " * (GUTTER - 1)
    return [
        *body.head,
        *(prefix + styled if styled else "" for _plain, styled in body.rows),
        *body.foot,
    ]


def _static_summary(session: Session, period: int | None) -> str:
    """Короткий headless-свод без попытки напечатать все интерактивные детали."""
    snap = collect(session)
    width = 78
    try:
        stats = storage.get_statistics(days=period)
    except Exception:
        logger.warning("stats: get_statistics failed", exc_info=True)
        stats = {}

    lines: list[str] = []
    if snap.turns and period is None:
        lines.extend(_flat(_section_session(snap, width, 0)))
        lines.append("")
    lines.extend(_flat(_section_history(snap, width, 0, period, stats)))
    return "\n".join(lines)


async def stats_interactive(session: Session, period: int | None = None) -> None:
    """Открыть интерактивную статистику. Без Shell вывести короткий свод."""
    shell = get_shell()
    if shell is None:
        from rich.console import Console
        from rich.text import Text

        Console().print(Text.from_ansi(_static_summary(session, period)))
        return
    await shell.run_overlay(StatsOverlay(session, period))


__all__ = ["StatsOverlay", "collect", "stats_interactive"]
