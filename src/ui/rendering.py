"""Conversion and layout helpers for terminal renderables."""

from __future__ import annotations

import io
import logging
from typing import Any

from rich.console import Console

logger = logging.getLogger(__name__)


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
            file=buf,
            width=max(8, width),
            force_terminal=True,
            color_system=self.color_system,
            soft_wrap=False,
            legacy_windows=False,
            highlight=False,
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
    plain = getattr(renderable, "plain", None)  # rich.text.Text
    if isinstance(plain, str):
        return not plain.strip()
    parts = getattr(renderable, "renderables", None)  # rich.console.Group
    if parts:
        return _renderable_ends_blank(parts[-1])
    return False


def _raw_ends_blank(text: str) -> bool:
    """То же для готовой строки с ANSI (её пишут байт в байт, без Rich)."""
    if not text.endswith("\n"):
        return False
    return not text[:-1].rsplit("\n", 1)[-1].strip()
