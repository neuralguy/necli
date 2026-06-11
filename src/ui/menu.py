"""Интерактивное меню со стрелками в стиле Rovo Dev."""

import shutil
import sys
from io import StringIO
import unicodedata

from ui._keyreader import drain_keys as _drain_keys, drain_text_keys as _drain_text_keys, raw_mode
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from session._time import format_relative
from config.themes import t

def _format_context_limit(limit: int) -> str:
    if limit >= 1_000_000:
        return f"{limit // 1_000_000}M"
    if limit >= 1000:
        return f"{limit // 1000}K"
    return str(limit)


def _cell_width(value: str) -> int:
    width = 0
    for ch in value:
        if unicodedata.combining(ch):
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
    return width

def _safe_menu_text(value: str, max_width: int) -> str:
    cleaned = []
    for ch in str(value):
        category = unicodedata.category(ch)
        if category[0] == "C" or category == "So":
            continue
        if ord(ch) >= 0x1F000 or ch in ("\ufe0e", "\ufe0f"):
            continue
        cleaned.append(ch)
    text = " ".join("".join(cleaned).split())
    if _cell_width(text) <= max_width:
        return text
    out = []
    width = 0
    limit = max(0, max_width - 3)
    for ch in text:
        ch_width = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
        if width + ch_width > limit:
            break
        out.append(ch)
        width += ch_width
    return "".join(out).rstrip() + "..."

def _pad_menu_text(value: str, width: int, justify: str = "left") -> str:
    value_width = _cell_width(value)
    pad = max(0, width - value_width)
    if justify == "right":
        return " " * pad + value
    return value + " " * pad

def clear_lines(n: int):
    """Очищает n строк вверх."""
    sys.stdout.write('\r\x1b[2K')
    for _ in range(n - 1):
        sys.stdout.write('\x1b[A')
        sys.stdout.write('\x1b[2K')
    sys.stdout.flush()


def _move_up_and_overwrite(stream, new_content: str, prev_lines: int) -> int:
    """
    Перемещает курсор вверх на prev_lines, перезаписывает содержимое построчно.
    Каждая строка очищается до конца — устраняет артефакты и мигание.
    Возвращает количество строк в новом контенте.
    """
    stream.write('\x1b[?25l')  # скрыть курсор
    stream.write('\r')  # начало текущей строки
    # Подняться на prev_lines - 1 строк
    for _ in range(prev_lines - 1):
        stream.write('\x1b[A')

    # Разбиваем на строки и пишем каждую с очисткой остатка
    lines = new_content.split('\n')
    # Визуальные строки = количество элементов split
    # (последний элемент может быть непустым, например hint_line)
    new_lines = len(lines) if lines[-1] else len(lines) - 1

    for i, line in enumerate(lines):
        stream.write('\r')  # начало строки
        stream.write(line)
        stream.write('\x1b[K')  # очистить от курсора до конца строки
        if i < len(lines) - 1:
            stream.write('\x1b[B')

    # Если старый контент был длиннее — очистить лишние строки
    extra = prev_lines - new_lines
    for _ in range(extra):
        stream.write('\x1b[B\r\x1b[2K')
    # Вернуться назад на extra строк
    for _ in range(extra):
        stream.write('\x1b[A')

    stream.write('\x1b[?25h')  # показать курсор
    stream.flush()
    return new_lines


def _clear_stream_lines(stream, n: int):
    """Очищает n строк вверх в указанном потоке."""
    stream.write('\r\x1b[2K')
    for _ in range(n - 1):
        stream.write('\x1b[A')
        stream.write('\x1b[2K')
    stream.flush()


def select_menu(
    items: list[dict],
    current: int = 0,
    title: str = "",
) -> Optional[int]:
    """
    Показывает интерактивное меню со стрелками.

    items: список dict с ключами:
        - "label": str — основной текст
        - "hint": str — серый текст справа (опционально)
        - "active": bool — текущий выбранный (опционально)
    current: начальный индекс курсора
    title: заголовок меню

    Возвращает индекс выбранного элемента или None если отменено.
    """
    if not items:
        return None

    selected = current
    total = len(items)

    RESET = '\x1b[0m'
    DIM = '\x1b[2m'

    def _hex_to_ansi_fg(h: str) -> str:
        h = h.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'\x1b[38;2;{r};{g};{b}m'

    def _hex_to_ansi_bg(h: str) -> str:
        h = h.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'\x1b[48;2;{r};{g};{b}m'

    BOLD_BLUE = '\x1b[1m' + _hex_to_ansi_fg(t('accent'))
    GREEN = _hex_to_ansi_fg(t('success'))
    BOLD = '\x1b[1m'
    BG_SELECT = _hex_to_ansi_bg(t('bg_select'))

    def _render():
        lines = []
        for i, item in enumerate(items):
            label = item["label"]
            hint = item.get("hint", "")
            is_active = item.get("active", False)

            if i == selected:
                marker = f"{BOLD_BLUE}❯{RESET}"
                text = f"{BG_SELECT}{BOLD} {label}{RESET}"
                if hint:
                    text += f"  {BG_SELECT}{DIM}{hint}{RESET}"
                if is_active:
                    text += f"  {GREEN}◄{RESET}"
                lines.append(f"  {marker}{text}")
            else:
                marker = " "
                text = f"  {label}"
                if hint:
                    text += f"  {DIM}{hint}{RESET}"
                if is_active:
                    text += f"  {GREEN}◄{RESET}"
                lines.append(f"  {marker}{text}")
        return lines

    hint_line = f"  {DIM}↑↓ select · enter confirm · esc cancel{RESET}"

    def _build_content():
        parts = []
        if title:
            if '\x1b' in title:
                parts.append(f"  {title}")
            else:
                parts.append(f"  {DIM}{title}{RESET}")
        for line in _render():
            parts.append(line)
        parts.append(hint_line)
        return '\n'.join(parts)

    # Первая отрисовка
    content = _build_content()
    sys.stdout.write(content)
    sys.stdout.flush()
    rendered_count = content.count('\n') + 1

    try:
        with raw_mode():
            while True:
                key = _drain_keys()
                if key == 'up':
                    selected = (selected - 1) % total
                elif key == 'down':
                    selected = (selected + 1) % total
                elif key == 'enter':
                    clear_lines(rendered_count)
                    return selected
                elif key == 'ctrl-c' or key == 'escape':
                    clear_lines(rendered_count)
                    return None
                else:
                    if key.isdigit():
                        num = int(key)
                        if 1 <= num <= total:
                            clear_lines(rendered_count)
                            return num - 1
                    continue

                content = _build_content()
                rendered_count = _move_up_and_overwrite(sys.stdout, content, rendered_count)
    except Exception:
        clear_lines(rendered_count)
        return None



def select_session_menu(
    sessions: list[dict],
    current_id: str = "",
) -> Optional[int]:
    """
    Интерактивное меню сессий в стиле панели с таблицей.
    Pinned sessions всегда сверху. P — toggle pin для выделенной сессии.
    Возвращает индекс ВЫБРАННОЙ сессии в ИСХОДНОМ списке sessions, или None.
    """
    if not sessions:
        return None

    from config.pinned import get_pinned, toggle as toggle_pin

    # Работаем по индексам исходного списка; внутри сортируем по pin.
    # original_indices_sorted — массив исходных индексов в порядке отображения.
    query = ""

    def _sort_indices() -> list[int]:
        pinned_ids = get_pinned()
        pinned_idx = [i for i, s in enumerate(sessions) if s.get("id") in pinned_ids]
        rest_idx = [i for i, s in enumerate(sessions) if s.get("id") not in pinned_ids]
        return pinned_idx + rest_idx

    def _matches_query(orig_idx: int) -> bool:
        if not query:
            return True
        s = sessions[orig_idx]
        haystack = " ".join(
            str(s.get(k, ""))
            for k in ("title", "id", "site", "last_model")
        ).casefold()
        return query.casefold() in haystack

    def _filtered_order() -> list[int]:
        return [i for i in _sort_indices() if _matches_query(i)]

    order = _filtered_order()

    # Курсор начинается на current_id
    initial_selected = 0
    for pos, orig_idx in enumerate(order):
        if sessions[orig_idx].get("id") == current_id:
            initial_selected = pos
            break

    total = len(order)
    out_console = Console(file=sys.stderr, highlight=False)
    render_width = max(20, out_console.width - 1)

    term_h = shutil.get_terminal_size((80, 24)).lines
    max_visible = max(3, (term_h - 8) // 3)
    need_scroll = total > max_visible

    def _format_tokens_short(n: int) -> str:
        if n < 1000:
            return str(n)
        if n < 1_000_000:
            return f"{n // 1000}K"
        return f"{n / 1_000_000:.1f}M"

    def _viewport(sel: int, scroll_off: int) -> tuple[int, int, int]:
        """Вычисляет viewport (start, end) и новый scroll_offset."""
        need_scroll_now = total > max_visible
        if not need_scroll_now:
            return 0, total, 0
        if sel < scroll_off:
            scroll_off = sel
        elif sel >= scroll_off + max_visible:
            scroll_off = sel - max_visible + 1
        scroll_off = max(0, min(scroll_off, total - max_visible))
        return scroll_off, scroll_off + max_visible, scroll_off

    scroll_offset = 0
    if need_scroll:
        if initial_selected >= max_visible:
            scroll_offset = min(initial_selected, total - max_visible)

    def render_fn(sel: int) -> str:
        nonlocal scroll_offset
        vp_start, vp_end, scroll_offset = _viewport(sel, scroll_offset)
        pinned_ids = get_pinned()
        inner_width = max(20, render_width - 4)
        title_width = max(20, inner_width - 4)
        body = Text()
        shown = sel + 1 if total else 0
        body.append(f"Session management ({shown} of {total})\n\n", style="bold #9bbcff")
        search_text = query if query else "Search..."
        body.append("  / " + _safe_menu_text(search_text, inner_width - 4) + "\n", style="" if query else "dim")
        body.append("─" * inner_width, style="dim")

        if total == 0:
            body.append("\n  No sessions found", style="dim")

        for pos in range(vp_start, vp_end):
            orig_idx = order[pos]
            s = sessions[orig_idx]
            sid = s.get("id", "")
            is_pinned = sid in pinned_ids
            title = _safe_menu_text(s.get("title", "") or "Untitled Session", title_width)
            msgs = s.get("messages", 0)
            updated_at = s.get("updated_at", 0)
            tokens = s.get("tokens", 0)
            activity = format_relative(updated_at) if updated_at else "—"
            is_current = sid == current_id
            is_selected = pos == sel
            marker = "› " if is_selected else "  "
            pin = "↓ " if is_pinned else ""

            row_bg = Style(bgcolor=t("bg_select")) if is_selected else Style.null()
            if is_current and is_selected:
                title_style = row_bg + Style.parse("bold green")
            elif is_current:
                title_style = row_bg + Style.parse("green")
            elif is_selected:
                title_style = row_bg + Style.parse("bold #9bbcff")
            else:
                title_style = row_bg + Style.parse("green")

            meta = f"{activity} · {msgs} msgs · {_format_tokens_short(tokens)} · {sid[:12]}"
            body.append("\n")
            body.append(_pad_menu_text(marker + pin + title, inner_width), style=title_style)
            body.append("\n")
            body.append(_pad_menu_text("  " + _safe_menu_text(meta, inner_width - 2), inner_width), style=row_bg + Style(dim=True))

        buf = StringIO()
        render_console = Console(file=buf, highlight=False, force_terminal=True, width=render_width)
        render_console.print(body)
        return buf.getvalue()

    def on_key(key: str, sel: int):
        nonlocal order, query, scroll_offset, total
        if key == "backspace":
            if query:
                query = query[:-1]
            order = _filtered_order()
            total = len(order)
            scroll_offset = 0
            return (True, min(sel, max(0, total - 1)), total)
        if key == "escape":
            if query:
                query = ""
                order = _filtered_order()
                total = len(order)
                scroll_offset = 0
                return (True, min(sel, max(0, total - 1)), total)
            return (False, sel, total)
        if len(key) == 1 and key.isprintable():
            query += key
            order = _filtered_order()
            total = len(order)
            scroll_offset = 0
            return (True, 0, total)
        if key == "ctrl-p":
            if 0 <= sel < len(order):
                sid = sessions[order[sel]].get("id", "")
                if sid:
                    toggle_pin(sid)
                    new_order = _filtered_order()
                    try:
                        new_sel = next(pos for pos, oi in enumerate(new_order)
                                       if sessions[oi].get("id") == sid)
                    except StopIteration:
                        new_sel = min(sel, max(0, len(new_order) - 1))
                    order = new_order
                    total = len(order)
                    return (True, new_sel, total)
        return None

    result_pos = _panel_menu_direct(
        render_fn, sys.stderr,
        "type to search · ↑↓ navigate · enter select · ctrl+p pin · backspace delete · esc clear/cancel",
        total, initial_selected,
        on_key=on_key,
        text_input=True,
    )
    if result_pos is None:
        return None
    return order[result_pos]


def select_api_model_menu(
    api_models: list,
    current_id: str = "",
    provider_name: str = "",
) -> Optional[int]:
    """Меню выбора API-модели в стиле таблицы с ценами и контекстом."""
    if not api_models:
        return None

    from models import model_group, model_group_order

    def _group_of(m) -> str:
        return model_group(m.display_name or m.id)

    order = sorted(
        range(len(api_models)),
        key=lambda i: (model_group_order(_group_of(api_models[i])),
                       api_models[i].display_name),
    )

    initial_selected = 0
    for pos, orig in enumerate(order):
        if api_models[orig].id == current_id:
            initial_selected = pos
            break

    total = len(order)
    out_console = Console(file=sys.stderr, highlight=False)
    render_width = max(20, out_console.width - 1)

    term_h = shutil.get_terminal_size((80, 24)).lines
    max_visible = max(3, term_h - 6)
    need_scroll = total > max_visible

    def _viewport(sel: int, scroll_off: int) -> tuple[int, int, int]:
        if not need_scroll:
            return 0, total, 0
        if sel < scroll_off:
            scroll_off = sel
        elif sel >= scroll_off + max_visible:
            scroll_off = sel - max_visible + 1
        scroll_off = max(0, min(scroll_off, total - max_visible))
        return scroll_off, scroll_off + max_visible, scroll_off

    scroll_offset = 0
    if need_scroll and initial_selected >= max_visible:
        scroll_offset = min(initial_selected, total - max_visible)

    def render_fn(sel: int) -> str:
        nonlocal scroll_offset
        vp_start, vp_end, scroll_offset = _viewport(sel, scroll_offset)

        table = Table(
            show_header=True,
            header_style="bold dim",
            border_style="dim",
            padding=(0, 1),
            show_edge=False,
            show_lines=False,
        )
        table.add_column("Model", min_width=22, no_wrap=True, header_style="bold dim yellow")
        table.add_column("Input", justify="right", min_width=7, no_wrap=True, header_style="bold dim yellow")
        table.add_column("Output", justify="right", min_width=7, no_wrap=True, header_style="bold dim yellow")
        table.add_column("Context", justify="right", min_width=8, no_wrap=True, header_style="bold dim yellow")
        table.add_column("ID", min_width=20, no_wrap=True, header_style="bold dim yellow")

        prev_group = _group_of(api_models[order[vp_start - 1]]) if vp_start > 0 else None
        for pos in range(vp_start, vp_end):
            m = api_models[order[pos]]
            group = _group_of(m)
            if group != prev_group:
                table.add_row(
                    Text(group.upper(), style="bold dim yellow"),
                    Text(""), Text(""), Text(""), Text(""),
                )
                prev_group = group
            is_current = m.id == current_id
            is_selected = pos == sel
            marker = "> " if is_selected else "  "

            input_str = f"${m.input_price:.2f}"
            output_str = f"${m.output_price:.2f}"
            ctx_str = _format_context_limit(m.context_window)

            row_bg = Style(bgcolor=t("bg_select")) if is_selected else Style.null()
            if is_current and is_selected:
                style = "bold green"
            elif is_current:
                style = "green"
            elif is_selected:
                style = "bold white"
            else:
                style = ""

            table.add_row(
                Text(marker + m.display_name, style=style),
                Text(input_str, style=style or "cyan"),
                Text(output_str, style=style or "yellow"),
                Text(ctx_str, style=style or "dim"),
                Text(m.id, style=style or "dim"),
                style=row_bg,
            )

        scroll_hint = ""
        if need_scroll:
            scroll_hint = f"({sel + 1}/{total})"
            if vp_start > 0 and vp_end < total:
                scroll_hint = f"↑{vp_start} ↓{total - vp_end} " + scroll_hint
            elif vp_start > 0:
                scroll_hint = f"↑{vp_start} " + scroll_hint
            elif vp_end < total:
                scroll_hint = f"↓{total - vp_end} " + scroll_hint

        title = f"Models: {provider_name}" if provider_name else "Model selection"
        panel = Panel(
            table,
            title=title,
            subtitle=("prices per 1M tokens · " + scroll_hint) if scroll_hint else "prices per 1M tokens",
            title_align="left",
            subtitle_align="right",
            border_style="dim",
            padding=(0, 1),
        )

        buf = StringIO()
        render_console = Console(file=buf, highlight=False, force_terminal=True, width=render_width)
        render_console.print(panel)
        return buf.getvalue()

    result_pos = _panel_menu_direct(render_fn, sys.stderr, "↑↓ navigate · enter select · esc cancel",
                                    total, initial_selected)
    if result_pos is None:
        return None
    return order[result_pos]


def _panel_menu_direct(
    render_fn,
    stream,
    hint_text: str,
    total: int,
    initial_selected: int,
    on_key=None,
    text_input: bool = False,
) -> Optional[int]:
    """
    Общий цикл навигации для панельных меню без мигания.
    render_fn(selected: int) -> str — рендерит панель.
    on_key(key: str, selected: int) -> tuple[bool, int, int] | None:
        Callback для кастомных клавиш. Возвращает (handled, new_selected, new_total)
        если клавиша обработана, иначе None.
    """
    DIM = '\x1b[2m'
    RESET = '\x1b[0m'
    hint_line = f"  {DIM}{hint_text}{RESET}"

    selected = initial_selected

    # Первая отрисовка
    panel_str = render_fn(selected)
    stream.write('\x1b[?25l')  # скрыть курсор
    stream.write(panel_str)
    stream.write(hint_line)
    stream.flush()
    rendered_count = panel_str.count('\n') + 1  # panel lines + hint_line

    try:
        with raw_mode():
            while True:
                key = _drain_text_keys() if text_input else _drain_keys()
                if key == 'up':
                    if total > 0:
                        selected = (selected - 1) % total
                elif key == 'down':
                    if total > 0:
                        selected = (selected + 1) % total
                elif key == 'enter':
                    if total <= 0:
                        continue
                    _clear_stream_lines(stream, rendered_count)
                    stream.write('\x1b[?25h')
                    stream.flush()
                    return selected
                elif key == 'ctrl-c':
                    _clear_stream_lines(stream, rendered_count)
                    stream.write('\x1b[?25h')
                    stream.flush()
                    return None
                else:
                    if on_key is not None:
                        res = on_key(key, selected)
                        if res is not None:
                            _handled, selected, total = res
                            selected = min(selected, max(0, total - 1))
                            if not _handled:
                                _clear_stream_lines(stream, rendered_count)
                                stream.write('\x1b[?25h')
                                stream.flush()
                                return None
                        else:
                            continue
                    elif key == 'escape':
                        _clear_stream_lines(stream, rendered_count)
                        stream.write('\x1b[?25h')
                        stream.flush()
                        return None
                    else:
                        continue

                panel_str = render_fn(selected)
                new_content = panel_str + hint_line
                rendered_count = _move_up_and_overwrite(stream, new_content, rendered_count)
    except Exception:
        _clear_stream_lines(stream, rendered_count)
        stream.write('\x1b[?25h')
        stream.flush()
        return None
