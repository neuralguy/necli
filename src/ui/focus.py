"""Фокус терминального окна через focus reporting (CSI ?1004).

Терминал, получив `\x1b[?1004h`, шлёт `\x1b[I` при входе окна в фокус и
`\x1b[O` — при потере. Эту версию prompt_toolkit эти последовательности не
знает и разбивает их на мусорные клавиши (Esc, '[', 'I'), поэтому
`wrap_input_for_focus_tracking` вырезает их из потока ДО парсера и
превращает в состояние фокуса.

Не все терминалы поддерживают режим — тогда состояние остаётся None
(«неизвестно»), и вызывающие трактуют его как «не в фокусе».
"""

from __future__ import annotations

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
