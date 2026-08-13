"""Overlay lifecycle and prompt-toolkit layout responsibilities of ``Shell``."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app_or_none
from prompt_toolkit.filters import Condition, has_completions, is_done
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.containers import ConditionalContainer, ScrollOffsets
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.margins import ScrollbarMargin
from prompt_toolkit.layout.menus import CompletionsMenuControl
from prompt_toolkit.layout.processors import AppendAutoSuggestion

from .overlay import Overlay
from .rendering import ansi_rows
from .terminal import term_size
from .text_layout import WordWrapProcessor, _word_wrapped_rows, clip_visible, visible_width

logger = logging.getLogger(__name__)


class ShellLayoutMixin:
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
        has_bar = all(
            m in text for m in (BAR_FILLED_START, BAR_FILLED_END, BAR_EMPTY_START, BAR_EMPTY_END)
        )
        if not has_bar:
            tail = max(0, w - len(head) - visible_width(text) - 1)
            return [
                ("class:frame", head),
                ("class:status", text),
                ("class:frame", " " + "─" * tail),
            ]

        rest = text
        before, rest = rest.split(BAR_FILLED_START, 1)
        filled, rest = rest.split(BAR_FILLED_END, 1)
        _skip, rest = rest.split(BAR_EMPTY_START, 1)
        empty, after = rest.split(BAR_EMPTY_END, 1)
        used = (
            len(head)
            + visible_width(before)
            + visible_width(filled)
            + visible_width(empty)
            + visible_width(after)
            + 1
        )
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
            ansi_rows(self._dynamic_ansi())  # динамическая зона
            + self._top_gap_rows()  # ответ/thinking или отступ оверлея
            + 2 * self._frame_height()  # рамка только у обычного ввода
            + self._below_height()  # подсказка оверлея
            + 1  # обязательная пустая строка
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
        return self._confirm_exit_until is not None and time.monotonic() < self._confirm_exit_until

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
        key = (id(overlay), version, w, budget, self._frame_id() if version is None else None)
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
            out.append(
                (
                    "class:row.sel" if focused else "class:row",
                    ("❯ " if focused else "  ") + group.label() + "\n",
                )
            )
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
            ansi_rows(self._dynamic_ansi())  # динамическая зона
            + self._top_gap_rows()
            + 1  # верхняя линия рамки
            + self._input_height()
            + self._overlay_height()
            + 1  # нижняя линия рамки
            + self._below_height()
            + 1  # обязательная пустая строка
        )
        # Верхняя линия поля не должна уезжать выше середины. Из нижней
        # половины вычитаем само поле, нижнюю линию, строки и отбивку.
        lift_cap = (rows + 1) // 2 - self._input_height() - 1 - self._below_height() - 1
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
        root = HSplit(
            [
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
                VSplit(
                    [
                        Window(
                            FormattedTextControl(self._prompt_fragments),
                            width=self._prompt_width,
                            height=self._input_height,
                        ),
                        self.input_window,
                    ],
                    height=self._input_height,
                ),
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
                Window(
                    FormattedTextControl(self._below_fragments),
                    height=self._below_height,
                    wrap_lines=False,
                ),
                # обязательная пустая строка снизу
                Window(height=1),
            ]
        )
        self.layout = Layout(root)
        self.layout.focus(self.input_window)
