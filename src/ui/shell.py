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
import logging
import sys
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.styles import Style
from rich.console import Console

from config._atomic import atomic_write_text
from config.constants import Limits
from ui.buffer_editing import edit_buffer_key as _edit_buffer_key
from ui.overlay import Overlay as Overlay
from ui.rendering import RichBridge as RichBridge
from ui.rendering import _raw_ends_blank as _raw_ends_blank
from ui.rendering import _renderable_ends_blank as _renderable_ends_blank
from ui.rendering import ansi_rows as ansi_rows
from ui.rows import RowGroup as RowGroup
from ui.shell_keys import ShellKeyBindingMixin
from ui.shell_layout import ShellLayoutMixin
from ui.shell_output import ShellOutputMixin
from ui.terminal import color_system_for as color_system_for
from ui.terminal import detect_color_depth as detect_color_depth
from ui.terminal import term_size as term_size
from ui.text_layout import (
    WordWrapProcessor as WordWrapProcessor,
)
from ui.text_layout import (
    _word_wrap_padding as _word_wrap_padding,
)
from ui.text_layout import (
    _word_wrapped_rows as _word_wrapped_rows,
)
from ui.text_layout import (
    clip_visible as clip_visible,
)
from ui.text_layout import (
    visible_width as visible_width,
)

__all__ = [
    "Overlay",
    "RichBridge",
    "RowGroup",
    "_edit_buffer_key",
    "_raw_ends_blank",
    "_renderable_ends_blank",
    "_word_wrap_padding",
    "_word_wrapped_rows",
    "ansi_rows",
    "clip_visible",
    "color_system_for",
    "detect_color_depth",
    "visible_width",
]

logger = logging.getLogger(__name__)

_STATIC_QUEUE_MAX = Limits.STATIC_QUEUE_MAX

# ─────────────────────────── типы сообщений из UI ───────────────────────────
# Единая воронка: и клавиатура, и Telegram, и пробуждение фоновой задачей
# кладут сюда одинаковые кортежи, а главный цикл разбирает их в одном месте.
import contextlib

from ui.submissions import (
    SUBMIT_BG_RESUME,
    SUBMIT_EOF,
    SUBMIT_INTERRUPT,
    SUBMIT_SLASH,
    SUBMIT_TG,
    SUBMIT_USER,
)


def _prune_history_file(path: str | Path, *, now: datetime | None = None) -> bool:
    """Удалить из prompt_toolkit FileHistory записи старше недели."""
    history_path = Path(path)
    try:
        lines = history_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return False

    records: list[tuple[str, datetime | None, list[str]]] = []
    timestamp_text = ""
    timestamp: datetime | None = None
    entry_lines: list[str] = []

    def finish_record() -> None:
        if timestamp_text:
            records.append((timestamp_text, timestamp, entry_lines.copy()))

    for line in lines:
        if line.startswith("# "):
            finish_record()
            timestamp_text = line[2:].strip()
            entry_lines = []
            try:
                timestamp = datetime.fromisoformat(timestamp_text)
            except ValueError:
                timestamp = None
        elif timestamp_text and line.startswith("+"):
            entry_lines.append(line[1:])
    finish_record()

    current = now or datetime.now()
    cutoff = current - timedelta(days=Limits.HISTORY_MAX_AGE_DAYS)

    def is_recent(record_time: datetime | None) -> bool:
        if record_time is None:
            return True
        comparable_cutoff = cutoff
        if record_time.tzinfo is not None and comparable_cutoff.tzinfo is None:
            comparable_cutoff = comparable_cutoff.astimezone(record_time.tzinfo)
        elif record_time.tzinfo is None and comparable_cutoff.tzinfo is not None:
            record_time = record_time.replace(tzinfo=comparable_cutoff.tzinfo)
        return record_time >= comparable_cutoff

    kept = [record for record in records if is_recent(record[1])]
    if len(kept) == len(records):
        return False

    output = "".join(
        f"\n# {stamp}\n" + "".join(f"+{line}\n" for line in entry)
        for stamp, _record_time, entry in kept
    )
    atomic_write_text(history_path, output)
    return True


# ───────────────────────────── мост Rich → ptk ──────────────────────────────


# ───────────────────────────────── оверлеи ──────────────────────────────────


# ─────────────────────────────── сам Shell ──────────────────────────────────
class Shell(ShellKeyBindingMixin, ShellLayoutMixin, ShellOutputMixin):
    """Единственный Application. Создаётся один раз за процесс."""

    _instance: Shell | None = None

    def __init__(self, working_dir: str = ".") -> None:
        depth = detect_color_depth()
        self.color_depth = depth
        self.bridge = RichBridge(color_system_for(depth))
        # Пишем в РЕАЛЬНЫЙ stdout мимо подмены patch_stdout: та превращает
        # ESC-байт в "?", и цвета печатались бы текстом ("?[38;2;...m").
        self.term = Console(
            file=sys.__stdout__,
            force_terminal=True,
            color_system=color_system_for(depth),
            soft_wrap=False,
        )

        self.submissions: asyncio.Queue = asyncio.Queue()
        self.app: Application | None = None
        self._stopped = asyncio.Event()
        self._ticker_task: asyncio.Task | None = None

        # ── содержимое зон ──
        self._dynamic: dict[str, Any] = {}  # ключ → Rich-объект
        self._dynamic_order: list[str] = []  # порядок показа
        self._dynamic_version: int = 0  # растёт при каждой смене состава зоны
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
        self._rows: dict[str, RowGroup] = {}  # строки под рамкой
        self._rows_order: list[str] = []
        # Фокус и начало окна считаются в координатах ПОЛНОГО списка. На экране
        # одновременно живут только ROW_WINDOW_SIZE строк; стрелки двигают
        # окно вслед за выбранным агентом/фоновой задачей.
        self._row_focus: int = -1  # -1 = фокус на вводе
        self._row_window_start: int = 0
        self._queued_messages: list[str] = []

        self.overlay: Overlay | None = None
        self._overlay_stack: list[Overlay] = []

        self.mode: str = "agent"
        self.on_mode_toggle: Callable[[str], None] | None = None
        self.status_provider: Callable[[], str] | None = None
        self.on_ctrl_o: Callable[[], None] | None = None
        self.on_edit_queued: Callable[[], str | None] | None = None
        self.submission_text_transform: Callable[[str], str] | None = None
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
        try:
            _prune_history_file(history_file)
        except OSError:
            logger.debug("history prune failed", exc_info=True)
        self.input_buffer = Buffer(
            name="input",
            completer=self.completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            history=ThreadedHistory(FileHistory(history_file)),
            multiline=True,  # перенос по "\" + Enter; Enter отправляет сам
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

    # ──────────────────────────── оверлеи ──────────────────────────────────

    # ──────────────────────────── клавиши ──────────────────────────────────

    def _submit_text(self, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        self._notice_text = ""
        kind = SUBMIT_SLASH if cleaned.startswith("/") else SUBMIT_USER
        submitted = cleaned
        if self.submission_text_transform is not None:
            try:
                submitted = self.submission_text_transform(cleaned)
            except Exception:
                logger.debug("submission text transform failed", exc_info=True)
        self.submissions.put_nowait((kind, submitted))
        if kind == SUBMIT_USER:
            try:
                latest = next(iter(self.input_buffer.history.load_history_strings()), None)
                if latest != submitted:
                    self.input_buffer.history.append_string(submitted)
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
        """Оставляет в истории ввода записи за последние семь суток."""
        try:
            if not _prune_history_file(self._history_file):
                return
            history = self.input_buffer.history
            entries = list(history.load_history_strings())
            lock = getattr(history, "_lock", None)
            if lock is None:
                history._loaded_strings[:] = entries
            else:
                with lock:
                    history._loaded_strings[:] = entries
        except Exception:
            logger.debug("history prune failed", exc_info=True)

    # ──────────────────────────── жизненный цикл ───────────────────────────
    def invalidate(self) -> None:
        if self.app is not None:
            with contextlib.suppress(Exception):
                self.app.invalidate()

    def _style(self) -> Style:
        from config.themes import t

        return Style.from_dict(
            {
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
            }
        )

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
        from ui.focus import (
            disable_focus_reporting,
            enable_focus_reporting,
            wrap_input_for_focus_tracking,
        )

        base_input = None
        try:
            from prompt_toolkit.input import create_input

            base_input = wrap_input_for_focus_tracking(create_input())
        except Exception:
            logger.debug("focus tracking input unavailable", exc_info=True)
        self.app = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self._style(),
            color_depth=self.color_depth,
            full_screen=False,  # никакого alt-screen — иначе умрёт скроллбар
            mouse_support=False,  # иначе умрёт выделение мышью
            erase_when_done=False,
            refresh_interval=0,
            input=base_input,
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
        app_task = asyncio.create_task(self.app.run_async(), name="shell-app")
        if base_input is not None:
            enable_focus_reporting()
            app_task.add_done_callback(lambda _: disable_focus_reporting())
        return app_task

    def print_exit_notice(self, text: str) -> None:
        """Стереть рамку ввода и напечатать финальное сообщение на её месте.

        После `app.exit()` кадр prompt_toolkit остаётся на экране
        (`erase_when_done=False`). Перед финальным сообщением его надо снять:
        поднимаемся на высоту кадра и очищаем всё до конца экрана.
        """
        try:
            rows = self._input_height() + 2 * self._frame_height() + self._below_height() + 1
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


# ─────────────────────────── строки под рамкой ──────────────────────────────


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


def static_tail_blank() -> bool | None:
    """Кончается ли scrollback пустой строкой; None — Shell ещё не создан."""
    sh = Shell.instance()
    return sh.static_tail_blank() if sh is not None else None


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
