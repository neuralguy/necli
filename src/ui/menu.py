"""Интерактивное меню со стрелками в стиле Rovo Dev."""

import re
import shutil
import sys
import unicodedata
from io import StringIO

from rich.console import Console
from rich.panel import Panel
from rich.style import Style
from rich.table import Table
from rich.text import Text

from config.themes import t
from session._time import format_relative
from ui._keyreader import drain_keys as _drain_keys
from ui._keyreader import drain_text_keys as _drain_text_keys
from ui._keyreader import raw_mode


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

def _clean_menu_text(value: str) -> str:
    cleaned = []
    for ch in str(value):
        category = unicodedata.category(ch)
        if category[0] == "C" or category == "So":
            continue
        if ord(ch) >= 0x1F000 or ch in ("\ufe0e", "\ufe0f"):
            continue
        cleaned.append(ch)
    return " ".join("".join(cleaned).split())


def _safe_menu_text(value: str, max_width: int) -> str:
    text = _clean_menu_text(value)
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


def _strip_ansi(text: str) -> str:
    """Удаляет ANSI-escape последовательности для подсчёта видимой ширины."""
    return re.sub(r'\x1b\[[0-9;?]*[A-Za-z]', '', text)


def _physical_rows(line: str, term_width: int) -> int:
    """Сколько физических строк терминала займёт одна логическая строка.

    Учитывает перенос длинных строк (wrap) и двойную ширину CJK-символов,
    игнорируя ANSI-коды (они не занимают видимых ячеек).
    """
    if term_width <= 0:
        return 1
    visible_w = _cell_width(_strip_ansi(line))
    if visible_w == 0:
        return 1
    return (visible_w + term_width - 1) // term_width


def _move_up_and_overwrite(stream, new_content: str, prev_lines: int) -> int:
    """
    Перемещает курсор вверх на prev_lines, перезаписывает содержимое построчно.
    Каждая строка очищается до конца — устраняет артефакты и мигание.
    Возвращает количество ФИЗИЧЕСКИХ строк терминала в новом контенте
    (с учётом переноса длинных строк), чтобы следующий вызов поднял курсор
    ровно на столько же строк.
    """
    term_width = Console().size.width
    stream.write('\x1b[?25l')  # скрыть курсор
    stream.write('\r')  # начало текущей строки
    # Подняться на prev_lines - 1 физических строк
    for _ in range(max(0, prev_lines - 1)):
        stream.write('\x1b[A')

    # Разбиваем на логические строки и пишем каждую с очисткой остатка.
    lines = new_content.split('\n')
    # Последний элемент может быть пустым (контент заканчивается \n).
    logical = lines if lines[-1] else lines[:-1]

    # Физическое число строк = сумма перенесённых строк по каждой логической.
    new_lines = sum(_physical_rows(ln, term_width) for ln in logical) or 1

    for i, line in enumerate(logical):
        stream.write('\r')  # начало строки
        stream.write(line)
        stream.write('\x1b[K')  # очистить от курсора до конца строки
        if i < len(logical) - 1:
            # Спускаемся на число физических строк, которое заняла записанная
            # строка (а не на одну): при переносе терминал уже сдвинул курсор
            # на rows-1 строк автоматически, поэтому добиваем недостающее.
            rows = _physical_rows(line, term_width)
            stream.write('\x1b[B')
            for _ in range(rows - 1):
                stream.write('\x1b[B')

    # Если старый контент был длиннее — очистить лишние физические строки
    extra = max(0, prev_lines - new_lines)
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
    allow_back: bool = False,
    allow_forward: bool = False,
) -> int | None:
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

    RESET = '\x1b[0m'  # noqa: N806
    DIM = '\x1b[2m'  # noqa: N806

    def _hex_to_ansi_fg(h: str) -> str:
        h = h.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'\x1b[38;2;{r};{g};{b}m'

    def _hex_to_ansi_bg(h: str) -> str:
        h = h.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'\x1b[48;2;{r};{g};{b}m'

    BOLD_BLUE = '\x1b[1m' + _hex_to_ansi_fg(t('accent'))  # noqa: N806
    GREEN = _hex_to_ansi_fg(t('success'))  # noqa: N806
    BOLD = '\x1b[1m'  # noqa: N806
    WHITE = '\x1b[38;2;255;255;255m'  # noqa: N806
    BG_SELECT = _hex_to_ansi_bg(t('bg_select'))  # noqa: N806

    def _render():
        lines = []
        for i, item in enumerate(items):
            label = item["label"]
            hint = item.get("hint", "")
            is_active = item.get("active", False)

            if i == selected:
                marker = f"{BOLD_BLUE}❯{RESET}"
                text = f"{BG_SELECT}{BOLD}{WHITE} {label}{RESET}"
                if hint:
                    text += f"  {BG_SELECT}{BOLD}{WHITE}{hint}{RESET}"
            else:
                marker = " "
                text = f"  {label}"
                if hint:
                    text += f"  {DIM}{hint}{RESET}"
            if is_active:
                text += f"  {GREEN}◄{RESET}"
            lines.append(f"  {marker}{text}")
        return lines

    if allow_back and allow_forward:
        nav_hint = " · ←→ steps"
    elif allow_back:
        nav_hint = " · ← step"
    elif allow_forward:
        nav_hint = " · → step"
    else:
        nav_hint = ""
    hint_line = f"  {DIM}↑↓ select · enter confirm{nav_hint} · esc cancel{RESET}"

    def _build_content():
        parts = []
        if title:
            if '\x1b' in title:
                parts.append(f"  {title}")
            else:
                parts.append(f"  {DIM}{title}{RESET}")
        parts.extend(_render())
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
                elif key in ('ctrl-c', 'escape'):
                    clear_lines(rendered_count)
                    return None
                elif key == 'left' and allow_back:
                    clear_lines(rendered_count)
                    return -(selected + 2)
                elif key == 'right' and allow_forward:
                    clear_lines(rendered_count)
                    return selected
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
) -> int | None:
    """
    Интерактивное меню сессий в стиле панели с таблицей.
    Pinned sessions всегда сверху. P — toggle pin для выделенной сессии.
    Возвращает индекс ВЫБРАННОЙ сессии в ИСХОДНОМ списке sessions, или None.
    """
    if not sessions:
        return None

    from config.pinned import get_pinned
    from config.pinned import toggle as toggle_pin

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
    _probe = Console()
    cols, rows = _probe.size
    render_width = max(20, cols - 1)

    term_h = rows
    max_visible = max(3, (term_h - 8) // 2)
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
    if need_scroll and initial_selected >= max_visible:
        scroll_offset = min(initial_selected, total - max_visible)

    def render_fn(sel: int) -> str:
        nonlocal scroll_offset
        vp_start, vp_end, scroll_offset = _viewport(sel, scroll_offset)
        pinned_ids = get_pinned()
        inner_width = max(20, render_width - 4)
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
            msgs = s.get("messages", 0)
            updated_at = s.get("updated_at", 0)
            tokens = s.get("tokens", 0)
            activity = format_relative(updated_at) if updated_at else "—"
            is_current = sid == current_id
            is_selected = pos == sel
            marker = "› " if is_selected else "  "
            pin = "✱ " if is_pinned else ""
            prefix_w = _cell_width(marker + pin)
            title = _safe_menu_text(
                s.get("title", "") or "Untitled Session",
                max(1, inner_width - prefix_w),
            )

            row_bg = Style(bgcolor=t("bg_select")) if is_selected else Style.null()
            if is_current and is_selected:
                title_style = row_bg + Style.parse("bold green")
            elif is_current:
                title_style = row_bg + Style.parse("green")
            elif is_selected:
                title_style = row_bg + Style.parse("bold white")
            else:
                title_style = row_bg + Style.parse("green")

            folder = str(s.get("working_dir", "")).rstrip("/").rsplit("/", 1)[-1]
            meta = f"{activity} · {msgs} msgs · {_format_tokens_short(tokens)}"
            if folder:
                meta += f" · {folder}"
            meta_style = row_bg + Style.parse("bold white") if is_selected else row_bg + Style(dim=True)
            body.append("\n")
            body.append(_pad_menu_text(marker + pin + title, inner_width), style=title_style)
            body.append("\n")
            body.append(_pad_menu_text("  " + _safe_menu_text(meta, inner_width - 2), inner_width), style=meta_style)

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
        if key == "ctrl-p" and 0 <= sel < len(order):
            sid = sessions[order[sel]].get("id", "")
            if sid:
                toggle_pin(sid)
                order = _filtered_order()
                total = len(order)
                scroll_offset = 0
                return (True, 0, total)
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
    group_labels: list[str] | None = None,
) -> int | None:
    """Меню выбора API-модели с поиском/фильтрацией по названию и ID.

    group_labels: если задан (параллельно api_models), секции формируются по
    этим меткам (напр. провайдерам) с сохранением исходного порядка. Иначе —
    группировка по семейству модели с сортировкой.
    """
    if not api_models:
        return None

    from models import model_group, model_group_order

    def _group_of_idx(i: int) -> str:
        if group_labels is not None:
            return group_labels[i]
        m = api_models[i]
        return model_group(m.display_name or m.id)

    def _sorted_indices() -> list[int]:
        if group_labels is not None:
            return list(range(len(api_models)))
        return sorted(
            range(len(api_models)),
            key=lambda i: (model_group_order(_group_of_idx(i)),
                           api_models[i].display_name),
        )

    query = ""

    def _matches(orig_idx: int) -> bool:
        if not query:
            return True
        m = api_models[orig_idx]
        haystack = f"{m.display_name} {m.id}".casefold()
        return query.casefold() in haystack

    def _filtered_order() -> list[int]:
        return [i for i in _sorted_indices() if _matches(i)]

    order = _filtered_order()

    initial_selected = 0
    for pos, orig in enumerate(order):
        if api_models[orig].id == current_id:
            initial_selected = pos
            break

    total = len(order)
    out_console = Console(file=sys.stderr, highlight=False)
    render_width = max(20, out_console.width - 1)

    term_h = shutil.get_terminal_size((80, 24)).lines
    max_visible = max(3, term_h - 12)

    def _build_rows() -> list:
        """Плоский список строк таблицы: ('group', name) и ('model', order_idx).
        Заголовки групп учитываются как реальные строки — окно скролла берётся
        по этому списку, а не по позициям моделей, чтобы не вылезать за экран.
        """
        rows = []
        prev_group = None
        for orig_idx in order:
            grp = _group_of_idx(orig_idx)
            if grp != prev_group:
                rows.append(("group", grp))
                prev_group = grp
            rows.append(("model", orig_idx))
        return rows

    def _row_of_model_pos(model_pos: int, rows: list) -> int:
        seen = -1
        for ridx, row in enumerate(rows):
            if row[0] == "model":
                seen += 1
                if seen == model_pos:
                    return ridx
        return 0

    def _viewport(sel: int, scroll_off: int, n_rows: int) -> tuple[int, int, int]:
        if n_rows <= max_visible:
            return 0, n_rows, 0
        if sel < scroll_off:
            scroll_off = sel
        elif sel >= scroll_off + max_visible:
            scroll_off = sel - max_visible + 1
        scroll_off = max(0, min(scroll_off, n_rows - max_visible))
        return scroll_off, scroll_off + max_visible, scroll_off

    # Фиксированная ширина колонок: считаем единожды по всем строкам,
    # чтобы при скролле viewport'а ширина не пересчитывалась и не дёргалась.
    _marker = "> "
    _model_w = _cell_width("Model")
    _in_w = _cell_width("In")
    _out_w = _cell_width("Out")
    _ctx_w = _cell_width("Ctx")
    _id_w = _cell_width("ID")
    for orig in order:
        m = api_models[orig]
        _model_w = max(_model_w, _cell_width(m.display_name) + len(_marker))
        _in_w = max(_in_w, _cell_width(f"${m.input_price:.2f}"))
        _out_w = max(_out_w, _cell_width(f"${m.output_price:.2f}"))
        _ctx_w = max(_ctx_w, _cell_width(_format_context_limit(m.context_window)))
        _id_w = max(_id_w, _cell_width(m.id))
        grp = _group_of_idx(orig)
        _model_w = max(_model_w, _cell_width(grp.upper()))

    scroll_offset = 0
    flat_rows = _build_rows()
    flat_total = len(flat_rows)

    def render_fn(sel: int) -> str:
        nonlocal scroll_offset, flat_rows, flat_total
        flat_rows = _build_rows()
        flat_total = len(flat_rows)
        sel_row = _row_of_model_pos(sel, flat_rows)
        vp_start, vp_end, scroll_offset = _viewport(sel_row, scroll_offset, flat_total)

        table = Table(
            show_header=True,
            header_style="bold dim",
            border_style="dim",
            padding=(0, 1),
            show_edge=False,
            show_lines=False,
            width=render_width,
        )
        table.add_column("Model", width=_model_w, max_width=_model_w, min_width=_model_w, no_wrap=True, header_style="bold dim yellow")
        table.add_column("In", justify="right", width=_in_w, max_width=_in_w, min_width=_in_w, no_wrap=True, header_style="bold dim yellow")
        table.add_column("Out", justify="right", width=_out_w, max_width=_out_w, min_width=_out_w, no_wrap=True, header_style="bold dim yellow")
        table.add_column("Ctx", justify="right", width=_ctx_w, max_width=_ctx_w, min_width=_ctx_w, no_wrap=True, header_style="bold dim yellow")
        table.add_column("ID", no_wrap=True, min_width=_id_w, max_width=_id_w, header_style="bold dim yellow")

        for ridx in range(vp_start, vp_end):
            row = flat_rows[ridx]
            if row[0] == "group":
                table.add_row(
                    Text(row[1].upper(), style="bold dim yellow"),
                    Text(""), Text(""), Text(""), Text(""),
                )
                continue
            orig_idx = row[1]
            m = api_models[orig_idx]
            is_current = m.id == current_id
            is_selected = ridx == sel_row
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
        if flat_total > max_visible:
            scroll_hint = f"({sel + 1}/{total})"
            if vp_start > 0 and vp_end < flat_total:
                scroll_hint = f"↑{vp_start} ↓{flat_total - vp_end} " + scroll_hint
            elif vp_start > 0:
                scroll_hint = f"↑{vp_start} " + scroll_hint
            elif vp_end < flat_total:
                scroll_hint = f"↓{flat_total - vp_end} " + scroll_hint

        search_text = f"🔍 {query}▌" if query else "🔍 type to search by name or id..."
        search_style = "bold cyan" if query else "dim"
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
        search_panel = Panel(
            Text(search_text, style=search_style),
            title="Search",
            title_align="left",
            border_style="cyan" if query else "dim",
            padding=(0, 1),
        )

        buf = StringIO()
        render_console = Console(file=buf, highlight=False, force_terminal=True, width=render_width)
        render_console.print(search_panel)
        render_console.print(panel)
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
            return None
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
        return None

    result_pos = _panel_menu_direct(
        render_fn, sys.stderr,
        "type to search · ↑↓ navigate · enter select · backspace delete · esc clear/cancel",
        total, initial_selected,
        on_key=on_key,
        text_input=True,
    )
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
    allow_back: bool = False,
    allow_forward: bool = False,
) -> int | None:
    """
    Общий цикл навигации для панельных меню без мигания.
    render_fn(selected: int) -> str — рендерит панель.
    on_key(key: str, selected: int) -> tuple[bool, int, int] | None:
        Callback для кастомных клавиш. Возвращает (handled, new_selected, new_total)
        если клавиша обработана, иначе None.
    """
    DIM = '\x1b[2m'  # noqa: N806
    RESET = '\x1b[0m'  # noqa: N806
    if allow_back and allow_forward:
        nav_suffix = " · ←→ steps"
    elif allow_back:
        nav_suffix = " · ← step"
    elif allow_forward:
        nav_suffix = " · → step"
    else:
        nav_suffix = ""
    hint_line = f"  {DIM}{hint_text}{nav_suffix}{RESET}"

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
                elif key == 'left' and allow_back:
                    _clear_stream_lines(stream, rendered_count)
                    stream.write('\x1b[?25h')
                    stream.flush()
                    return -(selected + 2)
                elif key == 'right' and allow_forward:
                    _clear_stream_lines(stream, rendered_count)
                    stream.write('\x1b[?25h')
                    stream.flush()
                    return selected
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
