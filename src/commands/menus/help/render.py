"""Рендеринг блоков и двухколоночной раскладки справки."""

from __future__ import annotations

import textwrap

from commands.menus.help.models import HBlock
from ui.overlays import cell_width, clip, paint


def _wrap_text(text: str, width: int) -> list[str]:
    """Перенос plain-текста по видимой ширине."""
    if not text:
        return [""]
    return textwrap.wrap(text, width=max(8, width)) or [""]


def render_block(block: HBlock, width: int) -> list[str]:
    """Рендерит один блок в список ANSI-строк."""
    w = max(8, width)
    kind = block.kind

    if kind == "h":
        # Заголовок: жирный, цвет по роли
        role = block.role or "accent"
        return [paint(block.content, role, bold=True)]

    if kind == "text":
        lines = _wrap_text(block.content, w)
        return lines

    if kind == "code":
        lines = block.content.split("\n")
        return ["  " + paint(ln, "info") for ln in lines]

    if kind == "tip":
        lines = _wrap_text(block.content, w - 3)
        return ["💡 " + paint(lines[0], "success")] + \
               ["   " + paint(ln, "success") for ln in lines[1:]]

    if kind == "warn":
        lines = _wrap_text(block.content, w - 3)
        return ["⚠ " + paint(lines[0], "warning")] + \
               ["  " + paint(ln, "warning") for ln in lines[1:]]

    if kind == "table":
        return _render_table(block.items, w)

    if kind == "keys":
        return _render_keys(block.items, w)

    if kind == "list":
        out = []
        for item in block.items:
            lines = _wrap_text(item, w - 4)
            out.append("  • " + lines[0])
            out.extend("    " + ln for ln in lines[1:])
        return out

    if kind == "sep":
        return [""]

    if kind == "demo":
        lines = block.content.split("\n")
        out = []
        for ln in lines:
            if ln.startswith(("●", "🔧", "⏺")):
                out.append(paint(ln, "success"))
            elif ln.startswith("❯"):
                out.append(paint(ln, "accent", bold=True))
            elif ln.strip().startswith("⎿"):
                out.append(paint(ln, "dim_text"))
            elif ln.startswith("💭"):
                out.append(paint(ln, "purple"))
            elif ln.startswith("🤖"):
                out.append(paint(ln, "magenta"))
            elif ln.startswith("⚠"):
                out.append(paint(ln, "warning"))
            else:
                out.append(ln)
        return out

    return []


def _render_table(rows: list[tuple], width: int) -> list[str]:
    """Простая текстовая таблица без рамок."""
    if not rows:
        return []
    # Определяем ширины колонок
    ncols = max(len(r) for r in rows) if rows else 0
    col_widths = [0] * ncols
    for r in rows:
        for i, cell in enumerate(r):
            col_widths[i] = max(col_widths[i], cell_width(str(cell)))
    # Ограничиваем общую ширину
    total = sum(col_widths) + 3 * (ncols - 1)
    if total > width:
        scale = width / max(1, total)
        col_widths = [max(4, int(w * scale)) for w in col_widths]
    out = []
    for r in rows:
        parts = []
        for i, cell in enumerate(r):
            cw = col_widths[i] if i < len(col_widths) else 10
            cell_txt = clip(str(cell), cw)
            # Последнюю колонку не паддим — иначе хвостовые пробелы до конца
            # ширины колонки (визуальный мусор в правой части строки).
            if i < ncols - 1:
                cell_txt = cell_txt.ljust(cw)
            parts.append(cell_txt)
        out.append("  " + "   ".join(parts))
    return out


def _render_keys(rows: list[tuple], width: int) -> list[str]:
    """Клавиши: название + действие."""
    if not rows:
        return []
    key_w = max(cell_width(str(k)) for k, _ in rows)
    key_w = min(key_w, 16)
    out = []
    for key, desc in rows:
        k = clip(str(key), key_w).ljust(key_w)
        d = clip(str(desc), width - key_w - 5)
        out.append("  " + paint(k, "purple", bold=True) + "  " + d)
    return out


def render_column(blocks: list[HBlock], width: int) -> list[str]:
    """Рендерит колонку блоков в список строк."""
    lines: list[str] = []
    for block in blocks:
        rendered = render_block(block, width)
        lines.extend(rendered)
    return lines


def render_two_columns(
    left_blocks: list[HBlock],
    right_blocks: list[HBlock],
    total_width: int,
    max_height: int,
    paired: bool = False,
) -> str:
    """Двухколоночная раскладка: информация слева, примеры справа.

    paired=False — колонки текут независимо и склеиваются по строкам.
    paired=True — блоки парятся по индексу: блок i слева выравнивается
    с блоком i справа, высота пары = максимум из двух. Так примеры
    остаются на одной строке со своими командами.
    """
    gap = 3
    left_width = int(total_width * 0.52)
    right_width = total_width - left_width - gap

    if paired:
        lines: list[str] = []
        n = max(len(left_blocks), len(right_blocks))
        for i in range(n):
            lb = left_blocks[i] if i < len(left_blocks) else None
            rb = right_blocks[i] if i < len(right_blocks) else None
            ll = render_block(lb, left_width) if lb else [""]
            rl = render_block(rb, right_width) if rb else [""]
            h = max(len(ll), len(rl))
            ll += [""] * (h - len(ll))
            rl += [""] * (h - len(rl))
            for j in range(h):
                left = ll[j]
                right = rl[j]
                left_padded = left + " " * max(0, left_width - cell_width(left))
                lines.append("  " + left_padded + " " * gap + right)
                if len(lines) >= max_height:
                    return "\n".join(lines)
        return "\n".join(lines)

    left_lines = render_column(left_blocks, left_width)
    right_lines = render_column(right_blocks, right_width)

    # Выравниваем длину
    max_len = max(len(left_lines), len(right_lines), 1)
    left_lines.extend([""] * (max_len - len(left_lines)))
    right_lines.extend([""] * (max_len - len(right_lines)))

    # Обрезаем по высоте
    if max_len > max_height:
        left_lines = left_lines[:max_height]
        right_lines = right_lines[:max_height]
        max_len = max_height

    # Собираем
    result = []
    for i in range(max_len):
        left = left_lines[i] if i < len(left_lines) else ""
        right = right_lines[i] if i < len(right_lines) else ""
        left_padded = left + " " * max(0, left_width - cell_width(left))
        result.append("  " + left_padded + " " * gap + right)

    return "\n".join(result)
