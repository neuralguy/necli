"""Отображение субагентов.

SubagentBuffer — буфер событий одного субагента с рендерингом.
SubagentTracker — панель прогона в динамической зоне Shell (раньше — rich Live).
SwarmOverlay — та же панель в нижней зоне, но с навигацией.

Единый framed-рендер (стиль Claude Code) используется всегда. Слева панель
«Phases» со списком фаз и прогрессом done/total (активная фаза помечена «›»),
справа панель с агентами показанной фазы: строка
«<глиф> <label>  <модель>   <Ntok · Mt · Ns>». Если фаз нет, создаётся
синтетическая фаза «Agents», чтобы внешний вид не переключался.
Сверху хедер «Subagents · имя … N/M agents · общее_время».

Один и тот же render_view обслуживает обе зоны, поэтому таблица, открытая
Enter'ом со строки под рамкой, выглядит ровно как та, что тикает над рамкой:
курсор, окно строк и панель деталей — единственное, что добавляется.
"""

import asyncio
import itertools
import logging
import shutil
import time
from dataclasses import dataclass

from rich.console import Group
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from config.i18n import t as tr
from config.themes import t
from config.ui import ui
from ui.shell import Overlay, RowGroup, get_shell, print_static, visible_width

logger = logging.getLogger(__name__)


def _w() -> int:
    cap = int(ui.get("subagent.max_width", 0))
    term = shutil.get_terminal_size((80, 24)).columns
    return term if cap <= 0 else min(cap, term)


def _fmt_tokens(n: int) -> str:
    """Компактный формат счётчика токенов: 940, 25.8k, 1.2M."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        v = n / 1000.0
        return f"{v:.1f}k" if v < 100 else f"{v:.0f}k"
    v = n / 1_000_000.0
    return f"{v:.1f}M" if v < 100 else f"{v:.0f}M"


_TOOL_HINT_ARG = {
    "web_fetch": "urls",
    "web_search": "queries",
    "skill": "name",
    "memory_read": "name",
    "memory_write": "name",
    "poll": "question",
    "subagent": "prompt",
    "expand_tool_result": "id",
}


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
        role: str = "", preset: str = "", depends_on: list | None = None,
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
        # Стартовое состояние — "queued", а не "starting": задача, которая ещё
        # ждёт своей волны или слота семафора (max_concurrency), НЕ инициализируется.
        # При 56 задачах и лимите 12 сорок четыре строки показывали "starting" и врали.
        self.status = "queued"
        self.error: str | None = None
        self.activity_start_time: float | None = None
        self.activity_end_time: float | None = None
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

    def on_queued(self):
        """Задача ждёт слот семафора. Часы НЕ запускаем: работа ещё не началась."""
        self.status = "queued"

    def on_start(self):
        """Слот получен — с этой секунды агент действительно инициализируется."""
        self._mark_activity()
        self.status = "starting"

    def on_chunk(self, text: str):
        self._mark_activity()
        self.streaming_text = text
        self.status = "streaming"

    def on_tool_start(self, tool_name: str, command: str, args: dict | None = None):
        self._mark_activity()
        self.status = "tools"
        hint = ""
        if args:
            if tool_name == "shell":
                cmd = args.get("command") or command or ""
                hint = cmd.splitlines()[0] if cmd else ""
            else:
                path = args.get("path")
                if path is None:
                    paths_val = args.get("paths")
                    if paths_val is not None:
                        path = paths_val
                if isinstance(path, (list, tuple)):
                    names = []
                    for p in path:
                        if isinstance(p, str):
                            names.append(p)
                        elif isinstance(p, dict):
                            names.append(str(p.get("path", p)))
                    hint = ", ".join(names[:3])
                    if len(names) > 3:
                        hint += ", …"
                elif path:
                    hint = str(path)
                else:
                    _arg_key = _TOOL_HINT_ARG.get(tool_name)
                    if _arg_key:
                        val = args.get(_arg_key)
                        if isinstance(val, list):
                            items = [str(v)[:60] for v in val[:3]]
                            hint = ", ".join(items)
                            if len(val) > 3:
                                hint += ", …"
                        elif val:
                            hint = str(val)[:80]
        self.tool_events.append(
            ToolEvent(
                tool_name=tool_name,
                command=hint or command,
                emoji=_tool_emoji(tool_name),
            )
        )

    def on_tool_done(self, elapsed: float = 0.0, error: bool = False):
        self._mark_activity()
        if self.tool_events:
            ev = self.tool_events[-1]
            ev.status = "error" if error else "done"
            ev.elapsed = elapsed

    def on_iteration(self, n: int):
        self.iteration = n

    def on_usage(self, usage_metadata: dict | None) -> None:
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
        return "\u25ef", "dim"

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
                trail.append(f"\u25ef{ev.emoji} ", style="dim")
        return trail

    def _current_action(self) -> str:
        """Одно текущее действие компактно: `create(test.py)`, `$ pytest`.

        Приоритет: бегущий инструмент → последний завершённый (✓/✗) →
        «streaming…» только когда инструментов ещё не было вовсе. Иначе между
        вызовами status==streaming затирал бы инструменты голым «streaming…»."""
        if self.status == "queued":
            # Единственное, что честно можно сказать про ждущего слот: он ждёт.
            return tr("subagent.queued")
        if self.status in ("done", "error", "starting"):
            return ""
        last = self.tool_events[-1] if self.tool_events else None
        if last is not None:
            arg = (last.command or "").strip()
            if last.tool_name == "shell":
                body = f"$ {arg}" if arg else "$ shell"
            elif arg:
                body = f"{last.tool_name}({arg})"
            else:
                body = last.tool_name
            if last.status == "running":
                return body
            mark = "\u2717" if last.status == "error" else "\u2713"
            return f"{mark}{body}"
        # Ещё ни одного инструмента — значит модель только пишет текст.
        if self.status == "streaming":
            return "streaming…"
        return ""

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
        elif self.status == "queued":
            txt.append(tr("subagent.queued"), style="dim")
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

        # Левая часть — глиф + label + модель + текущее действие.
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

        # Текущее действие — справа от модели. Усекается ПЕРВЫМ при нехватке
        # ширины (имя и метрики важнее), под него берём остаток строки.
        action = self._current_action()
        if action:
            fixed = len(left.plain) + len(metrics.plain) + 2  # +2 разделителя
            avail_action = width - fixed
            if avail_action >= 4:
                if len(action) > avail_action:
                    action = action[: avail_action - 1] + "\u2026"
                # queued \u2014 \u043d\u0435 \u0434\u0435\u0439\u0441\u0442\u0432\u0438\u0435, \u0430 \u0435\u0433\u043e \u043e\u0442\u0441\u0443\u0442\u0441\u0442\u0432\u0438\u0435: \u043f\u0438\u0448\u0435\u043c \u0442\u0443\u0441\u043a\u043b\u043e, \u0447\u0442\u043e\u0431\u044b
                # \u0441\u0442\u0440\u043e\u043a\u0430 \u043d\u0435 \u0447\u0438\u0442\u0430\u043b\u0430\u0441\u044c \u043a\u0430\u043a \u0440\u0430\u0431\u043e\u0442\u0430\u044e\u0449\u0430\u044f.
                left.append(f"  {action}",
                            style="dim" if self.status == "queued" else t("magenta"))

        # Собираем с выравниванием метрик вправо.
        gap = width - len(left.plain) - len(metrics.plain)
        if gap < 1:
            # Не влезает — режем хвост левой части, оставляя место под метрики.
            avail = max(4, width - len(metrics.plain) - 1)
            left.truncate(avail, overflow="ellipsis")
            gap = max(1, width - len(left.plain) - len(metrics.plain))
        left.append(" " * gap)
        left.append_text(metrics)
        left.truncate(width, overflow="ellipsis")
        return left

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


_RUN_SEQ = itertools.count(1)

# Живые прогоны. Нужны по двум причинам: "swarm" — канонический ключ
# динамической зоны (см. SHELL_API.md), и второй одновременный прогон не должен
# затирать кадр первого; плюс вертикаль экрана делится между всеми кадрами.
_LIVE_TRACKERS: list["SubagentTracker"] = []

# Прогоны с открытой таблицей: пока она открыта, кадры из динамической зоны
# убраны — их содержимое показывает оверлей, и дублировать его незачем.
_OPEN_TABLES: set[int] = set()

#: Строки кадра помимо агентов: хедер, рамка кадра, рамка панели, индикатор.
_PANEL_CHROME = 6
#: Строки вне динамической зоны: статус, ввод, нижняя линия, пустая + запас.
_OUTSIDE_ROWS = 6
#: Панель деталей выбранного агента: шапка, задача (2), действие + рамка.
_DETAIL_ROWS = 6
#: Сколько строк стрима/инструментов показывать в раскрытых деталях.
_EXPAND_LINES = 8


def _term_rows() -> int:
    return shutil.get_terminal_size((80, 24)).lines


def _panel_budget(shown: int, below: int) -> int | None:
    """Сколько строк агентов помещается в ОДИН кадр динамической зоны.

    None — кадр не влезает вовсе, остаётся только строка под рамкой (для этого
    она и есть). Иначе prompt_toolkit пишет «Window too small...»: именно это и
    происходило, когда два одновременных прогона рисовали по 20 строк каждый.
    """
    if shown <= 0:
        return None
    per = (_term_rows() - _OUTSIDE_ROWS - below) // shown
    return per - _PANEL_CHROME if per >= _PANEL_CHROME + 2 else None


def _short(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _viewport(total: int, budget: int, anchor: int) -> tuple[int, int]:
    """Окно строк [start, end). budget включает строку-индикатор; 0 = без окна.

    Пока панель жила в Live, она рисовала все строки фазы: Live сам обрезал
    кадр по высоте консоли. В динамической зоне обрезать некому — 56 строк
    выдавили бы рамку ввода за край экрана, поэтому окно считаем сами.
    """
    if budget <= 0 or total <= budget:
        return 0, total
    usable = max(1, budget - 1)
    start = max(0, min(anchor - usable // 2, total - usable))
    return start, start + usable


def _derive_run_name(buffers: list[SubagentBuffer]) -> str:
    """Короткое имя прогона, когда вызывающая сторона его не передала.

    Схема инструмента несёт optional name/goal, но панель конструируется
    отдельно от разбора args, поэтому имя доходит не всегда. Тогда берём
    единственную фазу (её заполняет именем прогона _fill_default_phase) либо
    первые слова первой задачи: без идентичности строка под рамкой и шапка
    выглядели бы одинаково у всех прогонов.
    """
    phases: list[str] = []
    for b in buffers:
        if b.phase and b.phase not in phases:
            phases.append(b.phase)
    if len(phases) == 1:
        return _short(phases[0], 28)
    head = ""
    if buffers:
        lines = (buffers[0].prompt or "").strip().splitlines()
        head = lines[0].lstrip("#*-— ").strip() if lines else ""
    words = head.split()
    return _short(" ".join(words[:4]), 28) if words else "subagents"


class SubagentTracker:
    """Панель субагентов: живой кадр в динамической зоне Shell + строка под рамкой.

    Раньше кадры двигал rich Live — он лез в терминал курсором и дрался с
    prompt_toolkit за одни и те же строки. Теперь ровно тот же callable, что
    уходил в get_renderable, отдаётся Shell'у: его ticker вызывает его сам
    ~10 fps, поэтому вид панели не изменился ни на символ.

    Интерактив: под панелью ввода живёт строка-сводка прогона (стрелка вниз →
    Enter), она открывает ту же таблицу оверлеем, где можно ходить по агентам и
    фазам. Пока таблица открыта, кадр из динамической зоны убран — иначе одно и
    то же рисовалось бы дважды.
    """

    def __init__(self, buffers: list[SubagentBuffer], name: str = ""):
        self._buffers = buffers
        self._seq = next(_RUN_SEQ)
        self._name = (name or "").strip() or _derive_run_name(buffers)
        # Ключ строки уникален всегда: прогонов может идти несколько сразу.
        self._row_key = f"swarm-{self._seq}"
        self._dyn_key = "swarm"
        self._shell = None
        self._running = False
        self._stopped = False
        self._overlay_task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._running

    @property
    def name(self) -> str:
        return self._name

    # ── жизненный цикл ───────────────────────────────────────────────────────

    def start(self):
        self._running = True
        sh = get_shell()
        if sh is None:
            # Headless / не-TTY: Application нет, анимировать нечего —
            # финальный кадр напечатаем один раз в stop().
            return
        self._shell = sh
        if any(live._dyn_key == self._dyn_key for live in _LIVE_TRACKERS):
            self._dyn_key = f"swarm#{self._seq}"
        _LIVE_TRACKERS.append(self)
        sh.set_dynamic(self._dyn_key, self._render)
        sh.attach_rows(self._row_key, RowGroup(self.row_label, self.open_table))

    def stop(self):
        # Идемпотентность обязательна: agent/loop.py на пути исключения зовёт
        # stop() и в except, и в finally.
        if self._stopped:
            return
        self._stopped = True
        self._running = False
        sh, self._shell = self._shell, None
        try:
            frame = self._render_final()
        except Exception:
            logger.debug("subagent tracker: final render failed", exc_info=True)
            return
        if sh is None:
            # Трекер стартовал без Application. print_static сам решит, куда
            # писать: если Shell успел появиться — через run_in_terminal, иначе
            # прямо в stdout. Прямой Console.print сломал бы кадр Application.
            print_static(frame)
            return
        if self in _LIVE_TRACKERS:
            _LIVE_TRACKERS.remove(self)
        _OPEN_TABLES.discard(self._seq)
        sh.clear_dynamic(self._dyn_key)
        sh.detach_rows(self._row_key)
        # transient=False у Live оставлял финальный кадр в скроллбэке — сохраняем.
        sh.print_static(frame)

    async def wait_all_done(self):
        while not all(b.status in ("done", "error") for b in self._buffers):  # noqa: ASYNC110
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.3)

    # ── строка под рамкой ────────────────────────────────────────────────────

    def row_label(self) -> str:
        """Компактная сводка прогона для строки под панелью ввода."""
        emoji = str(ui.get("subagent.header_emoji", "\U0001f916"))
        total = len(self._buffers)
        done = sum(1 for b in self._buffers if b.status in ("done", "error"))
        parts = [f"{emoji} {self._name}",
                 tr("subagent.agents_n", done=done, total=total)]
        phases = self._seen_phases()
        if phases:
            active = self.active_phase()
            idx = phases.index(active) + 1 if active in phases else 1
            parts.append(tr("subagent.phase_n", i=idx, n=len(phases)))
        failed = sum(1 for b in self._buffers if b.status == "error")
        if failed:
            parts.append(f"✗{failed}")
        queued = sum(1 for b in self._buffers if b.status == "queued")
        if queued:
            parts.append(tr("subagent.queued_n", n=queued))
        line = " · ".join(parts)
        # Строка печатается без переноса (wrap_lines=False) — режем сами.
        limit = max(12, _w() - 4)
        while visible_width(line) > limit and len(line) > 4:
            line = line[:-2] + "…"
        return line

    def open_table(self) -> None:
        """Enter на строке. RowGroup.open синхронный, а run_overlay — корутина,
        поэтому запускаем задачей: мы внутри обработчика клавиш, loop крутится."""
        sh = self._shell
        if sh is None:
            return
        if self._overlay_task is not None and not self._overlay_task.done():
            return
        try:
            self._overlay_task = asyncio.create_task(self._show_table(sh))
        except RuntimeError:
            logger.debug("subagent tracker: no running loop for overlay", exc_info=True)

    async def _show_table(self, sh) -> None:
        overlay = SwarmOverlay(self)
        # Кадры «сворачиваются» на всё время таблицы: её содержимое — то же
        # самое, а вертикали экрана на два кадра сразу не хватает.
        _OPEN_TABLES.add(self._seq)
        sh.clear_dynamic(self._dyn_key)
        ticker = asyncio.create_task(_overlay_ticker(sh))
        try:
            await sh.run_overlay(overlay)
        except Exception:
            logger.warning("subagent overlay failed", exc_info=True)
        finally:
            ticker.cancel()
            _OPEN_TABLES.discard(self._seq)
            if self._running:
                sh.set_dynamic(self._dyn_key, self._render)

    # ── фазы ─────────────────────────────────────────────────────────────────

    def _seen_phases(self) -> list[str]:
        """Фазы в порядке первого появления."""
        seen: list[str] = []
        for b in self._buffers:
            if b.phase and b.phase not in seen:
                seen.append(b.phase)
        return seen

    def phase_names(self) -> list[str]:
        """Фазы для показа. Без фаз — одна синтетическая, чтобы вид не менялся."""
        return self._seen_phases() or ["Agents"]

    def phase_buffers(self, phase: str) -> list[SubagentBuffer]:
        if self._seen_phases():
            return [b for b in self._buffers if b.phase == phase]
        return list(self._buffers)

    def active_phase(self) -> str:
        """Первая незавершённая фаза, иначе последняя."""
        phases = self.phase_names()
        for ph in phases:
            if not all(b.status in ("done", "error") for b in self.phase_buffers(ph)):
                return ph
        return phases[-1]

    # ── время ────────────────────────────────────────────────────────────────

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

    # ── рендер ───────────────────────────────────────────────────────────────

    def _render(self) -> Group | None:
        """Кадр динамической зоны — тот же, что раньше отдавался Live.

        None — зоне нечего показывать (открыта таблица либо кадр не влезает в
        экран); Shell в этом случае даёт зоне нулевую высоту, а сводка остаётся
        в строке под рамкой.
        """
        if _OPEN_TABLES:
            return None
        live = max(1, len(_LIVE_TRACKERS))
        budget = _panel_budget(live, live)
        if budget is None:
            return None
        return self.render_view(rows_budget=budget)

    def _render_final(self) -> Group:
        """Кадр в скроллбэк: без окна — статику по высоте обрезать нечем."""
        return self.render_view()

    def render_view(
        self,
        width: int = 0,
        *,
        phase: str | None = None,
        selected: int | None = None,
        rows_budget: int = 0,
        detail: bool = False,
        expanded: bool = False,
    ) -> Group:
        """Двухпанельный кадр: слева фазы, справа агенты показанной фазы.

        Один рендер на две зоны, поэтому вид гарантированно совпадает.
        `selected is None` — режим динамической зоны: курсора нет вообще, кадр
        побайтово прежний. `phase` даёт смотреть любую фазу, а не только
        активную; `rows_budget` — высота окна строк (0 = без окна).
        """
        width = min(width, _w()) if width > 0 else _w()
        total = len(self._buffers)
        done = sum(1 for b in self._buffers if b.status in ("done", "error"))
        phases = self.phase_names()
        active = self.active_phase()
        shown = phase if phase in phases else active

        # Хедер: Subagents · имя … N/M agents · общее время (прижато вправо).
        header = Text("  ")
        header.append("Subagents", style=f"bold {t('magenta')}")
        if self._name:
            header.append(f" · {self._name}", style=t("accent"))
        right = f"{done}/{total} agents · {self._fmt_clock(self._total_elapsed())}"
        gap = width - len(header.plain) - len(right)
        if gap < 1:
            gap = 1
        header.append(" " * gap)
        header.append(right, style="dim")

        frame_width = max(40, width - 4)
        left_w = max(18, int(frame_width * 0.22))
        left_panel = self._phase_panel(
            phases, active, shown, left_w, cursor=selected is not None,
            rows_budget=rows_budget,
        )

        shown_bufs = self.phase_buffers(shown)
        right_w = max(28, frame_width - left_w - 3)
        right_panel = self._agents_panel(
            shown, shown_bufs, right_w, selected=selected, rows_budget=rows_budget,
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

        parts: list = [header, frame]
        if detail and shown_bufs:
            sel = min(max(0, selected or 0), len(shown_bufs) - 1)
            parts.append(self._detail_panel(shown_bufs[sel], width, expanded))
        return Group(*parts)

    def _phase_panel(self, phases: list[str], active: str, shown: str,
                     left_w: int, cursor: bool, rows_budget: int = 0) -> Panel:
        """Левая панель со списком фаз. Каждая фаза — ровно одна строка:
        «<маркер><номер> <имя…>   done/total» — имя усекается под ширину."""
        inner = left_w - 4  # минус рамка(2) и padding(2)
        # Фаз обычно единицы, но конвейер на двадцать стадий не должен растянуть
        # кадр выше экрана — окно то же, что у списка агентов.
        p_start, p_end = _viewport(
            len(phases), rows_budget, phases.index(shown) if shown in phases else 0,
        )
        phase_lines: list[Text] = []
        for i in range(p_start, p_end):
            ph = phases[i]
            ph_bufs = self.phase_buffers(ph)
            ph_done = sum(1 for b in ph_bufs if b.status in ("done", "error"))
            # Состояние фазы: пройденная (все агенты завершены и фаза не активна) →
            # зелёная галочка; активная → «›»; будущая → пробел.
            is_done = bool(ph_bufs) and ph_done == len(ph_bufs) and ph != active
            if is_done:
                marker = "✓ "
                mstyle = f"bold {t('success')}"
                name_style = t("success")
            elif ph == active:
                marker = "› "
                mstyle = f"bold {t('accent')}"
                name_style = f"bold {t('accent')}"
            else:
                marker = "  "
                mstyle = "dim"
                name_style = "dim"
            count = f"{ph_done}/{len(ph_bufs)}"
            prefix = f"{marker}{i + 1}. "
            # Бюджет под имя = inner − префикс − счётчик − разделитель(1 пробел).
            name_budget = max(3, inner - len(prefix) - len(count) - 1)
            name = ph if len(ph) <= name_budget else ph[: name_budget - 1] + "…"
            gap = max(1, inner - len(prefix) - len(name) - len(count))
            row = Text()
            row.append(marker, style=mstyle)
            row.append(f"{i + 1}. ", style=mstyle)
            row.append(name, style=name_style)
            row.append(" " * gap)
            row.append(count, style="dim")
            # ✓/› остаются признаком СОСТОЯНИЯ фазы, фон — признаком курсора:
            # смотреть можно любую фазу, не только активную.
            if cursor and ph == shown:
                row.style = Style(bgcolor=t("bg_select"))
            phase_lines.append(row)
        if p_end - p_start < len(phases):
            phase_lines.append(Text(
                tr("subagent.page_range", start=p_start + 1, end=p_end,
                   total=len(phases)),
                style="dim",
            ))
        return Panel(
            Group(*phase_lines),
            title="Phases",
            title_align="left",
            border_style=t("magenta"),
            padding=(0, 1),
            width=left_w,
        )

    def _agents_panel(self, shown: str, shown_bufs: list[SubagentBuffer], right_w: int,
                      *, selected: int | None, rows_budget: int) -> Panel:
        """Правая панель: агенты показанной фазы, окном по доступной высоте."""
        shown_done = sum(1 for b in shown_bufs if b.status in ("done", "error"))
        row_w = max(20, right_w - 4)
        anchor = selected if selected is not None else _follow_anchor(shown_bufs)
        start, end = _viewport(len(shown_bufs), rows_budget, anchor)
        agent_lines: list[Text] = []
        for i in range(start, end):
            row = shown_bufs[i].render_agent_row(row_w)
            if selected is not None and i == selected:
                # Курсор — фоном на всю строку: так подсвечивает выбранное всё
                # остальное меню проекта, и колонки не съезжают.
                row.pad_right(max(0, row_w - len(row.plain)))
                row.style = Style(bgcolor=t("bg_select"))
            agent_lines.append(row)
        if not agent_lines:
            agent_lines = [Text("(no agents)", style="dim")]
        elif end - start < len(shown_bufs):
            agent_lines.append(Text(
                tr("subagent.page_range", start=start + 1, end=end,
                   total=len(shown_bufs)),
                style="dim",
            ))
        return Panel(
            Group(*agent_lines),
            title=f"{shown} {shown_done}/{len(shown_bufs)}",
            title_align="left",
            border_style=t("accent"),
            padding=(0, 1),
            width=right_w,
        )

    def _detail_panel(self, b: SubagentBuffer, width: int, expanded: bool) -> Panel:
        """Детали выбранного агента — в существующем стиле панели субагента."""
        pad = tuple(ui.get("paddings.subagent_panel", [0, 1]))
        h_pad = pad[1] if len(pad) > 1 else 1
        # −2 клетки запаса: render_block выравнивает шапку по len(), а 🤖/⏱
        # занимают по две клетки — без запаса строка переносится и панель растёт.
        inner = max(20, width - 4 - 2 * h_pad)
        lines: list[Text] = list(b.render_block(inner))
        if expanded:
            lines.extend(_detail_tail(b, inner))
        border = (
            "red" if b.status == "error"
            else t("success") if b.status == "done"
            else t("magenta")
        )
        return Panel(Group(*lines), border_style=border, padding=pad, width=width)


def _follow_anchor(bufs: list[SubagentBuffer]) -> int:
    """Куда смотреть окну без курсора: на первого незавершённого — окно само
    уползает за прогрессом, как это делал хвост Live."""
    for i, b in enumerate(bufs):
        if b.status not in ("done", "error"):
            return i
    return max(0, len(bufs) - 1)


def _detail_tail(b: SubagentBuffer, inner: int, limit: int = _EXPAND_LINES) -> list[Text]:
    """Хвост работы агента: ошибка, последние строки стрима либо инструменты."""
    if b.error:
        return [
            Text(f"  {ln}", style="red")
            for ln in _wrap_words(b.error.strip(), max(8, inner - 2))[:limit]
        ]
    body = [ln for ln in (b.streaming_text or "").splitlines() if ln.strip()]
    if body:
        return [Text(f"  {ln[: inner - 2]}", style="dim") for ln in body[-limit:]]
    out: list[Text] = []
    for ev in b.tool_events[-limit:]:
        if ev.status == "done":
            mark, style = "✓", t("success")
        elif ev.status == "error":
            mark, style = "✗", "red"
        else:
            mark, style = "◯", "dim"
        cmd = f" {ev.command.strip()}" if ev.command else ""
        out.append(Text(f"  {mark}{ev.emoji} {ev.tool_name}{cmd}"[:inner], style=style))
    return out or [Text(f"  {tr('subagent.no_output')}", style="dim")]


async def _overlay_ticker(sh) -> None:
    """Пока таблица открыта, кадры двигаем сами: ticker Shell'а просыпается
    только когда динамическая зона непуста, а мы её на это время свернули."""
    while True:
        await asyncio.sleep(0.1)
        sh.invalidate()


class SwarmOverlay(Overlay):
    """Таблица субагентов в нижней зоне: навигация по агентам и фазам.

    Вид — тот же двухпанельный кадр, что и в динамической зоне (один и тот же
    render_view). Добавлены только курсор, листание и панель деталей выбранного
    агента: заказчик просил интерактив, а не новый дизайн.
    """

    def __init__(self, tracker: SubagentTracker) -> None:
        super().__init__()
        self._tracker = tracker
        phases = tracker.phase_names()
        active = tracker.active_phase()
        self._phase_idx = phases.index(active) if active in phases else 0
        self._sel = 0
        self._expanded = False
        self._page = 1  # размер окна с последнего кадра — для pageup/pagedown

    # ── что показываем ──
    def _phase(self) -> str:
        phases = self._tracker.phase_names()
        self._phase_idx = max(0, min(self._phase_idx, len(phases) - 1))
        return phases[self._phase_idx]

    def _visible(self) -> list[SubagentBuffer]:
        return self._tracker.phase_buffers(self._phase())

    def _layout(self) -> tuple[int, bool]:
        """(окно строк, показывать ли детали) под фактическую высоту экрана.

        Снаружи: статус, нижняя линия, подсказка, пустая + запас; внутри: хедер,
        рамки, индикатор и панель деталей. На низком терминале деталями
        приходится жертвовать — иначе prompt_toolkit скажет «Window too small».
        """
        base = _OUTSIDE_ROWS + 1 + _PANEL_CHROME
        budget = _term_rows() - base - _DETAIL_ROWS - self._expand_rows()
        if budget >= 3:
            return budget, True
        return max(2, _term_rows() - base), False

    def _expand_rows(self) -> int:
        """Сколько строк реально займёт раскрытый хвост. Считаем по факту, а не
        по максимуму: иначе одна строка ошибки съедала бы место под 8 агентов."""
        if not self._expanded:
            return 0
        bufs = self._visible()
        if not bufs:
            return 0
        b = bufs[min(self._sel, len(bufs) - 1)]
        return len(_detail_tail(b, max(20, _w() - 6)))

    def render(self, width: int):
        bufs = self._visible()
        if bufs:
            self._sel = max(0, min(self._sel, len(bufs) - 1))
        budget, detail = self._layout()
        self._page = max(1, budget - 1)
        return self._tracker.render_view(
            width,
            phase=self._phase(),
            selected=self._sel,
            rows_budget=budget,
            detail=detail,
            expanded=self._expanded,
        )

    def hint(self) -> str:
        tail = "" if self._tracker.running else f" · {tr('subagent.finished')}"
        # Подсказка печатается без переноса, поэтому на узком терминале
        # оставляем только главное — иначе она обрезалась бы посреди слова.
        if _w() < 84:
            return tr("subagent.hint_narrow") + tail
        return tr("subagent.hint") + tail

    # ── клавиши ──
    def handle_key(self, key: str, event) -> bool:
        total = len(self._visible())
        if key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
        elif key in ("up", "k", "c-p"):
            if total:
                self._sel = (self._sel - 1) % total
        elif key in ("down", "j", "c-n"):
            if total:
                self._sel = (self._sel + 1) % total
        elif key in ("left", "h"):
            self._switch_phase(-1)
        elif key in ("right", "l", "tab"):
            self._switch_phase(1)
        elif key == "pageup":
            self._sel = max(0, self._sel - self._page)
        elif key == "pagedown":
            self._sel = min(max(0, total - 1), self._sel + self._page)
        elif key == "home":
            self._sel = 0
        elif key == "end":
            self._sel = max(0, total - 1)
        elif key == "enter":
            self._expanded = not self._expanded
        return True

    def _switch_phase(self, delta: int) -> None:
        """Переход между фазами. Смотреть можно любую — активная тут ни при чём."""
        phases = self._tracker.phase_names()
        if not phases:
            return
        self._phase_idx = (self._phase_idx + delta) % len(phases)
        self._sel = 0
