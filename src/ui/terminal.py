"""Terminal capability detection shared by the interactive UI."""

from __future__ import annotations

import os

from prompt_toolkit.output import ColorDepth


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
            "1": ColorDepth.DEPTH_1_BIT,
            "mono": ColorDepth.DEPTH_1_BIT,
            "4": ColorDepth.DEPTH_4_BIT,
            "16": ColorDepth.DEPTH_4_BIT,
            "8": ColorDepth.DEPTH_8_BIT,
            "256": ColorDepth.DEPTH_8_BIT,
            "24": ColorDepth.DEPTH_24_BIT,
            "truecolor": ColorDepth.DEPTH_24_BIT,
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
