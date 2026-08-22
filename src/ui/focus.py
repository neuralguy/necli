"""Фокус терминального окна через focus reporting (CSI ?1004).

Терминал, получив `\x1b[?1004h`, шлёт `\x1b[I` при входе окна в фокус и
`\x1b[O` — при потере. Эту версию prompt_toolkit эти последовательности не
знает и разбивает их на мусорные клавиши (Esc, '[', 'I'), поэтому
`wrap_input_for_focus_tracking` вырезает их из потока ДО парсера и
превращает в состояние фокуса.

Режим включаем ТОЛЬКО пока терминал в raw-режиме: в cooked-режиме
ответ терминала (`\x1b[I`) уходит в эхо tty-драйвера и печатается в строку
ввода как `^[[I`. Поэтому включение привязано к `Input.raw_mode()`
(`bind_focus_reporting_to_raw_mode`), а на время `cooked_mode()`
(run_in_terminal) отчётность приостанавливается.

Не все терминалы поддерживают режим — тогда состояние остаётся None
(«неизвестно»), и вызывающие трактуют его как «не в фокусе».
"""

from __future__ import annotations

import contextlib
import logging
import sys

logger = logging.getLogger(__name__)

_FOCUS_IN = "\x1b[I"
_FOCUS_OUT = "\x1b[O"

ENABLE_SEQ = "\x1b[?1004h"
DISABLE_SEQ = "\x1b[?1004l"

_focused: bool | None = None


def is_terminal_focused() -> bool | None:
    """True — окно в фокусе, False — нет, None — терминал не отчитывается."""
    return _focused


def mark_focused(focused: bool) -> None:
    global _focused
    _focused = focused


def reset_focus_state() -> None:
    global _focused
    _focused = None


def strip_focus_sequences(data: str, on_event=mark_focused) -> str:
    """Убрать \\x1b[I / \\x1b[O из чанка ввода, обновив состояние фокуса.

    Терминал пишет focus-событие одной атомарной записью, поэтому считаем
    последовательность целой внутри одного чанка: одиночный Esc от
    пользователя должен дойти до парсера без задержки, а не ждать
    предполагаемого продолжения.
    """
    if _FOCUS_IN not in data and _FOCUS_OUT not in data:
        return data
    pieces: list[str] = []
    i = 0
    while True:
        a = data.find(_FOCUS_IN, i)
        b = data.find(_FOCUS_OUT, i)
        if a == -1 and b == -1:
            pieces.append(data[i:])
            break
        if a != -1 and (b == -1 or a < b):
            pieces.append(data[i:a])
            on_event(True)
            i = a + len(_FOCUS_IN)
        else:
            pieces.append(data[i:b])
            on_event(False)
            i = b + len(_FOCUS_OUT)
    return "".join(pieces)


def _write_seq(seq: str) -> None:
    out = getattr(sys, "__stdout__", None) or sys.stdout
    if out is None:
        return
    isatty = getattr(out, "isatty", None)
    if not callable(isatty) or not isatty():
        return
    try:
        out.write(seq)
        out.flush()
    except OSError:
        return


def enable_focus_reporting() -> None:
    _write_seq(ENABLE_SEQ)


def disable_focus_reporting() -> None:
    _write_seq(DISABLE_SEQ)
    reset_focus_state()


def bind_focus_reporting_to_raw_mode(base_input) -> bool:
    """Привязать focus reporting к raw-режиму конкретного Input.

    Возвращает True, если удалось обернуть `raw_mode`/`cooked_mode`. Пока
    активен raw-режим, режим включён; на время вложенного cooked-окна
    (`run_in_terminal` печатает статику) он выключается, чтобы ответ
    терминала не попал в эхо, а состояние фокуса при этом сохраняется.
    """
    raw_mode = getattr(base_input, "raw_mode", None)
    cooked_mode = getattr(base_input, "cooked_mode", None)
    if not callable(raw_mode) or not callable(cooked_mode):
        return False

    state = {"raw": 0}

    @contextlib.contextmanager
    def raw_mode_wrapper(*args, **kwargs):
        with raw_mode(*args, **kwargs):
            state["raw"] += 1
            enable_focus_reporting()
            try:
                yield
            finally:
                state["raw"] -= 1
                disable_focus_reporting()

    @contextlib.contextmanager
    def cooked_mode_wrapper(*args, **kwargs):
        paused = state["raw"] > 0
        if paused:
            _write_seq(DISABLE_SEQ)
        try:
            with cooked_mode(*args, **kwargs):
                yield
        finally:
            if paused:
                _write_seq(ENABLE_SEQ)

    base_input.raw_mode = raw_mode_wrapper
    base_input.cooked_mode = cooked_mode_wrapper
    return True


def wrap_input_for_focus_tracking(base_input):
    """Обёртка над Vt100Input: перехватывает feed() парсера.

    Возвращает тот же объект (патчим feed конкретного экземпляра) либо None,
    если ввод не vt100 (Windows-консоль, пайп) — там focus reporting нет.
    """
    try:
        from prompt_toolkit.input.vt100 import Vt100Input
    except ImportError:
        return None
    if not isinstance(base_input, Vt100Input):
        return None
    parser = base_input.vt100_parser
    inner_feed = parser.feed

    def feed(data: str) -> None:
        inner_feed(strip_focus_sequences(data))

    parser.feed = feed
    return base_input
