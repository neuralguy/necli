"""Output, dynamic-frame, queue, and status responsibilities of ``Shell``."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable
from typing import Any

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.application.current import get_app_or_none
from rich.console import Group
from rich.text import Text

from config.constants import Limits

from .rendering import _raw_ends_blank, _renderable_ends_blank
from .rows import RowGroup
from .text_layout import clip_visible

logger = logging.getLogger(__name__)
_STATIC_QUEUE_MAX = Limits.STATIC_QUEUE_MAX
ROW_WINDOW_SIZE = 4


class ShellOutputMixin:
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
        return self.overlay is None and (
            any(callable(v) for v in self._dynamic.values())
            or any(group.animated for group in self._visible_rows())
        )

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
        # План всегда последним — непосредственно над полем ввода. Working
        # и стримящиеся элементы остаются выше него.
        ordered = [k for k in self._dynamic_order if k not in ("working", "plan")]
        if "working" in self._dynamic:
            ordered.append("working")
        if "plan" in self._dynamic:
            ordered.append("plan")
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
            if (dynamic_key == "working" and chunks) or (
                dynamic_key == "plan" and "working" in self._dynamic and chunks
            ):
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
        if len(self._static_queue) >= _STATIC_QUEUE_MAX:
            # Агент генерирует вывод быстрее, чем run_in_terminal успевает
            # сбрасывать кадр: дропаем старые записи, оставляя последние.
            while len(self._static_queue) >= _STATIC_QUEUE_MAX:
                self._static_queue.popleft()
            logger.warning("static queue overflow, dropping old entries")
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

    def print_block(self, renderable: Any) -> None:
        """Верхнеуровневый блок вывода: перед ним ровно одна пустая строка.

        Вертикальный ритм строится на фактическом хвосте scrollback: если
        предыдущий вывод уже кончился пустой строкой, вторая не добавляется,
        если кончился контентом — добавляется ровно одна.
        """
        if not self._static_tail_blank:
            renderable = Group(Text(""), renderable)
        self.print_static(renderable)

    def print_block_raw(self, text: str) -> None:
        """Как print_block, но для готовой ANSI-строки (эхо ввода и т.п.)."""
        if text and not self._static_tail_blank:
            text = "\n" + text
        self.print_static_raw(text)

    def static_tail_blank(self) -> bool:
        """Кончается ли scrollback пустой строкой (для агентской print_block)."""
        return self._static_tail_blank

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
        visible = self._queued_messages[-(cap - 1) :]
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
            out.append(
                (
                    "class:queue.summary" if summary else "class:queue.text",
                    clip_visible(text, width),
                )
            )
        return out

    def _queued_height(self) -> int:
        return len(self._visible_queued_messages())

    def _queue_edit_hint(self) -> str:
        if (
            self.overlay is not None
            or self.input_buffer.text
            or not self._queued_messages
        ):
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
        return list(enumerate(rows[start : start + ROW_WINDOW_SIZE], start=start))

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
        other = sum(
            group.summary_count
            for group in hidden
            if group.kind not in ("agent", "task")
        )
        from config.i18n import t as tr

        if agents and tasks:
            return tr("rows.more_both", agents=agents, tasks=tasks)
        if agents:
            return tr("rows.more_agents", n=agents)
        if tasks:
            return tr("rows.more_tasks", n=tasks)
        return tr("rows.more_items", n=other or len(hidden))
