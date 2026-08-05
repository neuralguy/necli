"""Интерактивные меню.

Панельные виджеты (`select_session_menu`, `select_api_model_menu`) живут в
нижней зоне постоянного Application — см. `ui/shell.py`.

Здесь же лежат текстовые примитивы безрамочных виджетов (`cell`, `pad`, `fit`,
`row_line`, `section_line`). Они нужны и меню в `commands/menus/*`, поэтому
живут в ui-слое: обратная зависимость `ui → commands` закольцевала бы импорты,
так как `ui/overlays.py` откатывается на этот модуль в headless-режиме.

Синхронные `select_menu` и `_panel_menu_direct` остаются рабочими: пока
Application не поднят (headless, не-TTY, ранний старт, онбординг), рисовать
некому, и `ui/overlays.py` сознательно падает обратно на них.
"""

import asyncio
import logging
import shutil
import sys
import time

from rich.console import Console

from config.i18n import t as tr
from config.themes import t
from session._time import format_relative
from ui._keyreader import drain_keys as _drain_keys
from ui._keyreader import drain_text_keys as _drain_text_keys
from ui._keyreader import raw_mode
from ui.overlays import (
    BOLD,
    DIM,
    RESET,
    cell_width,
    clip,
    key_hints,
    more_note,
    pad,
    white_fg,
    paint,
    role_fg,
    row,
    scroll_window,
    section,
    two_column,
)

logger = logging.getLogger(__name__)


# ─────────────── мост «синхронный вызывающий → асинхронный виджет» ───────────
# Виджеты стали корутинами, но два пути к ним остались синхронными и переписать
# их здесь нельзя: исполнение инструментов (`executor._execute_single`) и первый
# запуск (click-команда `interactive` зовёт онбординг ДО `asyncio.run`).
# Поэтому мост, а не «await» через силу. Как только `_execute_single` станет
# корутиной — вызовы через `run_ui_sync` заменяются на прямой await, и всё это
# уходит целиком.

def _shell_loop():
    """Event loop, в котором крутится Application, либо None."""
    try:
        from ui.shell import get_shell
        shell = get_shell()
    except Exception:
        return None
    app = getattr(shell, "app", None) if shell is not None else None
    loop = getattr(app, "loop", None)
    if loop is None or loop.is_closed():
        return None
    return loop


def _drive_without_loop(coro):
    """Прокрутить корутину, которая обязана завершиться без единого await.

    Так и происходит, когда `get_shell()` пуст: оверлеи внутри уходят на
    синхронный путь и ничего не ждут. Если корутина всё же ушла в await —
    честно падаем, а не вешаемся: без работающего loop'а её никто не разбудит.
    """
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    coro.close()
    raise RuntimeError("виджет ушёл в await, а event loop недоступен")


def _run_detached(coro):
    """Синхронный вызывающий сидит В loop'е Application — отдать оверлей нельзя.

    Так исполняются fenced-инструменты: `apis/_retry` зовёт `on_chunk`
    синхронно из корутины стрима, дальше `stream.on_text_update` →
    `executor._execute_single`. Пока мы здесь, loop стоит, клавиш Application не
    разбирает, и оверлей никогда не получит ответ. Докрутить loop руками asyncio
    не даёт («Cannot enter into task … while another task is being executed»).

    Поэтому на этот один вызов честно откатываемся к прежнему поведению: гасим
    отрисовку Application, снимаем singleton (виджеты уходят синхронным путём),
    возвращаем настоящие stdout/stderr вместо прокси patch_stdout — тот
    складывает вывод в буфер и сливает его через loop, который стоит. После
    ответа singleton возвращается, Application перерисовывается.

    Уйдёт вместе с этой функцией, как только `_execute_single` станет корутиной.
    """
    from ui.shell import Shell
    shell = Shell.instance()
    app = getattr(shell, "app", None) if shell is not None else None
    logger.info("виджет вызван из loop'а — рисуем синхронно, минуя Application")
    if app is not None:
        try:
            app.renderer.erase()
        except Exception:
            logger.debug("renderer.erase failed", exc_info=True)
    out, err = sys.stdout, sys.stderr
    if shell is not None:
        Shell.set_instance(None)
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__
    try:
        return _drive_without_loop(coro)
    finally:
        sys.stdout, sys.stderr = out, err
        if shell is not None:
            Shell.set_instance(shell)
            shell.invalidate()


def run_ui_sync(coro):
    """Выполнить корутину виджета из синхронного кода и вернуть её результат."""
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        return _run_detached(coro)

    loop = _shell_loop()
    if loop is not None and loop.is_running():
        # Рабочий поток executor'а: блокируем только его, loop живёт дальше и
        # спокойно рисует оверлей.
        return asyncio.run_coroutine_threadsafe(coro, loop).result()

    # Loop'а нет вовсе — обычный ранний старт (онбординг) или headless.
    # Если singleton Shell'а всё ещё висит, но его loop уже мёртв (выход из
    # приложения), оверлеи ждали бы клавиш от несуществующего Application,
    # поэтому на время прогона снимаем singleton.
    from ui.shell import Shell
    stale = Shell.instance()
    if stale is not None:
        Shell.set_instance(None)
    try:
        return asyncio.run(coro)
    finally:
        if stale is not None:
            Shell.set_instance(stale)


def _format_context_limit(limit: int) -> str:
    if limit >= 1_000_000:
        return f"{limit // 1_000_000}M"
    if limit >= 1000:
        return f"{limit // 1000}K"
    return str(limit)


# ──────────────── примитивы безрамочных виджетов нижней зоны ─────────────────
# Стиль задаёт ui/overlays.py: оттуда берутся и цвета (role_fg/paint), и кирпичи
# разметки (row/section/two_column/scroll_window/more_note). Своего набора
# ANSI-констант здесь нет намеренно — два набора неизбежно разъехались бы.
# Ниже только то, чего в overlays нет: многоколоночная строка и окно списка,
# посчитанное по бюджету Shell.

#: Отступ до текста строки: два пробела + курсор + пробел (как в overlays.row).
ROW_INDENT = 4


def cell(value: str, width: int, align: str = "left") -> str:
    """Ячейка колонки: обрезать по ширине, затем добить пробелами."""
    return pad(clip(str(value), width), width, align)


def row_avail(width: int, mark: str = "") -> int:
    """Сколько ячеек остаётся под содержимое строки после курсора и глифа."""
    return max(4, width - 1 - ROW_INDENT - (cell_width(mark) + 1 if mark else 0))


def columns(cells, *, plain: bool = False) -> str:
    """Склеить ячейки `(текст, цвет)` в содержимое строки для `overlays.row`.

    На выделенной строке собственные цвета колонок гасим: `row()` красит всю
    полосу (bold white на `bg_select`), и локальные оттенки сделали бы её пёстрой.
    """
    if plain:
        return "".join(text for text, _style in cells)
    return "".join(f"{style}{text}{RESET}" if style else text for text, style in cells)


class Palette:
    """Цвета темы одним объектом на кадр отрисовки.

    Тема переключается на ходу (`/themes`), поэтому кэшировать её между кадрами
    нельзя — но и звать `role_fg` на каждую ячейку списка незачем.
    """

    __slots__ = ("accent", "bold", "dim", "error", "muted", "reset",
                 "success", "warning", "white")

    def __init__(self) -> None:
        self.reset = RESET
        self.dim = DIM
        self.bold = BOLD
        self.white = white_fg()
        self.accent = role_fg("accent")
        self.success = role_fg("success")
        self.warning = role_fg("warning")
        self.error = role_fg("error")
        self.muted = role_fg("muted")


def row_line(cells, width: int, *, selected: bool = False, active: bool = False,
             mark: str = "", mark_role: str = "", pal: Palette | None = None) -> str:
    """Строка списка с колонками поверх `overlays.row`.

    `overlays.row` умеет метку, подсказку и правый значок, но не произвольные
    выровненные колонки, которые нужны /models и /sessions. Колонки собираем
    здесь, а курсор, подсветку и обрезку по ширине отдаём общему кирпичу.
    """
    return row(
        columns(cells, plain=selected),
        selected=selected, width=width,
        mark=mark, mark_role=mark_role,
        right="◄" if active else "", right_role="success",
    )


def section_line(text: str, width: int, *, right: str = "",
                 pal: Palette | None = None, bold: bool = False) -> str:
    """Заголовок секции/шапки внутри виджета — приглушённый, без рамок.

    Правая приписка (счётчик, подпись) исчезает целиком, если на узком экране
    для неё нет места: обрезанный счётчик хуже отсутствующего.
    """
    room = width - ROW_INDENT - 1
    if right and cell_width(right) > room - 8:
        right = ""
    left = clip(text, max(4, room - (cell_width(right) + 2 if right else 0)))
    if not bold:
        return section(left, right=right, width=width)
    return two_column(paint(left, "accent", bold=True),
                      f"{DIM}{right}{RESET}" if right else "", width=width)


def search_line(query: str, width: int, placeholder: str = "",
                pal: Palette | None = None) -> str:
    """Строка поиска панельных меню: `/ запрос▌` либо приглушённая подсказка."""
    room = max(4, width - ROW_INDENT - 3)
    if query:
        return ("  " + paint("/", "accent") + " " + BOLD + white_fg()
                + clip(query, room) + RESET + paint("▌", "accent"))
    return f"  {DIM}/ {clip(placeholder or 'type to search', room)}{RESET}"


def overlay_rows(reserve: int = 0) -> int:
    """Сколько строк доступно телу виджета (минус `reserve` на шапку).

    Бюджет спрашиваем у Shell: он знает высоту динамической зоны, рамки и
    подсказки. Без Shell (headless) считаем по размеру терминала.
    """
    try:
        from ui.shell import get_shell
        shell = get_shell()
        if shell is not None:
            return max(1, shell.overlay_budget() - reserve)
    except Exception:
        logger.debug("overlay_budget unavailable", exc_info=True)
    return max(1, shutil.get_terminal_size((80, 24)).lines - 8 - reserve)


def render_width(default: int = 100) -> int:
    """Ширина тела виджета в колонках.

    `render_fn(selected)` ширину не получает (протокол панельных меню не
    меняем), поэтому спрашиваем её у того же источника, что и Shell, — иначе
    колонки разъедутся с линиями рамки.
    """
    try:
        from ui.shell import get_shell
        shell = get_shell()
        app = getattr(shell, "app", None) if shell is not None else None
        if app is not None:
            return max(24, app.output.get_size().columns)
    except Exception:
        logger.debug("render_width via shell failed", exc_info=True)
    try:
        return max(24, shutil.get_terminal_size((default, 24)).columns)
    except Exception:
        return default


def clear_lines(n: int):
    """Очищает n строк вверх."""
    sys.stdout.write('\r\x1b[2K')
    for _ in range(n - 1):
        sys.stdout.write('\x1b[A')
        sys.stdout.write('\x1b[2K')
    sys.stdout.flush()


def _physical_rows(line: str, term_width: int) -> int:
    """Сколько физических строк терминала займёт одна логическая строка.

    Учитывает перенос длинных строк (wrap) и двойную ширину CJK-символов,
    игнорируя ANSI-коды (они не занимают видимых ячеек).
    """
    if term_width <= 0:
        return 1
    visible_w = cell_width(line)
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


def _normalize_panel_key(key: str) -> str:
    """Сводит имена клавиш двух источников к одному словарю.

    Оверлей Shell'а присылает имена prompt_toolkit ("c-p", "space"), а legacy
    `drain_text_keys` — свои ("ctrl-p", " "). Один и тот же `on_key` работает в
    обоих режимах, поэтому названия приводим здесь, а не в двух рендерерах.
    """
    if key == "c-p":
        return "ctrl-p"
    if key == "space":
        return " "
    return key


async def _run_panel(render_fn, hint_text: str, total: int, initial_selected: int,
                     on_key=None) -> int | None:
    """Показать панельный виджет с поиском по вводу.

    Есть Shell → оверлей в нижней зоне. Нет Shell → прежний прямой вывод в
    stderr. Флаг `text_input` тут НЕ пробрасывается в overlays.panel_menu
    специально: в Shell он делает буфер ptk владельцем печатных клавиш, и они
    перестают доходить до `on_key`, где живёт строка поиска этих панелей.
    Legacy-циклу тот же флаг нужен ровно наоборот — иначе `drain_keys`
    переиначит q/j/k в команды навигации вместо букв запроса.
    """
    from ui.shell import get_shell
    if get_shell() is None:
        return _panel_menu_direct(
            render_fn, sys.stderr, hint_text, total, initial_selected,
            on_key=on_key, text_input=True,
        )
    from ui import overlays
    return await overlays.panel_menu(
        render_fn, hint_text, total, initial_selected, on_key=on_key,
    )


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
    WHITE = _hex_to_ansi_fg(t('fg_primary'))  # noqa: N806
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



def _tokens_short(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n // 1000}K"
    return f"{n / 1_000_000:.1f}M"


def _short_ago(ts: float) -> str:
    """Компактное «когда»: колонка не должна съедать треть ширины экрана."""
    if not ts:
        return "—"
    diff = max(0.0, time.time() - ts)
    if diff < 60:
        return "now"
    if diff < 3600:
        return f"{int(diff // 60)} min"
    if diff < 86400:
        return f"{int(diff // 3600)} h"
    if diff < 86400 * 7:
        return f"{int(diff // 86400)} d"
    return format_relative(ts)


async def select_session_menu(
    sessions: list[dict],
    current_id: str = "",
) -> int | None:
    """Меню выбора сессии: поиск, пины, плоский список без рамок.

    Возвращает индекс ВЫБРАННОЙ сессии в ИСХОДНОМ списке sessions, или None.
    """
    if not sessions:
        return None

    from config.pinned import get_pinned
    from config.pinned import toggle as toggle_pin

    # Пины лежат в JSON на диске, а get_pinned() читает файл КАЖДЫЙ раз.
    # Прежний рендер звал его из render_fn, то есть перечитывал диск ~20 раз в
    # секунду. Держим набор в памяти и обновляем только по ctrl+p.
    pinned_ids = get_pinned()
    query = ""

    # Текст ячеек не зависит ни от курсора, ни от ширины — считаем один раз.
    static: list[tuple[str, str, str, str, str]] = []
    for s in sessions:
        folder = str(s.get("working_dir", "")).rstrip("/").rsplit("/", 1)[-1]
        static.append((
            str(s.get("title", "") or tr("menu.untitled_session")),
            _short_ago(s.get("updated_at", 0)),
            f"{s.get('messages', 0)} msg",
            _tokens_short(int(s.get("tokens", 0) or 0)),
            folder,
        ))
    haystacks = [
        " ".join(str(s.get(k, "")) for k in ("title", "id", "site", "last_model")).casefold()
        for s in sessions
    ]

    def _filtered_order() -> list[int]:
        q = query.casefold()
        pinned_pos, rest = [], []
        for i, s in enumerate(sessions):
            if q and q not in haystacks[i]:
                continue
            (pinned_pos if s.get("id") in pinned_ids else rest).append(i)
        return pinned_pos + rest

    order = _filtered_order()
    total = len(order)

    initial_selected = 0
    for pos, orig_idx in enumerate(order):
        if sessions[orig_idx].get("id") == current_id:
            initial_selected = pos
            break

    version = 0                 # растёт при смене запроса или пинов
    layout_cache: dict = {}     # ширины колонок: (ширина, версия) → tuple

    def _layout(width: int) -> tuple[int, int, int, int, int]:
        """Ширины колонок под текущую ширину экрана.

        Считаем по видимому списку и кэшируем: пересчёт нужен только когда
        поменялся фильтр или размер окна, а не на каждом кадре.
        """
        key = (width, version)
        cached = layout_cache.get(key)
        if cached is not None:
            return cached
        when_w = msgs_w = tok_w = dir_w = 0
        for i in order:
            _t, when, msgs, tok, folder = static[i]
            when_w = max(when_w, cell_width(when))
            msgs_w = max(msgs_w, cell_width(msgs))
            tok_w = max(tok_w, cell_width(tok))
            dir_w = max(dir_w, cell_width(folder))
        dir_w = min(dir_w, 16)
        # Узкий терминал: сначала уходит папка, потом токены, потом счётчик
        # сообщений. Заголовок не сокращаем никогда — без него список слепой.
        free = width - ROW_INDENT - 1 - 3       # 3 — колонка состояния (пин/активна)
        for drop in range(4):
            widths = [when_w, msgs_w, tok_w, dir_w]
            for d in range(drop):
                widths[3 - d] = 0
            used = sum(w + 2 for w in widths if w)
            if free - used >= 18 or drop == 3:
                res = (max(8, min(free - used, 72)), *widths)
                layout_cache[key] = res
                return res
        return (18, when_w, msgs_w, tok_w, dir_w)  # pragma: no cover

    def render_fn(sel: int) -> str:
        pal = Palette()
        width = render_width()
        title_w, when_w, msgs_w, tok_w, dir_w = _layout(width)
        budget = max(3, overlay_rows(reserve=2))
        start, end, above, below = scroll_window(total, sel, budget)

        lines = [
            section_line(tr("menu.sessions_title"), width, bold=True, pal=pal,
                         right=f"{sel + 1}/{total}" if total else ""),
            search_line(query, width, "type to search by title, id or model", pal),
        ]
        if total == 0:
            lines.append(f"  {pal.dim}{tr('common.no_data')}{pal.reset}")
            return "\n".join(lines)

        if above:
            lines.append(more_note(above, up=True))
        for pos in range(start, end):
            orig = order[pos]
            title, when, msgs, tok, folder = static[orig]
            sid = sessions[orig].get("id", "")
            is_current = sid == current_id
            cells = [
                ("✱" if sid in pinned_ids else " ", pal.warning),
                ("● " if is_current else "  ", pal.success),
                (cell(title, title_w), pal.success if is_current else ""),
            ]
            if when_w:
                cells.append(("  " + cell(when, when_w, "right"), pal.dim))
            if msgs_w:
                cells.append(("  " + cell(msgs, msgs_w, "right"), pal.dim))
            if tok_w:
                cells.append(("  " + cell(tok, tok_w, "right"), pal.dim))
            if dir_w:
                cells.append(("  " + cell(folder, dir_w), pal.muted))
            lines.append(row_line(cells, width, selected=pos == sel, pal=pal))
        if below:
            lines.append(more_note(below, up=False))
        return "\n".join(lines)

    def on_key(key: str, sel: int):
        nonlocal order, query, total, version, pinned_ids
        key = _normalize_panel_key(key)

        def _refilter(keep: int):
            nonlocal order, total, version
            order = _filtered_order()
            total = len(order)
            version += 1
            return (True, min(keep, max(0, total - 1)), total)

        if key == "backspace":
            if query:
                query = query[:-1]
            return _refilter(sel)
        if key == "escape":
            if query:
                query = ""
                return _refilter(sel)
            return (False, sel, total)
        if key == "ctrl-p" and 0 <= sel < len(order):
            sid = sessions[order[sel]].get("id", "")
            if sid:
                toggle_pin(sid)
                pinned_ids = get_pinned()
                return _refilter(0)
            return None
        if len(key) == 1 and key.isprintable():
            query += key
            return _refilter(0)
        return None

    result_pos = await _run_panel(
        render_fn,
        # Подсказка живёт под рамкой одной строкой: на узком терминале длинный
        # текст обрезался бы посреди слова, поэтому формат компактный.
        key_hints(("type", "to search"), ("↑↓", "move"), ("enter", "open"),
                  ("^p", "pin"), ("esc", "close")),
        total, initial_selected,
        on_key=on_key,
    )
    if result_pos is None or not order:
        return None
    return order[min(result_pos, len(order) - 1)]


async def select_api_model_menu(
    api_models: list,
    current_id: str = "",
    provider_name: str = "",
    group_labels: list[str] | None = None,
) -> int | None:
    """Меню выбора API-модели с поиском по названию и ID.

    group_labels: если задан (параллельно api_models), секции формируются по
    этим меткам (напр. провайдерам) с сохранением исходного порядка. Иначе —
    группировка по семейству модели с сортировкой.

    Возвращает индекс в ИСХОДНОМ списке api_models либо None.
    """
    if not api_models:
        return None

    from models import model_group, model_group_order

    # Всё, что не зависит от курсора, считаем один раз. Прежний рендер собирал
    # плоский список строк и Rich-таблицу заново на КАЖДОМ кадре (тикер даёт
    # 10 fps, а кадр зовёт рендер дважды) — отсюда и тормоза на больших списках.
    if group_labels is not None:
        groups = list(group_labels)
    else:
        groups = [model_group(m.display_name or m.id) for m in api_models]
    static = [
        (m.display_name, f"${m.input_price:.2f}", f"${m.output_price:.2f}",
         _format_context_limit(m.context_window), m.id)
        for m in api_models
    ]
    haystacks = [f"{m.display_name} {m.id}".casefold() for m in api_models]
    if group_labels is not None:
        base_order = list(range(len(api_models)))
    else:
        base_order = sorted(
            range(len(api_models)),
            key=lambda i: (model_group_order(groups[i]), api_models[i].display_name),
        )

    query = ""
    order: list[int] = []
    flat: list[int] = []        # >= 0 — индекс модели, -1 — заголовок группы
    flat_group: list[str] = []  # текст заголовка для строк-групп
    row_of_pos: list[int] = []  # позиция модели в order → строка в flat
    total = 0
    version = 0                 # растёт при смене запроса: сбрасывает кэши

    def _rebuild() -> None:
        """Пересчёт фильтра и плоского списка строк — только при смене запроса."""
        nonlocal order, flat, flat_group, row_of_pos, total, version
        q = query.casefold()
        order = [i for i in base_order if not q or q in haystacks[i]]
        flat, flat_group, row_of_pos = [], [], []
        prev = None
        for i in order:
            if groups[i] != prev:
                flat.append(-1)
                flat_group.append(groups[i])
                prev = groups[i]
            row_of_pos.append(len(flat))
            flat.append(i)
            flat_group.append("")
        total = len(order)
        version += 1

    _rebuild()

    initial_selected = 0
    for pos, orig in enumerate(order):
        if api_models[orig].id == current_id:
            initial_selected = pos
            break

    layout_cache: dict = {}

    def _layout(width: int) -> tuple[int, int, int, int, int]:
        """Ширины колонок: (name, in, out, ctx, id); 0 — колонка не влезла.

        На узком терминале первым уходит ID, затем Ctx, затем цены: имя модели
        не жертвуем никогда, без него список нечитаем.
        """
        key = (width, version)
        cached = layout_cache.get(key)
        if cached is not None:
            return cached
        # Колонка не уже своего заголовка: обрезанный «Con…» читается хуже,
        # чем пара лишних пробелов под коротким значением.
        name_w = cell_width(tr("menu.col_model")) + 2
        in_w = cell_width(tr("menu.col_input"))
        out_w = cell_width(tr("menu.col_output"))
        ctx_w = cell_width(tr("menu.col_context"))
        id_w = cell_width(tr("menu.col_id"))
        for i in order:
            name, price_in, price_out, ctx, mid = static[i]
            name_w = max(name_w, cell_width(name) + 2)
            in_w = max(in_w, cell_width(price_in))
            out_w = max(out_w, cell_width(price_out))
            ctx_w = max(ctx_w, cell_width(ctx))
            id_w = max(id_w, cell_width(mid))
        free = width - ROW_INDENT - 1
        for drop in range(5):
            widths = [in_w, out_w, ctx_w, min(id_w, 34)]
            for d in range(drop):
                widths[3 - d] = 0
            used = sum(w + 2 for w in widths if w)
            if free - used >= 16 or drop == 4:
                res = (max(10, min(name_w, free - used)), *widths)
                layout_cache[key] = res
                return res
        return (16, 0, 0, 0, 0)  # pragma: no cover — цикл всегда возвращает раньше

    def render_fn(sel: int) -> str:
        pal = Palette()
        width = render_width()
        name_w, in_w, out_w, ctx_w, id_w = _layout(width)
        budget = max(3, overlay_rows(reserve=3))
        sel_row = row_of_pos[sel] if 0 <= sel < len(row_of_pos) else 0
        start, end, above, below = scroll_window(len(flat), sel_row, budget)

        title = tr("menu.models_for", name=provider_name) if provider_name \
            else tr("menu.model_title")
        head = [
            section_line(title, width, bold=True, pal=pal,
                         right=f"{sel + 1}/{total}" if total else ""),
            search_line(query, width, "type to search by name or id", pal),
        ]
        if not order:
            head.append(f"  {pal.dim}{tr('common.no_data')}{pal.reset}")
            return "\n".join(head)

        cols = [("  " + cell(tr("menu.col_model"), name_w - 2), pal.dim)]
        if in_w:
            cols.append(("  " + cell(tr("menu.col_input"), in_w, "right"), pal.dim))
        if out_w:
            cols.append(("  " + cell(tr("menu.col_output"), out_w, "right"), pal.dim))
        if ctx_w:
            cols.append(("  " + cell(tr("menu.col_context"), ctx_w, "right"), pal.dim))
        if id_w:
            cols.append(("  " + cell(tr("menu.col_id"), id_w), pal.dim))
        head.append(row_line(cols, width, pal=pal))

        lines = head
        if above:
            lines.append(more_note(above, up=True))
        for ridx in range(start, end):
            orig = flat[ridx]
            if orig < 0:
                lines.append(row_line(
                    [(clip(flat_group[ridx].upper(), name_w), pal.bold + pal.accent)],
                    width, pal=pal))
                continue
            name, price_in, price_out, ctx, mid = static[orig]
            is_current = mid == current_id
            # Колонка состояния фиксированной ширины: иначе активная модель
            # съезжала бы вправо относительно соседних строк.
            cells = [("● " if is_current else "  ", pal.success),
                     (cell(name, name_w - 2), pal.success if is_current else "")]
            if in_w:
                cells.append(("  " + cell(price_in, in_w, "right"), pal.dim))
            if out_w:
                cells.append(("  " + cell(price_out, out_w, "right"), pal.dim))
            if ctx_w:
                cells.append(("  " + cell(ctx, ctx_w, "right"), pal.dim))
            if id_w:
                cells.append(("  " + cell(mid, id_w), pal.muted))
            lines.append(row_line(cells, width, selected=ridx == sel_row, pal=pal))
        if below:
            lines.append(more_note(below, up=False))
        return "\n".join(lines)

    def on_key(key: str, sel: int):
        nonlocal query
        key = _normalize_panel_key(key)

        def _refilter(keep: int):
            _rebuild()
            layout_cache.clear()
            return (True, min(keep, max(0, total - 1)), total)

        if key == "backspace":
            if query:
                query = query[:-1]
                return _refilter(sel)
            return None
        if key == "escape":
            if query:
                query = ""
                return _refilter(sel)
            return (False, sel, total)
        if len(key) == 1 and key.isprintable():
            query += key
            return _refilter(0)
        return None

    result_pos = await _run_panel(
        render_fn,
        key_hints(("type", "to search"), ("↑↓", "move"), ("enter", "select"),
                  ("esc", "close")),
        total, initial_selected,
        on_key=on_key,
    )
    if result_pos is None or not order:
        return None
    return order[min(result_pos, len(order) - 1)]


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
