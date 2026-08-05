"""Shell — единственный постоянный prompt_toolkit Application на всю сессию.

Зачем это существует
--------------------
Раньше ввод жил в `PromptSession.prompt_async`, а вывод агента — в `rich.live.Live`.
Live лезет в терминал курсором и дерётся с prompt_toolkit за одни и те же строки,
а блокирующий `await` хода агента делал поле ввода недоступным на всё время ответа.

Здесь ровно одна точка владения экраном. Экран делится на две зоны:

  СТАТИКА   — печатается в scrollback один раз и больше не трогается
              (`print_static` → `run_in_terminal`). Работает родная прокрутка
              колесом и выделение мышью, потому что мы не включаем ни
              alt-screen, ни mouse tracking.

  ДИНАМИКА  — всё, что перерисовывается: спиннер, thinking, частичный вызов
              инструмента, активный блок ответа. Живёт ВНУТРИ Application
              (`set_dynamic`), поэтому prompt_toolkit сам стирает и рисует эту
              область заново, и в scrollback не остаётся дублей.

Раскладка сверху вниз:

    <динамическая зона>          высота 0, когда нечего показывать
    <одна пустая строка>         между динамикой Working/ответом и вводом
    ─── статус ──────────────    верхняя линия рамки
    🚀agent ❯ ввод             режим агента слева от стрелки ввода
    ─────────────────────────    нижняя линия рамки
    <меню автодополнения>        растёт вниз, сдвигая ввод не выше середины
    <до 4 агентов/фоновых задач> ИЛИ строка подсказок активного оверлея
    <сводка скрытых строк>       только если элементов больше четырёх
    <пустая строка>              обязательна: отбивка от низа терминала

У интерактивного оверлея обе линии рамки скрыты. После полноэкранного просмотра
агента или фоновой задачи обычная нижняя зона заново привязывается к низу терминала,
поэтому поле ввода не остаётся на месте верхней строки закрытого оверлея.

Вид сохраняется за счёт моста Rich → prompt_toolkit: любой Rich-объект
рендерится в ANSI и показывается внутри Window через `FormattedText.ANSI`.
Поэтому весь существующий код отрисовки (`render_partial_tool`,
`render_think_static`, панели субагентов) переиспользуется как есть.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from prompt_toolkit.application import Application, run_in_terminal
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_completions, is_done
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer, ScrollOffsets
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import (
    AppendAutoSuggestion,
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.layout.utils import explode_text_fragments
from prompt_toolkit.output import ColorDepth
from prompt_toolkit.styles import Style
from rich.console import Console
from wcwidth import wcswidth

logger = logging.getLogger(__name__)

# ─────────────────────────── типы сообщений из UI ───────────────────────────
# Единая воронка: и клавиатура, и Telegram, и пробуждение фоновой задачей
# кладут сюда одинаковые кортежи, а главный цикл разбирает их в одном месте.
SUBMIT_USER = "user"          # обычное сообщение агенту
SUBMIT_SLASH = "slash"        # slash-команда
SUBMIT_EOF = "eof"            # Ctrl+D
SUBMIT_INTERRUPT = "interrupt"  # Ctrl+C
SUBMIT_BG_RESUME = "bg_resume"  # фоновая задача завершилась, разбудить агента
SUBMIT_TG = "tg"              # сообщение из Telegram
HISTORY_LIMIT = 100           # сколько последних команд держать в истории ↑/↓
ROW_WINDOW_SIZE = 4           # максимум интерактивных строк под полем ввода


def visible_width(s: str) -> int:
    """Ширина строки в ячейках терминала (эмодзи и CJK занимают две)."""
    n = wcswidth(s)
    return n if n >= 0 else len(s)


def clip_visible(s: str, width: int, tail: str = "…") -> str:
    """Обрезать plain-текст по ячейкам терминала, включая emoji/CJK."""
    if width <= 0:
        return ""
    if visible_width(s) <= width:
        return s
    limit = max(0, width - visible_width(tail))
    out: list[str] = []
    used = 0
    for char in s:
        char_width = max(0, wcswidth(char))
        if used + char_width > limit:
            break
        out.append(char)
        used += char_width
    return "".join(out) + tail


def _word_wrap_padding(chars: list[str], width: int) -> dict[int, int]:
    """Сколько display-пробелов нужно вставить перед каждым словом."""
    width = max(1, width)
    padding: dict[int, int] = {}
    col = 0
    for index, char in enumerate(chars):
        if char and not char.isspace() and (index == 0 or chars[index - 1].isspace()):
            end = index
            while end < len(chars) and not chars[end].isspace():
                end += 1
            word_width = visible_width("".join(chars[index:end]))
            if col and word_width <= width and col + word_width > width:
                if width > col:
                    padding[index] = width - col
                col = width
        char_width = visible_width(char)
        if col + char_width > width:
            col = 0
        col += char_width
    return padding


def _word_wrapped_rows(text: str, width: int) -> int:
    chars = list(text)
    padding = _word_wrap_padding(chars, width)
    width = max(1, width)
    rows = 1
    col = 0
    for index, char in enumerate(chars):
        col += padding.get(index, 0)
        char_width = visible_width(char)
        if col + char_width > width:
            rows += 1
            col = 0
        col += char_width
    return rows


class WordWrapProcessor(Processor):
    """Переносит ввод по границам слов, не меняя текст Buffer.

    Штатный ``Window(wrap_lines=True)`` переносит строку ровно по
    ширине окна и режет последнее слово. Здесь перед словом,
    которое не помещается в остаток строки, добавляются только
    визуальные пробелы. Их нет ни в отправляемом тексте, ни в истории.
    """

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        fragments = explode_text_fragments(ti.fragments)
        chars = [fragment[1] for fragment in fragments]
        width = max(1, ti.width)
        padding_by_index = _word_wrap_padding(chars, width)
        result: list[tuple] = []
        # Позиция исходного display-текста -> позиция с мягкими отступами.
        source_to_display = [0] * (len(chars) + 1)
        display_to_source: list[int] = []
        col = 0

        for index, fragment in enumerate(fragments):
            char = fragment[1]
            source_to_display[index] = len(result)

            padding = padding_by_index.get(index, 0)
            if padding:
                style = fragment[0]
                for _ in range(padding):
                    result.append((style, " "))
                    display_to_source.append(index)
                col = width

            char_width = visible_width(char)
            if col + char_width > width:
                col = 0
            result.append(fragment)
            display_to_source.append(index)
            col += char_width

        source_to_display[len(chars)] = len(result)
        display_to_source.append(len(chars))
        return Transformation(
            result,
            source_to_display=lambda i: source_to_display[min(i, len(chars))],
            display_to_source=lambda i: display_to_source[min(i, len(display_to_source) - 1)],
        )


def term_size() -> tuple[int, int]:
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except OSError:
        return 80, 24


def detect_color_depth() -> ColorDepth:
    """Глубина цвета для Application.

    Критично: без явного значения prompt_toolkit роняет 24-битный цвет до
    256-цветной палитры (#9ece6a превращается в #afd75f). Rich при этом печатает
    в scrollback точный truecolor — и одна и та же тема начинает выглядеть
    по-разному внутри рамки и над ней. Поэтому глубину задаём сами.
    """
    if os.environ.get("NECLI_COLOR_DEPTH"):
        name = os.environ["NECLI_COLOR_DEPTH"].strip().lower()
        table = {
            "1": ColorDepth.DEPTH_1_BIT, "mono": ColorDepth.DEPTH_1_BIT,
            "4": ColorDepth.DEPTH_4_BIT, "16": ColorDepth.DEPTH_4_BIT,
            "8": ColorDepth.DEPTH_8_BIT, "256": ColorDepth.DEPTH_8_BIT,
            "24": ColorDepth.DEPTH_24_BIT, "truecolor": ColorDepth.DEPTH_24_BIT,
        }
        if name in table:
            return table[name]
    colorterm = (os.environ.get("COLORTERM") or "").lower()
    if colorterm in ("truecolor", "24bit"):
        return ColorDepth.DEPTH_24_BIT
    term = (os.environ.get("TERM") or "").lower()
    if "256" in term or "direct" in term:
        return ColorDepth.DEPTH_8_BIT
    if not term or term == "dumb":
        return ColorDepth.DEPTH_1_BIT
    return ColorDepth.DEPTH_4_BIT


def color_system_for(depth: ColorDepth) -> str:
    """Соответствующий color_system для Rich, чтобы обе половины экрана
    (динамика внутри Application и статика в scrollback) совпадали."""
    return {
        ColorDepth.DEPTH_1_BIT: "standard",
        ColorDepth.DEPTH_4_BIT: "standard",
        ColorDepth.DEPTH_8_BIT: "256",
        ColorDepth.DEPTH_24_BIT: "truecolor",
    }.get(depth, "truecolor")


# ───────────────────────────── мост Rich → ptk ──────────────────────────────
class RichBridge:
    """Рендерит Rich-объекты в ANSI-строки для показа внутри Application."""

    def __init__(self, color_system: str = "truecolor") -> None:
        self.color_system = color_system

    def to_ansi(self, renderable: Any, width: int) -> str:
        if renderable is None:
            return ""
        if isinstance(renderable, str):
            return renderable
        buf = io.StringIO()
        con = Console(
            file=buf, width=max(8, width), force_terminal=True,
            color_system=self.color_system, soft_wrap=False,
            legacy_windows=False, highlight=False,
        )
        try:
            con.print(renderable, end="")
        except Exception:
            logger.debug("rich→ansi failed", exc_info=True)
            return ""
        return buf.getvalue()


def ansi_rows(s: str) -> int:
    """Сколько строк займёт готовый ANSI-текст."""
    s = s.rstrip("\n")
    return s.count("\n") + 1 if s else 0


def _renderable_ends_blank(renderable: Any) -> bool:
    """Оставит ли Rich-объект после себя пустую строку в scrollback.

    Нужно, чтобы отбивка перед динамической зоной не удваивалась: агент сам
    печатает пустую строку-разделитель перед каждым элементом хода, и своя
    отбивка зоны поверх неё давала бы две пустые подряд.
    """
    if renderable is None:
        return True
    if isinstance(renderable, str):
        return not renderable.strip()
    plain = getattr(renderable, "plain", None)          # rich.text.Text
    if isinstance(plain, str):
        return not plain.strip()
    parts = getattr(renderable, "renderables", None)    # rich.console.Group
    if parts:
        return _renderable_ends_blank(parts[-1])
    return False


def _raw_ends_blank(text: str) -> bool:
    """То же для готовой строки с ANSI (её пишут байт в байт, без Rich)."""
    if not text.endswith("\n"):
        return False
    return not text[:-1].rsplit("\n", 1)[-1].strip()


# ───────────────────────────────── оверлеи ──────────────────────────────────
class Overlay:
    """Виджет, временно забирающий нижнюю зону (между линиями рамки).

    Один за раз — никогда два одновременно. Подклассы описывают только то, как
    себя рисовать и как реагировать на клавиши; жизненным циклом и фокусом
    управляет Shell.
    """

    #: Нужен ли оверлею буфер свободного ввода (фильтр / поле текста).
    wants_text: bool = False
    #: Пустые ряды между scrollback и телом конкретного оверлея.
    top_margin_rows: int = 0
    #: Разрешить оверлею больше половины экрана (всю свободную высоту).
    expand_height: bool = False
    #: После закрытия вернуть компактный кадр ввода к нижнему краю терминала.
    restore_input_to_bottom: bool = False

    def __init__(self) -> None:
        self.shell: Shell | None = None
        self.future: asyncio.Future | None = None

    # ── то, что переопределяют подклассы ──
    def render(self, width: int) -> Any:
        """Rich-объект или готовая ANSI-строка для тела оверлея."""
        raise NotImplementedError

    def hint(self) -> str:
        """Строка подсказок под нижней линией рамки."""
        return ""

    def handle_key(self, key: str, event) -> bool:
        """True — клавиша обработана и дальше не идёт."""
        return False

    def on_text_changed(self, text: str) -> None:
        """Вызывается при изменении буфера ввода (если wants_text)."""

    def version(self) -> Any:
        """Метка состояния для кэша отрисовки.

        Пока метка не меняется, Shell считает, что `render()` вернёт то же
        самое, и НЕ дёргает Rich повторно — большая таблица (`/models`) иначе
        перерисовывалась бы на каждом кадре тикера. `None` означает «не знаю»:
        тогда кэш живёт ровно один кадр (это всё равно вдвое меньше рендеров,
        чем было, — раньше кадр считался дважды: лямбдой высоты и контролом).
        """
        return None

    # ── служебное ──
    def finish(self, result: Any) -> None:
        if self.future is not None and not self.future.done():
            self.future.set_result(result)

    @property
    def text(self) -> str:
        return self.shell.overlay_buffer.text if self.shell else ""

    @text.setter
    def text(self, value: str) -> None:
        if self.shell:
            self.shell.overlay_buffer.text = value

    def invalidate(self) -> None:
        if self.shell:
            self.shell.invalidate()

    def edit_text_key(self, key: str) -> bool:
        """Применить обычную клавишу редактора к тексту оверлея.

        Текстовые панели рисуют поле сами (в том числе маскируют API-ключи),
        поэтому prompt_toolkit не может отдать им стандартные биндинги
        ``BufferControl``. Один общий набор операций нужен и poll, и прочим
        ask_text-полям.
        """
        if self.shell is None:
            return False
        return _edit_buffer_key(self.shell.overlay_buffer, key)


# ─────────────────────────────── сам Shell ──────────────────────────────────
class Shell:
    """Единственный Application. Создаётся один раз за процесс."""

    _instance: Shell | None = None

    def __init__(self, working_dir: str = ".") -> None:
        depth = detect_color_depth()
        self.color_depth = depth
        self.bridge = RichBridge(color_system_for(depth))
        # Пишем в РЕАЛЬНЫЙ stdout мимо подмены patch_stdout: та превращает
        # ESC-байт в "?", и цвета печатались бы текстом ("?[38;2;...m").
        self.term = Console(
            file=sys.__stdout__, force_terminal=True,
            color_system=color_system_for(depth), soft_wrap=False,
        )

        self.submissions: asyncio.Queue = asyncio.Queue()
        self.app: Application | None = None
        self._stopped = asyncio.Event()
        self._ticker_task: asyncio.Task | None = None

        # ── содержимое зон ──
        self._dynamic: dict[str, Any] = {}     # ключ → Rich-объект
        self._dynamic_order: list[str] = []    # порядок показа
        self._dynamic_version: int = 0         # растёт при каждой смене состава зоны
        self._status_text: str = ""
        # Сообщения slash-команд живут только в динамической нижней зоне.
        self._notice_text: str = ""
        # Дедлайн подтверждения выхода по Ctrl+D (time.monotonic); None — нет.
        self._confirm_exit_until: float | None = None

        # ── кэш отрисовки зон ──
        # Ключ кэша — (номер кадра, ширина, версия содержимого). Без него
        # `_dynamic_ansi`/`_overlay_ansi` считались ДВАЖДЫ за кадр: сначала
        # лямбдой высоты окна, потом контролом содержимого. При тикере 10 fps
        # это 20 полных рендеров Rich в секунду на каждую зону.
        self._dyn_cache: tuple[Any, str] | None = None
        self._ovl_cache: tuple[Any, str] | None = None

        # ── очередь статики ──
        # Каждый `run_in_terminal` стирает весь кадр приложения, печатает и
        # рисует заново. Во время стрима мелкие печати идут пачками, и кадр
        # мигал на каждой. Копим их и сбрасываем одним заходом.
        self._static_queue: deque[Callable[[], None]] = deque()
        self._static_flush_pending: bool = False
        #: Закончился ли последний вывод в scrollback пустой строкой. Нужно,
        #: чтобы отбивка перед динамической зоной не удваивалась.
        self._static_tail_blank: bool = False
        self._rows: dict[str, RowGroup] = {}   # строки под рамкой
        self._rows_order: list[str] = []
        # Фокус и начало окна считаются в координатах ПОЛНОГО списка. На экране
        # одновременно живут только ROW_WINDOW_SIZE строк; стрелки двигают
        # окно вслед за выбранным агентом/фоновой задачей.
        self._row_focus: int = -1              # -1 = фокус на вводе
        self._row_window_start: int = 0
        self._queued_messages: list[str] = []

        self.overlay: Overlay | None = None
        self._overlay_stack: list[Overlay] = []

        self.mode: str = "agent"
        self.on_mode_toggle: Callable[[str], None] | None = None
        self.status_provider: Callable[[], str] | None = None
        self.on_ctrl_o: Callable[[], None] | None = None
        self.on_edit_queued: Callable[[], str | None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

        self._build_buffers(working_dir)
        self._build_keys()
        self._build_layout()

    # ── singleton ──
    @classmethod
    def instance(cls) -> Shell | None:
        return cls._instance

    @classmethod
    def set_instance(cls, shell: Shell | None) -> None:
        cls._instance = shell

    # ──────────────────────────── буферы ввода ─────────────────────────────
    def _build_buffers(self, working_dir: str) -> None:
        from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
        from prompt_toolkit.history import FileHistory, ThreadedHistory

        import config
        from ui.completer import make_combined_completer

        self.completer, self.file_completer = make_combined_completer(working_dir)
        history_file = str(config.BASE_DIR / "history")
        self._history_file = history_file
        self.input_buffer = Buffer(
            name="input",
            completer=self.completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=ThreadedHistory(FileHistory(history_file)),
            multiline=True,     # перенос по "\" + Enter; Enter отправляет сам
            accept_handler=None,
        )
        # Автодополнение считается фоновой задачей и заканчивается ПОСЛЕ того,
        # как кадр по нажатой клавише уже нарисован. Штатный обработчик ptk на
        # этом месте зовёт `Application.invalidate()`, но тот молча выходит,
        # пока предыдущая перерисовка ещё не выполнена (`_invalidated` = True).
        # Кто успел раньше — гонка: список то появлялся, то нет. Поэтому
        # просим перерисовку на СЛЕДУЮЩЕЙ итерации loop'а, когда флаг снят.
        self.input_buffer.on_completions_changed += self._on_completions_changed

        # Отдельный буфер для оверлеев: фильтр в меню, поле ввода в ask_text.
        self.overlay_buffer = Buffer(name="overlay", multiline=False)
        self.overlay_buffer.on_text_changed += self._on_overlay_text

    def _on_completions_changed(self, _buf) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            self.invalidate()
            return
        loop.call_soon(self.invalidate)

    def _restart_completion(self) -> None:
        """Перезапустить подбор вариантов после правки буфера НЕ вставкой.

        `complete_while_typing` живёт внутри `Buffer.insert_text`, поэтому
        backspace и delete гасили список навсегда: стёр опечатку — и
        автодополнение больше не возвращалось до следующего символа.
        """
        buf = self.input_buffer
        if buf.completer is None or not buf.complete_while_typing():
            return
        if self.overlay is not None or not buf.text:
            return
        try:
            buf.start_completion(select_first=False)
        except Exception:
            logger.debug("start_completion failed", exc_info=True)

    def _on_overlay_text(self, _buf) -> None:
        if self.overlay is not None:
            try:
                self.overlay.on_text_changed(self.overlay_buffer.text)
            except Exception:
                logger.debug("overlay.on_text_changed failed", exc_info=True)
            self.invalidate()

    def set_working_dir(self, path: str) -> None:
        try:
            self.file_completer.set_working_dir(path)
        except Exception:
            logger.debug("set_working_dir failed", exc_info=True)

    # ──────────────────────────── динамическая зона ────────────────────────
    def set_dynamic(self, key: str, renderable: Any) -> None:
        """Показать renderable в динамической зоне под ключом key.

        Повторный вызов с тем же ключом заменяет содержимое — область
        перерисовывается на месте, в scrollback ничего не попадает.

        Можно передать **функцию без аргументов**: тогда она вызывается на
        каждом кадре. Это прямая замена `Live(get_renderable=...)` — спиннеры и
        счётчики продолжают тикать сами, без ручных обновлений.
        """
        if renderable is None:
            self.clear_dynamic(key)
            return
        if key not in self._dynamic:
            self._dynamic_order.append(key)
        self._dynamic[key] = renderable
        self._dynamic_version += 1
        # При открытом меню динамика всё равно скрыта. Не будим Application на
        # каждую SSE-дельту: даже пустой redraw заметно мигает в некоторых
        # терминалах. После закрытия run_overlay сам нарисует последний кадр.
        if self.overlay is None:
            self.invalidate()

    def clear_dynamic(self, key: str) -> None:
        if key in self._dynamic:
            del self._dynamic[key]
            self._dynamic_order = [k for k in self._dynamic_order if k != key]
            self._dynamic_version += 1
            if self.overlay is None:
                self.invalidate()

    def _resolve(self, value: Any) -> Any:
        """Разворачивает callable-содержимое зоны (аналог Live.get_renderable)."""
        if callable(value):
            try:
                return value()
            except Exception:
                logger.debug("dynamic provider failed", exc_info=True)
                return None
        return value

    def _frame_id(self) -> int:
        """Номер текущего кадра приложения.

        prompt_toolkit увеличивает `render_counter` ровно один раз на кадр, до
        того как раскладка начнёт спрашивать высоты и содержимое. Это и есть
        естественный ключ «за кадр посчитали один раз».
        """
        app = self.app
        if app is None:
            return -1
        return getattr(app, "render_counter", -1)

    def _animating(self) -> bool:
        """Есть ли в динамической зоне что-то, что меняется само по себе.

        Callable — прямой аналог `Live(get_renderable=...)`: спиннер, счётчик
        времени. Готовый Rich-объект сам по себе не меняется, его обновляет
        повторный `set_dynamic` (а он и так зовёт invalidate).
        """
        return self.overlay is None and any(callable(v) for v in self._dynamic.values())

    def _dynamic_ansi(self) -> str:
        # Открытое меню должно быть геометрически неподвижно. Агент продолжает
        # работать, но живой кадр вернётся после закрытия оверлея.
        if not self._dynamic or self.overlay is not None:
            return ""
        w = self._width()
        key = (self._frame_id(), w, self._dynamic_version, self._static_tail_blank)
        cached = self._dyn_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        # Working всегда последним: это стабильный блок, ближайший к полю
        # ввода; стримящийся ответ и другие динамические элементы живут выше.
        ordered = [k for k in self._dynamic_order if k != "working"]
        if "working" in self._dynamic:
            ordered.append("working")
        parts = [
            (k, self.bridge.to_ansi(self._resolve(self._dynamic[k]), w))
            for k in ordered
        ]
        chunks: list[str] = []
        for dynamic_key, part in parts:
            if not part:
                continue
            # Мысли/частичный инструмент живут над Working. Между этими
            # смысловыми блоками нужен пустой ряд, иначе нижняя строка мыслей
            # визуально слипается с заголовком Working.
            if dynamic_key == "working" and chunks:
                chunks.append("\n")
            chunks.append(part if part.endswith("\n") else part + "\n")
        body = "".join(chunks).rstrip("\n")
        if body:
            # Отбивка от scrollback — ровно одна пустая строка, независимо от
            # того, поставил ли её сам виджет: часть кадров приходит с ведущей
            # пустой, часть без, и зоны то слипались со статикой, то давали
            # двойной пробел. Если статика уже закончилась пустой строкой
            # (например эхо реплики), своей не добавляем — иначе их станет две.
            body = body.lstrip("\n")
            body = body if self._static_tail_blank else "\n" + body
        self._dyn_cache = (key, body)
        return body

    # ──────────────────────────── статика в scrollback ─────────────────────
    def _emit_static(self, write: Callable[[], None]) -> None:
        """Поставить печать в очередь так, чтобы она вклинилась НАД рамкой.

        Тонкость с потоками: инструменты выполняются через
        `loop.run_in_executor`, то есть их вывод приходит из рабочего потока.
        `run_in_terminal` там падает (ему нужен running loop в этом потоке), а
        прямая запись в stdout легла бы поверх рамки и порвала её. Поэтому из
        чужого потока перекидываем задание на loop приложения.

        Печатаем не сразу: `run_in_terminal` на каждый вызов стирает весь кадр
        приложения и рисует его заново. Во время стрима таких вызовов идут
        десятки подряд, и это ровно то мерцание, на которое жалуется заказчик.
        Копим их в очереди и сбрасываем одним заходом — порядок сохраняется,
        потому что очередь FIFO и пополняется только из loop-потока.
        """
        if self.app is None:
            write()
            return
        loop = self._loop
        if loop is not None and not loop.is_closed():
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not loop:
                loop.call_soon_threadsafe(lambda: self._emit_static(write))
                return
        if get_app_or_none() is None:
            write()
            return
        self._static_queue.append(write)
        # run_in_terminal стирает и заново рисует весь Application. Во время
        # меню копим готовые блоки агента и печатаем одной пачкой после выхода.
        if self.overlay is not None:
            return
        self._schedule_static_flush()

    def _schedule_static_flush(self) -> None:
        if self._static_flush_pending:
            return
        self._static_flush_pending = True
        try:
            run_in_terminal(self._drain_static)
        except Exception:
            # Application уже не владеет терминалом — печатаем как есть.
            self._static_flush_pending = False
            self._drain_static()

    def _drain_static(self) -> None:
        """Вылить всю накопленную статику внутри ОДНОГО run_in_terminal."""
        queue = self._static_queue
        while queue:
            write = queue.popleft()
            try:
                write()
            except Exception:
                logger.debug("static write failed", exc_info=True)
        # Флаг снимаем последним: до этого момента новые печати попадают в ту
        # же очередь и выльются здесь же, без второго стирания кадра.
        self._static_flush_pending = False

    def print_static(self, renderable: Any) -> None:
        """Напечатать в scrollback НАД рамкой — навсегда.

        Вне активного Application (старт/выход) печатаем напрямую.
        """
        self._static_tail_blank = _renderable_ends_blank(renderable)

        def _do() -> None:
            try:
                self.term.print(renderable)
            except Exception:
                logger.debug("print_static failed", exc_info=True)

        self._emit_static(_do)

    def ensure_static_blank(self) -> None:
        """Оставить в конце scrollback ровно одну смысловую пустую строку.

        Несколько соседних компонентов могут требовать одну и ту же отбивку
        (например низ строки времени одновременно является верхом recap).
        Повторный вызов поэтому ничего не печатает.
        """
        if not self._static_tail_blank:
            self.print_static("")

    def print_static_raw(self, text: str) -> None:
        """Как print_static, но пишет уже готовую строку (в т.ч. с ANSI) без
        обработки Rich-разметки. Нужно для эха ввода и replay."""
        self._static_tail_blank = _raw_ends_blank(text)

        def _do() -> None:
            try:
                sys.__stdout__.write(text)
                sys.__stdout__.flush()
            except Exception:
                logger.debug("print_static_raw failed", exc_info=True)

        self._emit_static(_do)

    # ──────────────────────────── статус-строка ────────────────────────────
    def set_status(self, text: str) -> None:
        self._status_text = text or ""
        # Рамка статуса скрыта внутри оверлея. Обновляем значение, но не
        # перерисовываем неподвижное меню из-за фоновой активности агента.
        if self.overlay is None:
            self.invalidate()

    # ─────────────────── queued messages ───────────────────
    def set_queued_messages(self, messages: list[str]) -> None:
        """Replace the editable queue snapshot shown immediately above input."""
        clean = [str(message) for message in messages if str(message).strip()]
        if clean == self._queued_messages:
            return
        self._queued_messages = clean
        self.invalidate()

    def _queued_capacity(self) -> int:
        """Keep queued rows inside the lower half on short terminals."""
        rows = self._term_rows()
        reserved = self._input_height() + 2 + self._below_height() + 1
        return max(1, min(12, (rows + 1) // 2 - reserved))

    def _visible_queued_messages(self) -> list[tuple[str, bool]]:
        if self.overlay is not None or not self._queued_messages:
            return []
        cap = self._queued_capacity()
        hidden = max(0, len(self._queued_messages) - cap)
        if not hidden:
            return [(message, False) for message in self._queued_messages]
        from config.i18n import t as tr
        if cap == 1:
            return [(tr("queue.more", n=hidden + 1), True)]
        visible = self._queued_messages[-(cap - 1):]
        return [
            (tr("queue.more", n=hidden), True),
            *((message, False) for message in visible),
        ]

    def _queued_fragments(self):
        rows = self._visible_queued_messages()
        out: list[tuple[str, str]] = []
        width = max(1, self._width() - 4)
        for index, (message, summary) in enumerate(rows):
            if index:
                out.append(("", "\n"))
            text = " ".join(message.split())
            out.append(("class:queue", "  ❯ "))
            out.append(("class:queue.summary" if summary else "class:queue.text",
                        clip_visible(text, width)))
        return out

    def _queued_height(self) -> int:
        return len(self._visible_queued_messages())

    def _queue_edit_hint(self) -> str:
        if self.overlay is not None or self.input_buffer.text or not self._queued_messages:
            return ""
        from config.i18n import t as tr
        return tr("queue.edit_hint")

    # ──────────────────────── строки под рамкой (субагенты) ────────────────
    def attach_rows(self, key: str, group: RowGroup) -> None:
        group.shell = self
        if key not in self._rows:
            self._rows_order.append(key)
        self._rows[key] = group
        self.invalidate()

    def detach_rows(self, key: str) -> None:
        if key in self._rows:
            try:
                removed = self._rows_order.index(key)
            except ValueError:
                removed = -1
            del self._rows[key]
            self._rows_order = [k for k in self._rows_order if k != key]
            if removed >= 0 and self._row_focus >= 0:
                if removed < self._row_focus:
                    self._row_focus -= 1
                elif removed == self._row_focus:
                    self._row_focus = -1
            if self._row_focus >= len(self._rows_order):
                self._row_focus = max(-1, len(self._rows_order) - 1)
            self._row_window_start = min(
                self._row_window_start,
                max(0, len(self._rows_order) - ROW_WINDOW_SIZE),
            )
            self.invalidate()

    def _all_rows(self) -> list[RowGroup]:
        return [self._rows[k] for k in self._rows_order if k in self._rows]

    def _visible_row_entries(self) -> list[tuple[int, RowGroup]]:
        rows = self._all_rows()
        total = len(rows)
        if not total:
            self._row_window_start = 0
            return []
        start = min(self._row_window_start, max(0, total - ROW_WINDOW_SIZE))
        if self._row_focus >= 0:
            if self._row_focus < start:
                start = self._row_focus
            elif self._row_focus >= start + ROW_WINDOW_SIZE:
                start = self._row_focus - ROW_WINDOW_SIZE + 1
        start = max(0, min(start, max(0, total - ROW_WINDOW_SIZE)))
        self._row_window_start = start
        return list(enumerate(rows[start:start + ROW_WINDOW_SIZE], start=start))

    def _visible_rows(self) -> list[RowGroup]:
        return [group for _index, group in self._visible_row_entries()]

    def _hidden_row_summary(self) -> str:
        rows = self._all_rows()
        visible_indices = {i for i, _group in self._visible_row_entries()}
        hidden = [group for i, group in enumerate(rows) if i not in visible_indices]
        if not hidden:
            return ""
        agents = sum(group.summary_count for group in hidden if group.kind == "agent")
        tasks = sum(group.summary_count for group in hidden if group.kind == "task")
        other = sum(group.summary_count for group in hidden if group.kind not in ("agent", "task"))
        from config.i18n import t as tr
        if agents and tasks:
            return tr("rows.more_both", agents=agents, tasks=tasks)
        if agents:
            return tr("rows.more_agents", n=agents)
        if tasks:
            return tr("rows.more_tasks", n=tasks)
        return tr("rows.more_items", n=other or len(hidden))

    # ──────────────────────────── оверлеи ──────────────────────────────────
    async def run_overlay(self, overlay: Overlay) -> Any:
        """Отдать нижнюю зону оверлею и дождаться его результата.

        Оверлеи складываются в стек: вложенное меню поверх родительского
        возвращает управление ему же по esc.
        """
        loop = asyncio.get_running_loop()
        use_alternate_screen = (
            self.overlay is None
            and bool(getattr(overlay, "restore_input_to_bottom", False))
            and self.app is not None
            and get_app_or_none() is not None
        )
        overlay.shell = self
        overlay.future = loop.create_future()
        if self.overlay is not None:
            self._overlay_stack.append(self.overlay)
        self.overlay = overlay
        self.overlay_buffer.text = ""
        self._focus_for_overlay()
        if use_alternate_screen:
            try:
                # Полноэкранные журналы живут на alternate screen. При выходе
                # терминал сам восстанавливает исходный scrollback и позицию
                # компактного ввода — без физической вставки пустых строк.
                await run_in_terminal(self._enter_overlay_screen)
            except Exception:
                logger.debug("alternate overlay screen enter failed", exc_info=True)
                use_alternate_screen = False
                self.invalidate()
        else:
            self.invalidate()
        try:
            return await overlay.future
        finally:
            self.overlay = self._overlay_stack.pop() if self._overlay_stack else None
            self.overlay_buffer.text = ""
            self._focus_for_overlay()
            if use_alternate_screen and self.overlay is None:
                try:
                    await run_in_terminal(self._leave_overlay_screen)
                except Exception:
                    logger.debug("alternate overlay screen exit failed", exc_info=True)
                    if self._static_queue:
                        self._schedule_static_flush()
            elif self.overlay is None and self._static_queue:
                self._schedule_static_flush()
            self.invalidate()

    def _enter_overlay_screen(self) -> None:
        """Переключить полноэкранный журнал на отдельный экран терминала."""
        app = self.app
        if app is None:
            return
        app.output.enter_alternate_screen()
        app.renderer.reset(leave_alternate_screen=False)
        app.output.flush()

    def _leave_overlay_screen(self) -> None:
        """Вернуть scrollback и напечатать накопленную статику без разрыва."""
        app = self.app
        if app is None:
            return
        app.output.quit_alternate_screen()
        app.renderer.reset(leave_alternate_screen=False)
        if self._static_queue:
            self._drain_static()
        app.output.flush()

    def _focus_for_overlay(self) -> None:
        """Фокус всегда на окне ввода.

        Буфер оверлея намеренно НЕ показывается отдельным окном: оверлей сам
        рисует своё поле (и сам решает, маскировать ли символы). Иначе рядом с
        маской `••••` печаталась бы вторая, открытая копия того же текста —
        то есть API-ключ утекал бы на экран.
        """
        if self.app is None:
            return
        try:
            self.app.layout.focus(self.input_window)
        except Exception:
            logger.debug("focus switch failed", exc_info=True)

    # ──────────────────────────── раскладка ────────────────────────────────
    def _width(self) -> int:
        if self.app is not None:
            try:
                return self.app.output.get_size().columns
            except Exception:
                pass
        return term_size()[0]

    def _status_fragments(self):
        """Верхняя линия рамки со статусом внутри.

        Строка статуса приходит с невидимыми маркерами вокруг заполненной и
        пустой частей прогресс-бара контекста. Их надо разобрать на отдельные
        фрагменты, иначе бар потеряет двухцветность (▮ акцентом, ▯ приглушённо)
        — а маркеры ещё и напечатаются мусором и собьют ширину линии.
        """
        w = self._width()
        # Очередь уже видна отдельными строками над вводом. В этом
        # состоянии верхняя граница — чистая линия, без дублирующего
        # `queued N` и длинного статуса между списком и полем.
        if self._queued_messages and self.overlay is None:
            return [("class:frame", "─" * w)]
        text = self._notice_text or self._status_text
        if not text:
            return [("class:frame", "─" * w)]
        if self._notice_text:
            text = clip_visible(text, max(1, w - 6))

        from ui.formatting import (
            BAR_EMPTY_END,
            BAR_EMPTY_START,
            BAR_FILLED_END,
            BAR_FILLED_START,
        )

        head = "─── "
        has_bar = all(m in text for m in
                      (BAR_FILLED_START, BAR_FILLED_END, BAR_EMPTY_START, BAR_EMPTY_END))
        if not has_bar:
            tail = max(0, w - len(head) - visible_width(text) - 1)
            return [("class:frame", head), ("class:status", text),
                    ("class:frame", " " + "─" * tail)]

        rest = text
        before, rest = rest.split(BAR_FILLED_START, 1)
        filled, rest = rest.split(BAR_FILLED_END, 1)
        _skip, rest = rest.split(BAR_EMPTY_START, 1)
        empty, after = rest.split(BAR_EMPTY_END, 1)
        used = (len(head) + visible_width(before) + visible_width(filled)
                + visible_width(empty) + visible_width(after) + 1)
        return [
            ("class:frame", head),
            ("class:status", before),
            ("class:bar-filled", filled),
            ("class:bar-empty", empty),
            ("class:status", after),
            ("class:frame", " " + "─" * max(0, w - used)),
        ]

    def _line_fragments(self):
        return [("class:frame", "─" * self._width())]

    def _mode_prompt(self) -> str:
        if self.mode == "planning":
            return "🧠plan"
        if self.mode == "swarm":
            return "🔮swarm"
        return "🚀agent"

    def _prompt_fragments(self):
        if self.overlay:
            return []
        out = [("class:mode", self._mode_prompt() + " "), ("class:arrow", "❯ ")]
        hint = self._queue_edit_hint()
        if hint:
            out.append(("class:queue.hint", clip_visible(hint, max(1, self._width() - 2))))
        return out

    def _prompt_width(self) -> int:
        if self.overlay:
            return 0
        return sum(visible_width(text) for _style, text in self._prompt_fragments())

    def _term_rows(self) -> int:
        if self.app is not None:
            try:
                return self.app.output.get_size().rows
            except Exception:
                pass
        return term_size()[1]

    def overlay_budget(self) -> int:
        """Сколько строк оверлей может занять, не выдавив раскладку за экран.

        Оверлеи со списками обязаны сами резать содержимое под этот бюджет
        (виджет со скроллом), иначе prompt_toolkit упрётся в «Window too small».
        Ниже стоит ещё и страховочная обрезка — на случай, если кто-то забыл.
        """
        used = (
            ansi_rows(self._dynamic_ansi())   # динамическая зона
            + self._top_gap_rows()            # ответ/thinking или отступ оверлея
            + 2 * self._frame_height()        # рамка только у обычного ввода
            + self._below_height()            # подсказка оверлея
            + 1                               # обязательная пустая строка
        )
        rows = self._term_rows()
        available = rows - used - 1
        # Меню с большим превью (например /themes) может занять всю свободную
        # высоту: ввод всё равно заменён оверлеем, а превью важнее сохранения
        # точки привязки выше середины.
        if getattr(self.overlay, "expand_height", False):
            return max(3, available)
        # Виджет вместе с подсказкой и нижней пустой строкой занимает
        # не более нижней половины экрана. Большой список прокручивается внутри,
        # а не выталкивает ввод/точку привязки выше середины.
        half_screen = (rows + 1) // 2
        capped = half_screen - self._below_height() - 1
        return max(3, min(available, capped))

    def _confirm_exit_active(self) -> bool:
        return (self._confirm_exit_until is not None
                and time.monotonic() < self._confirm_exit_until)

    def _overlay_ansi(self) -> str:
        overlay = self.overlay
        if overlay is None:
            return ""
        w = self._width()
        budget = self.overlay_budget()
        try:
            version = overlay.version()
        except Exception:
            logger.debug("overlay.version failed", exc_info=True)
            version = None
        # Пока виджет сообщает неизменную версию, повторно Rich не дёргаем
        # вообще: большая таблица `/models` иначе рендерилась бы на каждом
        # кадре. Без версии (None) кэш живёт один кадр — этого достаточно,
        # чтобы лямбда высоты и контрол содержимого делили один рендер.
        key = (id(overlay), version, w, budget,
               self._frame_id() if version is None else None)
        cached = self._ovl_cache
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            r = overlay.render(w)
        except Exception:
            logger.warning("overlay.render failed", exc_info=True)
            return ""
        text = r if isinstance(r, str) else self.bridge.to_ansi(r, w)
        lines = text.rstrip("\n").split("\n")
        if len(lines) > budget:
            # Страховка от «Window too small»: показываем сколько влезло и
            # честно сообщаем, что список обрезан.
            from config.i18n import t as tr
            keep = max(1, budget - 1)
            more = tr("shell.more_lines", n=len(lines) - keep)
            lines = [*lines[:keep], f"\x1b[2m  {more}\x1b[0m"]
        body = "\n".join(lines)
        self._ovl_cache = (key, body)
        return body

    def _overlay_height(self) -> int:
        return ansi_rows(self._overlay_ansi())

    def _below_fragments(self):
        """Под нижней линией: подсказка выхода, подсказка оверлея либо строки групп."""
        prefix: list[tuple[str, str]] = []
        if self._confirm_exit_active():
            # Пустая строка отбивает подсказку от нижней линии рамки.
            prefix = [("class:hint", "\nНажмите ctrl+d для выхода\n")]
        if self.overlay is not None:
            text = self._notice_text or self.overlay.hint()
            return prefix + ([("class:hint", "  " + text)] if text else [])
        out: list[tuple[str, str]] = []
        for absolute_index, group in self._visible_row_entries():
            focused = absolute_index == self._row_focus
            out.append((
                "class:row.sel" if focused else "class:row",
                ("❯ " if focused else "  ") + group.label() + "\n",
            ))
        summary = self._hidden_row_summary()
        if summary:
            out.append(("class:row.summary", f"  … {summary}\n"))
        return prefix + out

    def _below_height(self) -> int:
        extra = 2 if self._confirm_exit_active() else 0
        if self.overlay is not None:
            return extra + (1 if (self._notice_text or self.overlay.hint()) else 0)
        return extra + len(self._visible_rows()) + (1 if self._hidden_row_summary() else 0)

    def _frame_height(self) -> int:
        """У меню нет рамки поля ввода; у обычного ввода обе линии видимы."""
        return 0 if self.overlay is not None else 1

    def _dynamic_visible(self) -> bool:
        return bool(self._dynamic) and self.overlay is None

    def _dynamic_gap_rows(self) -> int:
        return 1 if self._dynamic_visible() else 0

    def _top_gap_rows(self) -> int:
        """Отбивка перед нижней зоной: один ряд после динамики либо отступ меню."""
        if self.overlay is not None:
            return max(0, int(getattr(self.overlay, "top_margin_rows", 0)))
        return self._dynamic_gap_rows()

    def _input_height(self) -> int:
        """Высота поля ввода в строках.

        Считаем явно: без этого Window с неограниченной высотой растягивается и
        выдавливает нижнюю линию рамки со строками субагентов за край экрана.
        """
        if self.overlay is not None:
            return 0
        # Поле ввода лежит справа от промпта ("🚀agent ❯"), поэтому перенос
        # происходит на ширине width - prompt_width, а не width - 2. Если
        # считать не ту ширину, строка, которую рендер уже завернул, попадает
        # в расчёт одной строкой — окно ввода оказывается ниже нужного и текст
        # уезжает на уровень промпта / за нижнюю линию рамки.
        avail = max(1, self._width() - self._prompt_width())
        rows = 0
        for line in (self.input_buffer.text or "").split("\n"):
            rows += _word_wrapped_rows(line, avail)
        return max(1, min(rows, 10))

    def _menu_max_height(self) -> int:
        """Потолок высоты списка автодополнения.

        Список растёт ВНИЗ и выталкивает рамку вверх, поэтому его надо
        ограничить дважды: половиной терминала (заказчик просил «до середины»)
        и реально свободными строками. Без второго ограничения на невысоком
        окне десять вариантов съедали весь экран вместе со scrollback'ом.
        """
        rows = self._term_rows()
        used = (
            ansi_rows(self._dynamic_ansi())   # динамическая зона
            + self._top_gap_rows()
            + 1                               # верхняя линия рамки
            + self._input_height()
            + self._overlay_height()
            + 1                               # нижняя линия рамки
            + self._below_height()
            + 1                               # обязательная пустая строка
        )
        # Верхняя линия поля не должна уезжать выше середины. Из нижней
        # половины вычитаем само поле, нижнюю линию, строки и отбивку.
        lift_cap = ((rows + 1) // 2 - self._input_height()
                    - 1 - self._below_height() - 1)
        return max(0, min(lift_cap, rows - used - 1))

    def _completions_menu(self):
        """Список автодополнения — обычный контейнер, не Float.

        В non-fullscreen у Float нет якоря, его пришлось бы позиционировать
        руками. Обычный контейнер под нижней линией рамки даёт ровно то, что
        просил заказчик: варианты выезжают ВНИЗ, а рамка ввода уезжает вверх.
        """
        return ConditionalContainer(
            content=Window(
                content=CompletionsMenuControl(),
                width=Dimension(min=8),
                height=(lambda: Dimension(min=1, max=max(1, self._menu_max_height()))),
                scroll_offsets=ScrollOffsets(top=1, bottom=1),
                right_margins=[ScrollbarMargin(display_arrows=False)],
                dont_extend_width=True,
                style="class:completion-menu",
                z_index=10**8,
            ),
            # Когда свободных строк нет вовсе, список не показываем: иначе он
            # выдавит саму рамку ввода за край экрана.
            filter=has_completions & ~is_done & Condition(lambda: self._menu_max_height() > 0),
        )

    def _build_layout(self) -> None:
        self.input_window = Window(
            BufferControl(
                buffer=self.input_buffer,
                focusable=True,
                # В PromptSession этот processor добавлялся автоматически.
                # После перехода на собственный BufferControl его не стало,
                # поэтому suggestion вычислялся, но серый хвост истории нигде
                # не рисовался.
                input_processors=[WordWrapProcessor(), AppendAutoSuggestion()],
            ),
            height=self._input_height,
            dont_extend_height=True,
            wrap_lines=True,
        )
        root = HSplit([
            # динамика: спиннер / thinking / частичный инструмент / активный блок
            Window(
                FormattedTextControl(lambda: ANSI(self._dynamic_ansi())),
                height=(lambda: ansi_rows(self._dynamic_ansi())),
                wrap_lines=False,
            ),
            # Только смысловая отбивка, без гибкого spacer: обычный ввод не
            # прижимается к низу. Во время ответа здесь ровно два пустых ряда.
            Window(height=self._top_gap_rows),
            # Реплики, отправленные во время активного хода. Они живут в
            # Application, а не в scrollback: `↑` сразу убирает строку.
            Window(
                FormattedTextControl(self._queued_fragments),
                height=self._queued_height,
                wrap_lines=False,
            ),
            # ─── верхняя линия рамки (внутри — статус) ───
            Window(
                FormattedTextControl(self._status_fragments),
                height=self._frame_height,
            ),
            # ─── средняя зона: ввод ИЛИ оверлей ───
            VSplit([
                Window(FormattedTextControl(self._prompt_fragments),
                       width=self._prompt_width,
                       height=self._input_height),
                self.input_window,
            ], height=self._input_height),
            Window(
                FormattedTextControl(lambda: ANSI(self._overlay_ansi())),
                height=self._overlay_height,
                wrap_lines=False,
            ),
            # ─── нижняя линия рамки ───
            Window(
                FormattedTextControl(self._line_fragments),
                height=self._frame_height,
            ),
            # список автодополнения — ПОД рамкой ввода
            self._completions_menu(),
            # строки субагентов ИЛИ подсказка оверлея
            Window(FormattedTextControl(self._below_fragments),
                   height=self._below_height, wrap_lines=False),
            # обязательная пустая строка снизу
            Window(height=1),
        ])
        self.layout = Layout(root)
        self.layout.focus(self.input_window)

    # ──────────────────────────── клавиши ──────────────────────────────────
    def _build_keys(self) -> None:
        kb = KeyBindings()
        self.kb = kb

        def overlay_takes(key: str, event) -> bool:
            if self.overlay is None:
                return False
            # В поле свободного ввода пробел — это символ, а не команда. Без
            # этой нормализации он приходит именем "space", не проходит проверку
            # `len(key) == 1` и молча теряется: "user name" → "username".
            # Оверлеям, где пробел действие (чекбоксы в poll), имя оставляем.
            if key == "space" and self.overlay.wants_text:
                key = " "
            try:
                return bool(self.overlay.handle_key(key, event))
            except Exception:
                logger.warning("overlay.handle_key failed", exc_info=True)
                return True

        @kb.add("escape")
        def _esc(event):
            if overlay_takes("escape", event):
                self.invalidate()
                return
            if self.overlay is not None:
                self.overlay.finish(None)
            self.invalidate()

        @kb.add("up", eager=True)
        def _up(event):
            if overlay_takes("up", event):
                self.invalidate()
                return
            if self._row_focus >= 0:
                # со строк субагентов возвращаемся к вводу
                self._row_focus -= 1
                self.invalidate()
                return
            buf = self.input_buffer
            if buf.complete_state:
                buf.complete_previous()
            elif not buf.text and self._queued_messages and self.on_edit_queued:
                text = self.on_edit_queued()
                if text is not None:
                    buf.text = text
                    buf.cursor_position = len(text)
            else:
                buf.auto_up()
            self.invalidate()

        @kb.add("down", eager=True)
        def _down(event):
            if overlay_takes("down", event):
                self.invalidate()
                return
            groups = self._all_rows()
            buf = self.input_buffer
            if self._row_focus >= 0:
                self._row_focus = min(len(groups) - 1, self._row_focus + 1)
                self.invalidate()
                return
            if buf.complete_state:
                buf.complete_next()
                self.invalidate()
                return
            # Вниз из пустого ввода — переход на строки субагентов. Если в
            # буфере текст, вниз остаётся навигацией по истории: иначе
            # пользователь терял бы каретку посреди набора.
            if groups and not buf.text.strip():
                self._row_focus = 0
            else:
                buf.auto_down()
            self.invalidate()

        @kb.add("enter", eager=True)
        def _enter(event):
            if overlay_takes("enter", event):
                self.invalidate()
                return
            if self._row_focus >= 0:
                groups = self._all_rows()
                if 0 <= self._row_focus < len(groups):
                    groups[self._row_focus].open()
                self.invalidate()
                return
            buf = self.input_buffer
            # принятие подсказки автодополнения не должно отправлять строку
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                self.invalidate()
                return
            line = buf.document.current_line_before_cursor
            if line.endswith("\\"):
                buf.delete_before_cursor(count=1)
                buf.insert_text("\n")
                self.invalidate()
                return
            text = buf.text
            buf.text = ""
            self._submit_text(text)
            self.invalidate()

        @kb.add("escape", "enter", eager=True)
        def _newline(event):
            if self.overlay is None:
                self.input_buffer.insert_text("\n")
                self.invalidate()

        @kb.add("escape", "backspace", eager=True)
        def _alt_backspace(event):
            if overlay_takes("a-backspace", event):
                self.invalidate()
                return
            _default_buffer_key(self, "a-backspace", event)
            self._restart_completion()
            self.invalidate()

        @kb.add("c-c", eager=True)
        def _ctrl_c(event):
            if overlay_takes("c-c", event):
                self.invalidate()
                return
            if self.overlay is not None:
                self.overlay.finish(None)
                self.invalidate()
                return
            if self.input_buffer.text:
                self.input_buffer.reset()
            else:
                self.submissions.put_nowait((SUBMIT_INTERRUPT, None))
            self.invalidate()

        @kb.add("c-d", eager=True)
        def _ctrl_d(event):
            if overlay_takes("c-d", event):
                return
            if self.overlay is None and not self.input_buffer.text:
                if self._confirm_exit_active():
                    # Второе нажатие в течение 3 с — выход сразу.
                    self._confirm_exit_until = None
                    self.submissions.put_nowait((SUBMIT_EOF, None))
                else:
                    # Первое нажатие — показать подсказку и ждать 3 с.
                    self._confirm_exit_until = time.monotonic() + 3.0
                    self.invalidate()

        @kb.add("tab", eager=True)
        def _tab(event):
            if overlay_takes("tab", event):
                self.invalidate()
                return
            # Tab переключает режим ВСЕГДА, даже когда в поле уже что-то
            # набрано: так было до реворка и на это опираются пальцы. Варианты
            # автодополнения выбираются стрелками ↑↓ и принимаются Enter, а
            # появляются они сами (complete_while_typing); принудительный
            # вызов повешен на ctrl+space.
            order = ("agent", "planning", "swarm")
            idx = order.index(self.mode) if self.mode in order else 0
            self.mode = order[(idx + 1) % len(order)]
            if self.on_mode_toggle:
                try:
                    self.on_mode_toggle(self.mode)
                except Exception:
                    logger.debug("on_mode_toggle failed", exc_info=True)
            self.invalidate()

        @kb.add("c-space", eager=True)
        def _force_complete(event):
            """Явный вызов автодополнения — вместо отобранного Tab'а."""
            if overlay_takes("c-space", event):
                self.invalidate()
                return
            self._restart_completion()
            self.invalidate()

        @kb.add("c-o", eager=True)
        def _ctrl_o(event):
            if self.on_ctrl_o:
                run_in_terminal(self.on_ctrl_o)
            self.invalidate()

        # Клавиши, которые должны доезжать до оверлея как есть.
        for key in ("left", "right", "home", "end", "c-left", "c-right",
                    "c-a", "c-e", "c-w", "c-u", "c-k",
                    "pageup", "pagedown", "backspace", "delete", "c-delete", "c-p",
                    "c-n", "c-x", "c-s", "space", "f5"):
            def _make(k):
                def _h(event):
                    if overlay_takes(k, event):
                        self.invalidate()
                        return
                    # вне оверлея — обычное поведение буфера
                    _default_buffer_key(self, k, event)
                    if k in ("backspace", "delete", "c-delete"):
                        self._restart_completion()
                    self.invalidate()
                return _h
            kb.add(key, eager=True)(_make(key))

        @kb.add("<any>")
        def _any(event):
            if self.overlay is not None:
                data = event.data
                if self.overlay.wants_text:
                    self.overlay_buffer.insert_text(data)
                else:
                    overlay_takes(data, event)
                self.invalidate()
                return
            # Начал печатать, стоя на строке субагентов — возвращаем каретку в
            # поле ввода, иначе символы уходили бы в поле, а маркер «❯» висел
            # бы на строке: пользователь не понимает, куда он печатает.
            if self._row_focus >= 0:
                self._row_focus = -1
            self.input_buffer.insert_text(event.data)
            self.invalidate()

    def _submit_text(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._notice_text = ""
        kind = SUBMIT_SLASH if cleaned.startswith("/") else SUBMIT_USER
        self.submissions.put_nowait((kind, cleaned))
        try:
            self.input_buffer.history.append_string(cleaned)
        except Exception:
            logger.debug("history append failed", exc_info=True)
        self._trim_history()
        # Buffer держит _working_lines — снимок истории с первого рендера;
        # обновить его может только reset(). Без него вводы текущей сессии
        # не появляются в навигации ↑/↓ (остаётся только файл на момент старта).
        try:
            self.input_buffer.reset()
        except Exception:
            logger.debug("buffer reset failed", exc_info=True)

    def _trim_history(self) -> None:
        """Оставляет в истории ввода последние HISTORY_LIMIT записей.

        FileHistory из prompt_toolkit только дописывает файл и не имеет
        лимита; обрезаем и файл, и кэш ThreadedHistory, чтобы ↑/↓ не рос
        безгранично.
        """
        try:
            entries = list(self.input_buffer.history.load_history_strings())
            if len(entries) <= HISTORY_LIMIT:
                return
            keep = entries[:HISTORY_LIMIT]  # новые — первыми
            with open(self._history_file, "wb") as f:
                for entry in reversed(keep):  # в файле старые — снизу
                    f.write(f"\n# {datetime.now()}\n".encode())
                    for line in entry.split("\n"):
                        f.write(f"+{line}\n".encode())
            # Кэш в памяти тоже ограничиваем, иначе он продолжит расти.
            self.input_buffer.history._loaded_strings[:] = keep
        except Exception:
            logger.debug("history trim failed", exc_info=True)

    # ──────────────────────────── жизненный цикл ───────────────────────────
    def invalidate(self) -> None:
        if self.app is not None:
            try:
                self.app.invalidate()
            except Exception:
                pass

    def _style(self) -> Style:
        from config.themes import t
        return Style.from_dict({
            "frame": t("muted"),
            "status": f"bold {t('fg_primary')}",
            "mode": f"bold {t('accent')}",
            "arrow": f"bold {t('success')}",
            "hint": t("dim_text"),
            "queue": t("dim_text"),
            "queue.text": t("fg_primary"),
            "queue.summary": f"italic {t('dim_text')}",
            "queue.hint": t("dim_text"),
            "row": t("accent"),
            "row.sel": f"bold {t('fg_primary')} bg:{t('bg_select')}",
            "row.summary": f"italic {t('dim_text')}",
            "bar-filled": t("bar_filled"),
            "bar-empty": t("muted"),
            "auto-suggestion": t("dim_alt"),
            "completion-menu": "bg:default noinherit",
            "completion-menu.completion": f"bg:default {t('dim_alt')} noinherit",
            "completion-menu.completion.current": f"bg:default {t('accent')} noinherit",
            "completion-menu.meta.completion": f"bg:default {t('dim_alt')} noinherit",
            "completion-menu.meta.completion.current": f"bg:default {t('accent')} noinherit",
            "scrollbar.background": "bg:default noinherit",
            "scrollbar.button": "bg:default noinherit",
        })

    async def _ticker(self) -> None:
        """Крутит анимацию, пока в динамической зоне есть что анимировать.

        Раньше кадры двигал refresh у rich.live.Live; Live больше нет, поэтому
        такт задаём сами. Будим приложение ТОЛЬКО когда в зоне лежит callable
        (спиннер, счётчик времени): готовый Rich-объект сам не меняется, а
        десять лишних кадров в секунду поверх открытого оверлея — это и есть
        тормоза `/models` и мерцание, на которые жалуется заказчик.
        """
        while not self._stopped.is_set():
            if self._confirm_exit_until is not None:
                if time.monotonic() >= self._confirm_exit_until:
                    self._confirm_exit_until = None
                self.invalidate()
                await asyncio.sleep(0.1)
            elif self._animating():
                self.invalidate()
                await asyncio.sleep(0.1)
            else:
                await asyncio.sleep(0.25)

    def start(self) -> asyncio.Task:
        """Создать Application и запустить его как фоновую задачу."""
        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self._style(),
            color_depth=self.color_depth,
            full_screen=False,       # никакого alt-screen — иначе умрёт скроллбар
            mouse_support=False,     # иначе умрёт выделение мышью
            erase_when_done=False,
            refresh_interval=0,
        )
        # ESC — и отдельная команда, и префикс Esc+Enter. Стандартный
        # timeoutlen prompt_toolkit ждёт продолжение целую секунду.
        self.app.ttimeoutlen = 0.05
        self.app.timeoutlen = 0.12
        Shell.set_instance(self)
        # Запоминаем loop: печать из рабочих потоков инструментов должна
        # перекидываться сюда, иначе она ляжет поверх рамки.
        self._loop = asyncio.get_event_loop()
        self._ticker_task = asyncio.create_task(self._ticker(), name="shell-ticker")
        return asyncio.create_task(self.app.run_async(), name="shell-app")

    def print_exit_notice(self, text: str) -> None:
        """Стереть рамку ввода и напечатать финальное сообщение на её месте.

        После `app.exit()` кадр prompt_toolkit остаётся на экране
        (`erase_when_done=False`). Перед финальным сообщением его надо снять:
        поднимаемся на высоту кадра и очищаем всё до конца экрана.
        """
        try:
            rows = (self._input_height() + 2 * self._frame_height()
                    + self._below_height() + 1)
            sys.__stdout__.write(f"\r\x1b[{max(1, rows)}A\x1b[J")
            sys.__stdout__.write(str(text) + "\n")
            sys.__stdout__.flush()
        except Exception:
            logger.debug("print_exit_notice failed", exc_info=True)

    async def stop(self) -> None:
        self._stopped.set()
        if self._ticker_task is not None:
            self._ticker_task.cancel()
        # Всё, что успело попасть в очередь статики, но не дождалось своего
        # run_in_terminal, дописываем прямо сейчас — иначе последние строки
        # ответа просто исчезли бы при выходе.
        if self._static_queue:
            self._static_flush_pending = False
            self._drain_static()
        if self.app is not None:
            try:
                self.app.exit()
            except Exception:
                logger.debug("app.exit failed", exc_info=True)


def _edit_buffer_key(buf: Buffer, key: str) -> bool:
    """Readline-подобные операции редактирования произвольного Buffer."""
    if key == "left":
        buf.cursor_left()
    elif key == "right":
        buf.cursor_right()
    elif key == "up":
        buf.auto_up()
    elif key == "down":
        buf.auto_down()
    elif key == "home":
        buf.cursor_position += buf.document.get_start_of_line_position()
    elif key == "end":
        buf.cursor_position += buf.document.get_end_of_line_position()
    elif key == "c-left":
        offset = buf.document.find_previous_word_beginning(WORD=True)
        buf.cursor_position += offset if offset is not None else -buf.cursor_position
    elif key == "c-right":
        offset = buf.document.find_next_word_beginning(WORD=True)
        if offset is None:
            offset = len(buf.text) - buf.cursor_position
        buf.cursor_position += offset
    elif key == "c-a":
        buf.cursor_position += buf.document.get_start_of_line_position()
    elif key == "c-e":
        buf.cursor_position += buf.document.get_end_of_line_position()
    elif key == "backspace":
        buf.delete_before_cursor(1)
    elif key == "delete":
        buf.delete(1)
    elif key == "c-delete":
        # Ctrl+Delete — удалить слово ПОСЛЕ курсора (как emacs kill-word),
        # симметрично Alt+Backspace, который стирает слово перед курсором.
        offset = buf.document.find_next_word_beginning(WORD=True)
        if offset is None:
            offset = len(buf.text) - buf.cursor_position
        buf.delete(offset)
    elif key == "a-backspace":
        # Alt+Backspace — удалить слово перед курсором вместе с пробелами
        # (как readline backward-kill-word).
        offset = buf.document.find_start_of_previous_word(WORD=True)
        if offset is None:
            offset = -buf.cursor_position
        buf.delete_before_cursor(-offset)
    elif key == "c-w":
        # Ctrl+W — очистить всё левее курсора до начала строки.
        buf.delete_before_cursor(-buf.document.get_start_of_line_position())
    elif key == "c-u":
        buf.delete_before_cursor(-buf.document.get_start_of_line_position())
    elif key == "c-k":
        buf.delete(buf.document.get_end_of_line_position())
    elif key == "space":
        buf.insert_text(" ")
    else:
        return False
    return True


def _default_buffer_key(shell: Shell, key: str, event) -> None:
    """Поведение навигационных клавиш в обычном поле ввода."""
    buf = shell.input_buffer
    # Штатные bindings PromptSession принимали fish-style подсказку истории
    # стрелкой вправо. Собственный eager-handler Shell перекрыл их и оставил
    # только cursor_right(), который в конце строки ничего не делает.
    if (
        key == "right"
        and buf.suggestion is not None
        and buf.suggestion.text
        and buf.document.is_cursor_at_the_end
    ):
        buf.insert_text(buf.suggestion.text)
        return
    _edit_buffer_key(buf, key)


# ─────────────────────────── строки под рамкой ──────────────────────────────
class RowGroup:
    """Строка-индикатор под панелью ввода (например, группа субагентов).

    Появляется, пока группа жива; стрелкой вниз на неё встаёт фокус, Enter
    открывает связанный оверлей.
    """

    def __init__(
        self,
        label_fn: Callable[[], str],
        open_fn: Callable[[], None],
        *,
        kind: str = "agent",
        summary_count: int = 1,
    ) -> None:
        self._label_fn = label_fn
        self._open_fn = open_fn
        self.kind = kind
        self.summary_count = max(1, int(summary_count or 1))
        self.shell: Shell | None = None

    def label(self) -> str:
        try:
            return self._label_fn()
        except Exception:
            logger.debug("RowGroup.label failed", exc_info=True)
            return "?"

    def open(self) -> None:
        try:
            self._open_fn()
        except Exception:
            logger.warning("RowGroup.open failed", exc_info=True)


# ────────────────────────────── доступ извне ────────────────────────────────
def get_shell() -> Shell | None:
    return Shell.instance()


def print_static(renderable: Any) -> None:
    """Напечатать в scrollback. Работает и до создания Shell."""
    sh = Shell.instance()
    if sh is not None:
        sh.print_static(renderable)
        return
    Console(file=sys.__stdout__, force_terminal=True).print(renderable)


def ensure_static_blank() -> None:
    """Добавить пустую строку в scrollback, если последняя уже не пустая."""
    sh = Shell.instance()
    if sh is not None:
        sh.ensure_static_blank()
        return
    Console(file=sys.__stdout__, force_terminal=True).print()


def set_dynamic(key: str, renderable: Any) -> None:
    sh = Shell.instance()
    if sh is not None:
        sh.set_dynamic(key, renderable)


def clear_dynamic(key: str) -> None:
    sh = Shell.instance()
    if sh is not None:
        sh.clear_dynamic(key)


__all__ = [
    "SUBMIT_BG_RESUME",
    "SUBMIT_EOF",
    "SUBMIT_INTERRUPT",
    "SUBMIT_SLASH",
    "SUBMIT_TG",
    "SUBMIT_USER",
    "Overlay",
    "RowGroup",
    "Shell",
    "ansi_rows",
    "clear_dynamic",
    "ensure_static_blank",
    "get_shell",
    "print_static",
    "set_dynamic",
    "visible_width",
]
