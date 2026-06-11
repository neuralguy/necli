"""Кросс-платформенное чтение одной клавиши из терминала.

POSIX: termios/tty raw-mode + os.read. Windows: msvcrt.getwch.
Возвращает нормализованные имена: up/down/left/right/enter/escape/ctrl-c
или сам символ.
"""

import sys

_IS_WIN = sys.platform == "win32"

if not _IS_WIN:
    import os
    import select
    import termios
    import tty


def _normalize(ch: str) -> str:
    # ВНИМАНИЕ: q/Q → выход и j/k → навигация (vim-style) — это поведение
    # рассчитано на одноклавишные МЕНЮ (poll/выбор пункта), где обычный текст
    # не вводится. Не переиспользовать read_key() для свободного текстового
    # ввода — там эти буквы будут перехвачены.
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03" or ch == "\x04":
        return "ctrl-c"
    if ch == "\x10":
        return "ctrl-p"
    if ch in ("q", "Q"):
        return "ctrl-c"
    if ch == "j":
        return "down"
    if ch == "k":
        return "up"
    return ch

def _normalize_text(ch: str) -> str:
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "\x03" or ch == "\x04":
        return "ctrl-c"
    if ch == "\x10":
        return "ctrl-p"
    if ch == "\x7f" or ch == "\b":
        return "backspace"
    return ch


if _IS_WIN:
    import msvcrt

    def _read_key(text_input: bool = False) -> str:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):  # спец-клавиша: второй байт = код
            code = msvcrt.getwch()
            return {"H": "up", "P": "down", "M": "right", "K": "left"}.get(code, "")
        if ch == "\x1b":
            return "escape"
        return _normalize_text(ch) if text_input else _normalize(ch)

    def read_key() -> str:
        return _read_key(False)

    def drain_keys() -> str:
        last = read_key()
        while msvcrt.kbhit():
            key = read_key()
            if key in ("enter", "ctrl-c", "escape"):
                return key
            last = key
        return last

    def drain_text_keys() -> str:
        last = _read_key(True)
        while msvcrt.kbhit():
            key = _read_key(True)
            if key in ("enter", "ctrl-c", "escape"):
                return key
            last = key
        return last

    class raw_mode:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

else:
    def _read_utf8_char(fd: int) -> str:
        first = os.read(fd, 1)
        if not first:
            return ""
        b0 = first[0]
        if b0 < 0x80:
            return first.decode("utf-8", errors="replace")
        if 0xC0 <= b0 <= 0xDF:
            needed = 1
        elif 0xE0 <= b0 <= 0xEF:
            needed = 2
        elif 0xF0 <= b0 <= 0xF7:
            needed = 3
        else:
            needed = 0
        return (first + (os.read(fd, needed) if needed else b"")).decode("utf-8", errors="replace")

    def _read_key_raw(fd: int, text_input: bool = False) -> str:
        ch = _read_utf8_char(fd)
        if ch == "\x1b":
            # Read the rest of a possible CSI escape sequence in one shot.
            # A real arrow key (\x1b[A) arrives as a burst; a lone ESC does not.
            if not select.select([fd], [], [], 0.02)[0]:
                return "escape"
            rest = os.read(fd, 8).decode("utf-8", errors="replace")
            if rest.startswith("[") and len(rest) >= 2:
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(rest[1], rest[1])
            return "escape"
        return _normalize_text(ch) if text_input else _normalize(ch)

    def read_key() -> str:
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            return _read_key_raw(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    def drain_keys() -> str:
        fd = sys.stdin.fileno()
        last = _read_key_raw(fd)
        while select.select([fd], [], [], 0)[0]:
            key = _read_key_raw(fd)
            if key in ("enter", "ctrl-c", "escape"):
                return key
            last = key
        return last

    def drain_text_keys() -> str:
        fd = sys.stdin.fileno()
        last = _read_key_raw(fd, text_input=True)
        while select.select([fd], [], [], 0)[0]:
            key = _read_key_raw(fd, text_input=True)
            if key in ("enter", "ctrl-c", "escape"):
                return key
            last = key
        return last

    class raw_mode:
        def __enter__(self):
            self._fd = sys.stdin.fileno()
            self._old = termios.tcgetattr(self._fd)
            tty.setraw(self._fd)
            return self

        def __exit__(self, *exc):
            termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old)
            return False