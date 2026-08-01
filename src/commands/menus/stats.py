"""Интерактивная статистика — оверлей команды /stats.

Прежний /stats печатал одну таблицу «модель × токены × цена» по всем сессиям.
Она отвечала ровно на один вопрос — «сколько потрачено вообще» — и ни на один
из тех, что возникают во время работы: во что обошёлся текущий диалог, куда
девается контекстное окно, какой ход был дорогим, что делали инструменты.

Пять разделов внутри одного оверлея:

    session  — текущий диалог одним экраном (деньги, окно, темп)
    turns    — список ходов, деталь по выбранному
    models   — во что обошлась каждая модель в этом диалоге
    tools    — вызовы инструментов
    history  — прежний общий свод по всем сессиям (сюда уехал аргумент [N])

Вёрстка целиком собрана из общих кирпичей `ui.overlays` (`row`, `section`,
`scroll_window`, `key_hints`, `pad`/`clip`), поэтому виджет выглядит и ведёт
себя как остальные списки нижней зоны: ни рамок, ни таблиц, ни вертикальных
линий — только колонки из пробелов и полоса `bg_select` под курсором.

Почему такие числа, а не другие
-------------------------------
Достоверны только те величины, которые пришли от провайдера или посчитаны из
его ответов. Поэтому:

* `usage.input` каждого ответа — реальный размер промпта на тот момент. Из ряда
  этих значений строится и спарклайн роста контекста, и прогноз «на сколько
  ходов хватит окна». Это единственный честный источник скорости заполнения.
* `Message.duration` НЕ используется: в него никто ничего не пишет, он всегда
  0.0. Длительность хода считается по разнице timestamp'ов user → последний
  assistant этого хода.
* Экономия от кэша не показывается деньгами: `Session._compute_cost` намеренно
  тарифицирует весь input по полной цене, и «сэкономлено $X» противоречило бы
  сумме, которую пользователь видит тут же рядом. Кэш показан долей токенов.
* Токены отдельных user-сообщений не показываются: `_reconcile_input_tokens`
  задним числом масштабирует их под реальный prompt_tokens, поэтому в отрыве от
  суммы такое число смысла не имеет.
"""

from __future__ import annotations

import re
import textwrap
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
)
from ui.shell import Overlay, get_shell

#: Ширина «служебного» поля строки списка: отступ виджета плюс место под курсор.
#: `ui.overlays.row` съедает ровно столько, поэтому колонки считаем от неё.
GUTTER = 5


def _cell(s: str, width: int, right: bool = False) -> str:
    """Колонка фиксированной ширины: обрезать и добить пробелами."""
    return pad(clip(s, width), width, "right" if right else "left")


def _bar_plain(ratio: float, width: int) -> str:
    """Бар без цвета — для выделенной строки, где идёт сплошной фон bg_select."""
    ratio = 0.0 if ratio < 0 else min(ratio, 1.0)
    filled = round(width * ratio)
    if ratio > 0 and filled == 0:
        filled = 1
    return "█" * filled + "░" * (width - filled)


def _bar(ratio: float, width: int) -> str:
    """Плотный бар из блоков. Не рамка: заливка, а не линия."""
    plain = _bar_plain(ratio, width)
    filled = plain.count("█")
    return (f"{role_fg('bar_filled')}{'█' * filled}{RESET}"
            f"{DIM}{'░' * (width - filled)}{RESET}")


_SPARK = "▁▂▃▄▅▆▇█"


def _spark(values: list[float], width: int) -> str:
    """Спарклайн. Длинный ряд усредняется по корзинам, чтобы влезть в width."""
    vals = [float(v) for v in values if v is not None]
    if not vals or width <= 0:
        return ""
    if len(vals) > width:
        bucket = len(vals) / width
        packed = []
        for i in range(width):
            lo_i = int(i * bucket)
            hi_i = max(int((i + 1) * bucket), lo_i + 1)
            chunk = vals[lo_i:hi_i]
            packed.append(sum(chunk) / len(chunk))
        vals = packed
    lo, hi = min(vals), max(vals)
    span = hi - lo
    if span <= 0:
        # Ровный ряд рисуем средней высотой: нулевая полоса выглядела бы как
        # «данных нет», хотя данные есть и они просто одинаковые.
        return "▄" * len(vals)
    return "".join(_SPARK[min(7, int((v - lo) / span * 7.999))] for v in vals)


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
    s = max(0.0, float(seconds))
    if s < 1:
        # Инструменты часто отрабатывают за миллисекунды: «0.0s» выглядело бы
        # как «времени нет», хотя оно измерено.
        return f"{s * 1000:.0f}{tr('stats.unit_ms')}"
    if s < 10:
        return f"{s:.1f}{tr('stats.unit_s')}"
    if s < 60:
        return f"{int(s)}{tr('stats.unit_s')}"
    if s < 3600:
        return (f"{int(s) // 60}{tr('stats.unit_min')} "
                f"{int(s) % 60:02d}{tr('stats.unit_s')}")
    return (f"{int(s) // 3600}{tr('stats.unit_h')} "
            f"{(int(s) % 3600) // 60:02d}{tr('stats.unit_min')}")


def _dur_short(seconds: float) -> str:
    """То же, но под колонку в шесть ячеек."""
    s = max(0.0, float(seconds))
    if s < 10:
        return f"{s:.1f}{tr('stats.unit_s')}"
    if s < 100:
        return f"{int(s)}{tr('stats.unit_s')}"
    if s < 3600:
        return (f"{int(s) // 60}{tr('stats.unit_min')}"
                f"{int(s) % 60:02d}{tr('stats.unit_s')}")
    return (f"{int(s) // 3600}{tr('stats.unit_h')}"
            f"{(int(s) % 3600) // 60:02d}{tr('stats.unit_min')}")


def _money(cost: float) -> str:
    """`format_cost` для ровного нуля даёт «$0.000000» — шесть нулей в колонке
    читаются как сбой. Бесплатный ход честнее показать просто «$0»."""
    return "$0" if not cost else format_cost(cost)


def _count(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.1f}M"


# ────────────────────────────── сбор данных ─────────────────────────────────
@dataclass
class Turn:
    """Один ход: user-реплика и все ответы модели до следующей user-реплики."""
    num: int
    ts: float
    prompt: str
    model: str
    prompt_tokens: int      # usage.input последнего ответа хода — размер контекста
    input_tokens: int       # сумма usage.input всех ответов хода — это и оплачено
    output_tokens: int
    reasoning: int
    cache_read: int
    cost: float
    wall: float
    replies: int
    tools: list[str] = field(default_factory=list)


@dataclass
class ToolStat:
    name: str
    calls: int = 0
    ok: int = 0
    fail: int = 0
    elapsed: float = 0.0
    timed: bool = False     # есть ли достоверное время выполнения


@dataclass
class Snapshot:
    """Всё, что показывает оверлей, посчитано один раз при открытии.

    Пересчитывать на каждом кадре нельзя: Shell дёргает render() дважды за кадр
    (лямбда высоты и контрол содержимого), а обход истории и чтение summary.json
    всех сессий — не то, что стоит делать 20 раз в секунду.
    """
    title: str = ""
    model: str = ""
    context_used: int = 0
    context_limit: int = 200_000
    billed_input: int = 0
    output: int = 0
    reasoning: int = 0
    cache_read: int = 0
    total_cost: float = 0.0
    input_cost: float = 0.0
    output_cost: float = 0.0
    compressed_cost: float = 0.0
    elapsed: float = 0.0
    busy: float = 0.0
    turns: list[Turn] = field(default_factory=list)
    by_model: dict = field(default_factory=dict)
    tools: list[ToolStat] = field(default_factory=list)
    tools_source: str = ""


_CALL_RE = re.compile(r"^[ \t]*:{2,3}call[ \t]+(\w+)", re.M)
_CONTROL_TOOLS = ("think", "plan")


def _usage_int(msg, key: str) -> int:
    u = msg.usage if isinstance(msg.usage, dict) else None
    if not u:
        return 0
    try:
        return int(u.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _msg_cost(msg, input_buffer: list[int]) -> tuple[float, int, int]:
    """Стоимость одного ответа по тем же правилам, что и Session._compute_cost:
    usage провайдера в приоритете, эвристика — только когда usage не пришёл."""
    price_in, price_out = app_models.get_pricing(msg.model or "unknown")
    inp = _usage_int(msg, "input")
    out = _usage_int(msg, "output")
    if not inp and not out:
        inp = sum(input_buffer)
        out = msg.tokens
    elif not out:
        out = msg.tokens
    return (inp * price_in / 1_000_000 + out * price_out / 1_000_000, inp, out)


def _collect_turns(session: Session) -> list[Turn]:
    turns: list[Turn] = []
    cur: Turn | None = None
    buf: list[int] = []
    for msg in session.messages:
        if msg.role == "user":
            cur = Turn(
                num=len(turns) + 1, ts=msg.timestamp,
                prompt=" ".join((msg.content or "").split()),
                model="", prompt_tokens=0, input_tokens=0, output_tokens=0,
                reasoning=0, cache_read=0, cost=0.0, wall=0.0, replies=0,
            )
            turns.append(cur)
            buf = [msg.tokens]
            continue
        if msg.role in ("system", "tool_result"):
            buf.append(msg.tokens)
            if cur is not None and msg.role == "tool_result":
                cur.tools.extend(_parse_calls(msg.content))
            continue
        if msg.role != "assistant" or cur is None:
            continue
        cost, inp, out = _msg_cost(msg, buf)
        buf = []
        cur.replies += 1
        cur.cost += cost
        cur.input_tokens += inp
        cur.output_tokens += out
        cur.prompt_tokens = max(cur.prompt_tokens, inp)
        cur.reasoning += _usage_int(msg, "reasoning")
        cur.cache_read += _usage_int(msg, "cache_read")
        cur.model = msg.model or cur.model
        cur.wall = max(0.0, msg.timestamp - cur.ts)
        cur.tools.extend(_parse_calls(msg.content))
    return turns


def _parse_calls(content: str) -> list[str]:
    """Имена инструментов из текстовых блоков `:::call <tool>`.

    Живёт только в режиме tool_format=text: нативные function calls в истории
    сессии не сохраняются вообще (см. agent/loop.py — источник истины для них
    структурные ToolMessage в ApiSession, а не наша история).
    """
    if not content or "::call" not in content:
        return []
    return [n for n in _CALL_RE.findall(content) if n not in _CONTROL_TOOLS]


def _live_tools(session: Session) -> list[ToolStat]:
    """Вызовы инструментов из RenderStore текущего процесса.

    RenderStore не сбрасывается при /new и не сохраняется на диск, поэтому берём
    только события, попавшие во временное окно самой сессии: от первого её
    сообщения до последнего. Для догруженной с диска сессии окно в прошлом —
    сегодняшние события в него не попадут, и раздел честно скажет «нет данных»
    вместо чужих чисел.
    """
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
    except Exception:
        logger.debug("stats: render store unavailable", exc_info=True)
        return []
    store = getattr(ctx, "render_store", None) if ctx is not None else None
    if store is None or not getattr(store, "items", None) or not session.messages:
        return []
    lo = session.messages[0].timestamp
    hi = max(session.updated_at, session.messages[-1].timestamp)
    acc: dict[str, ToolStat] = {}
    for item in store.items:
        if item.kind not in ("tool", "command_only") or not (lo <= item.ts <= hi):
            continue
        call = item.payload.get("call") or {}
        name = call.get("tool_name") or ""
        if not name or name in _CONTROL_TOOLS:
            continue
        st = acc.get(name)
        if st is None:
            st = acc[name] = ToolStat(name=name)
        st.calls += 1
        result = item.payload.get("result") or {}
        if result:
            if str(result.get("status", "ok")) == "ok":
                st.ok += 1
            else:
                st.fail += 1
            try:
                elapsed = float(result.get("elapsed") or 0.0)
            except (TypeError, ValueError):
                elapsed = 0.0
            if elapsed > 0:
                st.elapsed += elapsed
                st.timed = True
    return sorted(acc.values(), key=lambda s: (-s.calls, s.name))


def collect(session: Session) -> Snapshot:
    snap = Snapshot()
    snap.title = session.title or session.id
    snap.model = session.last_model or ""
    snap.context_used = session.context_tokens
    snap.context_limit = app_models.get_context_limit(snap.model) or 200_000
    snap.turns = _collect_turns(session)

    summary = session.summary()
    snap.by_model = summary.get("cost_by_model") or {}
    snap.total_cost = float(summary.get("total_cost") or 0.0)
    for data in snap.by_model.values():
        snap.input_cost += float(data.get("input_cost") or 0.0)
        snap.output_cost += float(data.get("output_cost") or 0.0)
    stats = session._compressed_stats or {}
    snap.compressed_cost = float(stats.get("total_cost") or 0.0)

    snap.billed_input = sum(tn.input_tokens for tn in snap.turns)
    snap.output = sum(tn.output_tokens for tn in snap.turns)
    snap.reasoning = sum(tn.reasoning for tn in snap.turns)
    snap.cache_read = sum(tn.cache_read for tn in snap.turns)
    snap.busy = sum(tn.wall for tn in snap.turns)
    snap.elapsed = max(0.0, session.updated_at - session.created_at)

    live = _live_tools(session)
    if live:
        snap.tools, snap.tools_source = live, "run"
    else:
        acc: dict[str, ToolStat] = {}
        for tn in snap.turns:
            for name in tn.tools:
                st = acc.get(name) or acc.setdefault(name, ToolStat(name=name))
                st.calls += 1
        snap.tools = sorted(acc.values(), key=lambda s: (-s.calls, s.name))
        snap.tools_source = "history" if snap.tools else ""
    return snap


# ────────────────────────────── строки разделов ─────────────────────────────
def _kv(label: str, value: str, width: int, lw: int = 12) -> str:
    """«Подпись → значение»: выравнивание пробелами, без разделителей."""
    return f"{DIM}{_cell(label, lw)}{RESET}{clip(value, max(0, width - GUTTER - lw))}"


@dataclass
class Body:
    """Раздел = неподвижная шапка, прокручиваемые строки, неподвижный подвал.

    Строки хранятся парой (plain, styled): под курсором `ui.overlays.row`
    заливает всю строку фоном, и собственные цвета колонок там только мешают —
    отдаём ему чистый текст, а расцвеченный вариант оставляем остальным.
    """
    head: list[str] = field(default_factory=list)
    rows: list[tuple[str, str]] = field(default_factory=list)
    foot: list[str] = field(default_factory=list)
    selectable: bool = True


# ────────────────────────────────── разделы ─────────────────────────────────
SECTIONS = ("session", "turns", "models", "tools", "history")
SECTION_LABELS = {name: f"stats.tab_{name}" for name in SECTIONS}
PERIODS: tuple[int | None, ...] = (None, 1, 7, 30)

#: Шире этого имя модели/инструмента не растягиваем: числа должны стоять рядом
#: с названием, а не улетать к правому краю широкого терминала.
NAME_CAP = 34


def _section_session(snap: Snapshot, width: int, _sel: int) -> Body:
    """Диалог одним экраном: прокручиваемый, но невыделяемый список строк."""
    b = Body(selectable=False)
    inner = max(20, width - GUTTER - 13)
    out: list[str] = []

    money = paint(_money(snap.total_cost), "accent", bold=True)
    tail = f"{DIM}  {tr('stats.cost_io', input=_money(snap.input_cost), output=_money(snap.output_cost))}"
    if snap.compressed_cost > 0:
        tail += f" · {tr('stats.compressed_cost', cost=_money(snap.compressed_cost))}"
    out.append(_kv(tr("stats.metric_cost"), money + tail + RESET, width))

    tok = tr("stats.tokens_io", input=format_tokens(snap.billed_input),
             output=format_tokens(snap.output))
    if snap.reasoning:
        tok += f"{DIM} · {tr('stats.reasoning_tokens', n=format_tokens(snap.reasoning))}{RESET}"
    out.append(_kv(tr("stats.metric_tokens"), tok, width))

    if snap.cache_read:
        share = snap.cache_read / snap.billed_input if snap.billed_input else 0.0
        out.append(_kv(tr("stats.metric_cache"),
                       tr("stats.cache_detail", n=format_tokens(snap.cache_read),
                          pct=f"{share * 100:.0f}"), width))

    ratio = snap.context_used / snap.context_limit if snap.context_limit else 0.0
    out.append(_kv(tr("stats.metric_context"),
                   f"{_bar(ratio, 24 if inner >= 56 else 14)}"
                   f"  {format_tokens(snap.context_used)}"
                   f" / {format_tokens(snap.context_limit)}"
                   f"{DIM}  {ratio * 100:.0f}%{RESET}", width))

    # Ряд usage.input — единственный достоверный след того, как рос промпт:
    # это числа самого провайдера, а не наша оценка токенов.
    series = [tn.prompt_tokens for tn in snap.turns if tn.prompt_tokens > 0]
    if len(series) >= 2:
        growth = (series[-1] - series[0]) / (len(series) - 1)
        if growth > 0:
            left = max(0, int((snap.context_limit - snap.context_used) / growth))
            out.append(_kv(tr("stats.metric_headroom"),
                           tr("stats.headroom_detail", turns=_count(left),
                              tokens=_count(int(growth))), width))

    pace = _plural(len(snap.turns), "turn") + " · " + tr(
        "stats.elapsed", duration=_dur(snap.elapsed))
    if snap.busy > 0:
        pace += f"{DIM} · {tr('stats.busy_detail', busy=_dur(snap.busy), slowest=_dur(max(tn.wall for tn in snap.turns)))}{RESET}"
    out.append(_kv(tr("stats.metric_pace"), pace, width))

    if len(snap.turns) >= 2:
        out.append(spacer())
        spark_w = min(24, max(6, inner - 30))
        costs = [tn.cost for tn in snap.turns]
        out.append(_kv(tr("stats.metric_cost_turn"),
                       f"{role_fg('accent')}{_spark(costs, spark_w)}{RESET}"
                       f"{DIM}  {tr('stats.avg_max', avg=_money(sum(costs) / len(costs)), maximum=_money(max(costs)))}{RESET}", width))
        if len(series) >= 2:
            grow = snap.billed_input / snap.context_used if snap.context_used else 0.0
            out.append(_kv(tr("stats.metric_prompt_size"),
                           f"{role_fg('accent')}{_spark(series, spark_w)}{RESET}"
                           f"{DIM}  {format_tokens(series[0])} → {format_tokens(series[-1])}"
                           f" · {tr('stats.chat_resent', factor=f'{grow:.1f}')}{RESET}", width))

    out.append(spacer())
    names = list(snap.by_model.keys())
    out.append(_kv(tr("stats.metric_models"),
                   ", ".join(names) if names else f"{DIM}—{RESET}", width))
    if snap.tools:
        brief = " · ".join(f"{s.name} ×{s.calls}" for s in snap.tools[:4])
        total = sum(s.calls for s in snap.tools)
        out.append(_kv(tr("stats.metric_tools"), f"{_plural(total, 'call')}"
                                f"{DIM} · {brief}{RESET}", width))
    else:
        out.append(_kv(tr("stats.metric_tools"),
                       f"{DIM}{tr('stats.none_recorded')}{RESET}", width))

    b.rows = [(line, line) for line in out]
    return b


def _section_turns(snap: Snapshot, width: int, sel: int) -> Body:
    b = Body()
    if not snap.turns:
        b.selectable = False
        b.head.append(section(tr("stats.no_data"), indent=GUTTER - 1))
        return b
    fixed = 3 + 1 + 5 + 1 + 6 + 1 + 7 + 1 + 6 + 1 + 9
    prompt_w = width - GUTTER - fixed - 1
    show_prompt = prompt_w >= 12

    def line(num, tm, took, up, down, cost, prompt) -> str:
        cells = [_cell(num, 3, True), _cell(tm, 5), _cell(took, 6, True),
                 _cell(up, 7, True), _cell(down, 6, True), _cell(cost, 9, True)]
        out = " ".join(cells)
        return f"{out} {_cell(prompt, prompt_w)}" if show_prompt else out

    b.head.append(section(line("#", tr("stats.col_time"), tr("stats.col_duration"),
                               "↑" + tr("stats.col_input"), "↓" + tr("stats.col_output"),
                               tr("stats.col_cost"), tr("stats.col_prompt")),
                          indent=GUTTER - 1))
    peak = max((tn.cost for tn in snap.turns), default=0.0)
    for tn in snap.turns:
        tm = time.strftime("%H:%M", time.localtime(tn.ts))
        took, up = _dur_short(tn.wall), format_tokens(tn.input_tokens)
        down, cost = format_tokens(tn.output_tokens), _money(tn.cost)
        b.rows.append((
            line(str(tn.num), tm, took, up, down, cost, tn.prompt),
            # Дорогие ходы подсвечиваем акцентом: их и ищут глазами в первую очередь.
            f"{DIM}{_cell(str(tn.num), 3, True)} {_cell(tm, 5)}{RESET}"
            f" {_cell(took, 6, True)} {_cell(up, 7, True)} {_cell(down, 6, True)}"
            f" {role_fg('accent') if peak > 0 and tn.cost >= peak * 0.75 else ''}"
            f"{_cell(cost, 9, True)}{RESET}"
            + (f" {DIM}{_cell(tn.prompt, prompt_w)}{RESET}" if show_prompt else ""),
        ))

    tn = snap.turns[max(0, min(sel, len(snap.turns) - 1))]
    lead = " " * (GUTTER - 1) + paint("❯ ", "accent", bold=True)
    b.foot.append(lead + clip(tn.prompt or "—", width - GUTTER - 2))
    bits = [tn.model or "—", time.strftime("%H:%M:%S", time.localtime(tn.ts)),
            _plural(tn.replies, "step"),
            tr("stats.prompt_tokens", n=format_tokens(tn.prompt_tokens)),
            tr("stats.billed_tokens", n=format_tokens(tn.input_tokens)),
            f"↓{format_tokens(tn.output_tokens)}"]
    if tn.reasoning:
        bits.append(tr("stats.reasoning_tokens", n=format_tokens(tn.reasoning)))
    if tn.cache_read:
        bits.append(tr("stats.cached_tokens", n=format_tokens(tn.cache_read)))
    if tn.tools:
        bits.append(" ".join(sorted(set(tn.tools))))
    bits.append(_money(tn.cost))
    b.foot.append(section(clip(" · ".join(bits), width - GUTTER - 2), indent=GUTTER + 1))
    return b


def _section_models(snap: Snapshot, width: int, sel: int) -> Body:
    b = Body()
    if not snap.by_model:
        b.selectable = False
        b.head.append(section(tr("stats.no_data"), indent=GUTTER - 1))
        return b
    inner = width - GUTTER
    bar_w = 10 if inner >= 66 else 6
    fixed = 8 + 1 + 7 + 1 + 9 + 1 + bar_w + 1 + 4
    name_w = max(10, min(NAME_CAP, inner - fixed - 1))
    items = sorted(snap.by_model.items(), key=lambda kv: -float(kv[1].get("total_cost") or 0))
    total = sum(float(d.get("total_cost") or 0.0) for _m, d in items) or 1.0

    def line(name, up, down, cost, bar, pct) -> str:
        return (f"{_cell(name, name_w)} {_cell(up, 8, True)} {_cell(down, 7, True)}"
                f" {_cell(cost, 9, True)} {bar} {_cell(pct, 4, True)}")

    b.head.append(section(line(tr("stats.col_model"), tr("stats.col_input"),
                               tr("stats.col_output"), tr("stats.col_cost"),
                               _cell(tr("stats.col_share"), bar_w), ""), indent=GUTTER - 1))
    for name, data in items:
        cost = float(data.get("total_cost") or 0.0)
        share = cost / total
        up = format_tokens(int(data.get("input_tokens") or 0))
        down = format_tokens(int(data.get("output_tokens") or 0))
        pct = f"{share * 100:.0f}%"
        b.rows.append((
            line(name, up, down, _money(cost), _bar_plain(share, bar_w), pct),
            f"{_cell(name, name_w)} {DIM}{_cell(up, 8, True)} {_cell(down, 7, True)}{RESET}"
            f" {_cell(_money(cost), 9, True)} {_bar(share, bar_w)}"
            f" {DIM}{_cell(pct, 4, True)}{RESET}",
        ))

    name, data = items[max(0, min(sel, len(items) - 1))]
    price_in, price_out = app_models.get_pricing(name)
    limit = app_models.get_context_limit(name)
    b.foot.append(section(clip(
        f"{name} · {tr('stats.price_in', price=f'${price_in:.2f}')}"
        f" · {tr('stats.price_out', price=f'${price_out:.2f}')}"
        f" · {tr('menu.col_context')} {format_tokens(limit)}",
        width - GUTTER), indent=GUTTER - 1))
    icost = float(data.get("input_cost") or 0.0)
    ocost = float(data.get("output_cost") or 0.0)
    both = icost + ocost or 1.0
    b.foot.append(section(clip(
        tr("stats.io_cost_share", input=_money(icost), input_pct=f"{icost / both * 100:.0f}",
           output=_money(ocost), output_pct=f"{ocost / both * 100:.0f}"),
        width - GUTTER), indent=GUTTER - 1))
    return b


def _section_tools(snap: Snapshot, width: int, _sel: int) -> Body:
    b = Body()
    if not snap.tools:
        b.selectable = False
        b.head.append(section(tr("stats.no_data"), indent=GUTTER - 1))
        # Пусто здесь — не «инструментов не было», а свойство формата вызовов:
        # честнее объяснить, чем показать ноль как факт.
        for part in textwrap.wrap(tr("stats.tools_not_saved"), width=max(20, width - GUTTER)):
            b.head.append(section(clip(part, width - GUTTER), indent=GUTTER - 1))
        return b
    inner = width - GUTTER
    bar_w = 10 if inner >= 60 else 6
    fixed = 6 + 1 + 4 + 1 + 5 + 1 + 8 + 1 + bar_w
    name_w = max(10, min(NAME_CAP, inner - fixed - 1))
    total = sum(s.calls for s in snap.tools) or 1

    def line(name, calls, ok, fail, took, bar) -> str:
        return (f"{_cell(name, name_w)} {_cell(calls, 6, True)} {_cell(ok, 4, True)}"
                f" {_cell(fail, 5, True)} {_cell(took, 8, True)} {bar}")

    b.head.append(section(line(tr("stats.col_tool"), tr("stats.col_calls"),
                               tr("stats.col_ok"), tr("stats.col_fail"),
                               tr("stats.col_time"), _cell(tr("stats.col_share"), bar_w)),
                          indent=GUTTER - 1))
    for st in snap.tools:
        took = _dur(st.elapsed) if st.timed else "—"
        ok, fail = (str(st.ok) if st.ok else ""), (str(st.fail) if st.fail else "")
        b.rows.append((
            line(st.name, str(st.calls), ok, fail, took, _bar_plain(st.calls / total, bar_w)),
            f"{_cell(st.name, name_w)} {_cell(str(st.calls), 6, True)}"
            f" {role_fg('success')}{_cell(ok, 4, True)}{RESET}"
            f" {role_fg('error')}{_cell(fail, 5, True)}{RESET}"
            f" {DIM}{_cell(took, 8, True)}{RESET} {_bar(st.calls / total, bar_w)}",
        ))
    origin = tr("stats.tools_origin_run" if snap.tools_source == "run"
                else "stats.tools_origin_history")
    b.foot.append(section(f"{_plural(total, 'call')} · {origin}", indent=GUTTER - 1))
    return b


def _section_history(_snap: Snapshot, width: int, _sel: int, period: int | None,
                     stats: dict) -> Body:
    b = Body()
    title = (tr("stats.overall") if period is None
             else tr("stats.last_n_days", n=period, s="" if period == 1 else "s"))
    if not stats or not stats.get("total_sessions"):
        b.selectable = False
        b.head.append(section(f"{title} · {tr('stats.no_data')}", indent=GUTTER - 1))
        return b
    inner = width - GUTTER
    fixed = 5 + 1 + 6 + 1 + 8 + 1 + 7 + 1 + 9
    name_w = max(10, min(NAME_CAP, inner - fixed - 1))

    def line(name, sess, msgs, up, down, cost) -> str:
        return (f"{_cell(name, name_w)} {_cell(sess, 5, True)} {_cell(msgs, 6, True)}"
                f" {_cell(up, 8, True)} {_cell(down, 7, True)} {_cell(cost, 9, True)}")

    tok = stats["total_input_tokens"] + stats["total_output_tokens"]
    b.head.append(" " * (GUTTER - 1) + paint(clip(title, width - GUTTER), "accent", bold=True))
    b.head.append(" " * (GUTTER - 1) + _kv(
        tr("stats.total"),
        f"{BOLD}{_money(stats['total_cost'])}{RESET}"
        f"{DIM}  {_plural(stats['total_sessions'], 'session')}"
        f" · {_plural(stats['total_messages'], 'message')}"
        f" · {tr('stats.tokens_count', n=format_tokens(tok))}{RESET}", width))
    b.head.append(spacer())
    b.head.append(section(line(tr("stats.col_model"), tr("stats.col_sessions"),
                               tr("stats.col_msgs"),
                               tr("stats.col_input"), tr("stats.col_output"),
                               tr("stats.col_cost")), indent=GUTTER - 1))
    items = sorted(stats["by_model"].items(), key=lambda kv: -float(kv[1].get("cost") or 0))
    for name, data in items:
        sess, msgs = str(data.get("sessions", 0)), str(data.get("messages", 0))
        up = format_tokens(int(data.get("input_tokens") or 0))
        down = format_tokens(int(data.get("output_tokens") or 0))
        cost = _money(float(data.get("cost") or 0.0))
        b.rows.append((
            line(name, sess, msgs, up, down, cost),
            f"{_cell(name, name_w)} {DIM}{_cell(sess, 5, True)} {_cell(msgs, 6, True)}"
            f" {_cell(up, 8, True)} {_cell(down, 7, True)}{RESET}"
            f" {_cell(cost, 9, True)}",
        ))
    return b


# ─────────────────────────────── сам оверлей ────────────────────────────────
class StatsOverlay(Overlay):
    """Пять разделов в нижней зоне. Ни рамок, ни таблиц — только колонки."""

    def __init__(self, session: Session, period: int | None = None) -> None:
        super().__init__()
        self.snap = collect(session)
        self.periods: list[int | None] = list(PERIODS)
        if period is not None and period not in self.periods:
            self.periods.insert(1, period)
        self.period = period
        self._stats_cache: dict[int | None, dict] = {}
        # /stats N открывается сразу на общем своде за N дней — прежний смысл
        # аргумента сохранён, просто теперь это стартовый раздел, а не весь вывод.
        # Пустой диалог тоже начинаем с истории: показывать нули незачем.
        start = "history" if (period is not None or not self.snap.turns) else "session"
        self.section = SECTIONS.index(start)
        self.sel = dict.fromkeys(SECTIONS, 0)
        self._page = 1
        self._cache_key: tuple | None = None
        self._cache_text = ""

    # ── данные ──
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
        sel = self.sel[name]
        if name == "session":
            return _section_session(self.snap, width, sel)
        if name == "turns":
            return _section_turns(self.snap, width, sel)
        if name == "models":
            return _section_models(self.snap, width, sel)
        if name == "tools":
            return _section_tools(self.snap, width, sel)
        return _section_history(self.snap, width, sel, self.period, self._stats())

    # ── отрисовка ──
    def _tabs(self, width: int) -> str:
        """Полоса разделов. Активный — тем же фоном, что и курсор в списках."""
        sel_bg = role_bg("bg_select")
        labels = [tr(SECTION_LABELS[name]) for name in SECTIONS]
        cells = [(f"{sel_bg}{BOLD} {label} {RESET}" if i == self.section
                  else f"{DIM} {label} {RESET}")
                 for i, label in enumerate(labels)]
        line = "  " + "".join(cells)
        used = 2 + sum(len(label) + 2 for label in labels)
        if self.snap.title and width - used >= 22:
            free = width - used - 2
            line += f"{DIM}{pad(clip(self.snap.title, free), free, 'right')}{RESET}"
        return line

    def render(self, width: int) -> str:
        # Shell зовёт render дважды за кадр (лямбда высоты и контрол
        # содержимого) — второй раз отдаём готовое, а не считаем заново.
        key = (width, self.section, self.sel[SECTIONS[self.section]], self.period)
        if key != self._cache_key:
            self._cache_key = key
            self._cache_text = self._render(width)
        return self._cache_text

    def version(self):
        """Статистика не пересобирается из-за кадров соседнего стрима."""
        name = SECTIONS[self.section]
        return (self.section, self.sel[name], self.period)

    def _render(self, width: int) -> str:
        budget = 20
        if self.shell is not None:
            try:
                budget = self.shell.overlay_budget()
            except Exception:
                logger.debug("overlay_budget failed", exc_info=True)
        body = self._body(width)
        name = SECTIONS[self.section]

        # Вкладки + пустая строка + шапка + подвал, остальное — окно списка.
        # Если списку остаётся меньше трёх строк, подвал (деталь по выделенному)
        # уступает ему место: без списка деталь бессмысленна.
        avail = max(1, budget - 2 - len(body.head) - len(body.foot))
        if body.rows and avail < 3 and body.foot:
            body.foot = []
            avail = max(1, budget - 2 - len(body.head))
        self._page = max(1, avail - 1)

        sel = max(0, min(self.sel[name], len(body.rows) - 1)) if body.rows else 0
        self.sel[name] = sel
        start, end, above, below = scroll_window(len(body.rows), sel, avail)

        out = [self._tabs(width), spacer(), *body.head]
        if above:
            out.append(more_note(above, up=True))
        for i in range(start, end):
            plain, styled = body.rows[i]
            picked = i == sel and body.selectable
            out.append(row(plain if picked else styled, selected=picked, width=width))
        if below:
            out.append(more_note(below, up=False))
        out.extend(body.foot)
        return "\n".join(out[:max(1, budget)])

    def hint(self) -> str:
        """Подсказка своя на раздел: в 60 колонок влезает только то, что здесь
        и правда работает, а не весь список клавиш сразу."""
        name = SECTIONS[self.section]
        pairs = [("←→", tr("stats.hint_section"))]
        if name == "session":
            pairs.append(("↑↓", tr("stats.hint_scroll")))
        elif name == "history":
            pairs += [("↑↓", tr("stats.hint_row")), ("p", tr("stats.hint_period"))]
        else:
            pairs += [("↑↓", tr("stats.hint_row")),
                      ("pgup/pgdn", tr("stats.hint_page"))]
        pairs.append(("esc", tr("stats.hint_close")))
        return key_hints(*pairs)

    # ── клавиши ──
    def _move(self, delta: int) -> None:
        """Двигаем только курсор: положение окна считает `_render`, потому что
        только там известен бюджет строк, выданный Shell на этот кадр."""
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
            idx = self.periods.index(self.period) if self.period in self.periods else 0
            self.period = self.periods[(idx + 1) % len(self.periods)]
            self.sel["history"] = 0
        elif len(key) == 1 and key.isdigit() and 1 <= int(key) <= len(SECTIONS):
            self.section = int(key) - 1
        self._cache_key = None
        return True


# ────────────────────────────── точка входа ─────────────────────────────────
def _flat(body: Body) -> list[str]:
    pre = " " * (GUTTER - 1)
    return [*body.head,
            *(pre + styled if styled else "" for _plain, styled in body.rows),
            *body.foot]


def _static_summary(session: Session, period: int | None) -> str:
    """Плоский текст для headless-режима: Shell нет, оверлею негде жить.

    Разделы те же самые, просто все сразу и без навигации.
    """
    snap = collect(session)
    width = 78
    lines: list[str] = []
    if snap.turns:
        for build in (_section_session, _section_turns, _section_models, _section_tools):
            lines.extend(_flat(build(snap, width, 0)))
            lines.append("")
    try:
        stats = storage.get_statistics(days=period)
    except Exception:
        logger.warning("stats: get_statistics failed", exc_info=True)
        stats = {}
    lines.extend(_flat(_section_history(snap, width, 0, period, stats)))
    return "\n".join(lines)


async def stats_interactive(session: Session, period: int | None = None) -> None:
    """Открыть интерактивную статистику. Без Shell печатает статичный свод."""
    shell = get_shell()
    if shell is None:
        from rich.console import Console
        from rich.text import Text
        Console().print(Text.from_ansi(_static_summary(session, period)))
        return
    await shell.run_overlay(StatsOverlay(session, period))


__all__ = ["StatsOverlay", "collect", "stats_interactive"]
