"""Интерактивные виджеты нижней зоны поверх Shell.

Здесь живут замены старым raw-termios циклам из `ui/menu.py`. Четыре вещи важны:

1. **Никаких рамок.** Пока оверлей открыт, Shell скрывает обе линии поля
   ввода. Внутри виджета нет ни `Panel`, ни `box=`-таблиц,
   ни `│`: выравнивание держится на колонках из пробелов, группировка — на пустых
   строках и приглушённых заголовках, выделение — на фоне `bg_select`.

2. **Протокол возврата не меняется.** `int` — выбран пункт, `None` — отмена,
   `-(selected + 2)` — шаг назад при `allow_back`. Благодаря этому ~45 мест
   вызова остаются нетронутыми: им нужен только `await`.

3. **Работает параллельно агенту.** Агент продолжает работать, но его SSE-кадры не
   инвалидируют неподвижный виджет; готовый вывод атомарно появляется после
   закрытия оверлея.

4. **Рисуется только видимое.** Список режется по `shell.overlay_budget()`
   (`scroll_window`), а не выводится целиком: иначе prompt_toolkit упирается в
   «Window too small», а Rich каждый кадр гоняет сотни строк.

Если Shell не запущен (headless, не-TTY), вызовы прозрачно падают обратно на
старую синхронную реализацию — она остаётся рабочей для таких случаев.

Публичный «конструктор» виджетов
--------------------------------
Всё, что рисует списки (меню слэш-команд, `commands/menus/*`, опрос, запрос
разрешений), собирается из одних и тех же кирпичей, чтобы стиль не разъезжался:

    row(...)            одна строка списка: курсор, метка, колонки, подсветка
    section(...)        приглушённый заголовок группы (без линий)
    spacer()            пустая строка-разделитель вместо «─────»
    two_column(...)     левое + прижатое вправо (шапки, «имя … значение»)
    key_hints(...)      строка подсказок «↑↓ выбор · enter ок · esc отмена»
    scroll_window(...)  окно прокрутки под бюджет строк
    more_note(...)      отметка «↑ ещё 12» над/под окном
    paint / fg / bg / role_fg / role_bg      цвета темы в ANSI
    cell_width / pad / clip / strip_ansi     ширина с учётом ANSI и эмодзи
    is_divider / strip_rules                 отлов старых «────» разделителей
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from wcwidth import wcswidth

from config.i18n import t as tr
from config.themes import ansi_24bit, t
from ui.shell import Overlay, get_shell

logger = logging.getLogger(__name__)

RESET = "\x1b[0m"
DIM = "\x1b[2m"
BOLD = "\x1b[1m"


def white_fg() -> str:
    """ANSI SGR для основного текста темы (роль fg_primary), 24-bit.

    Функция, а не константа: тема переключается на ходу, и строку надо
    пересобирать каждый кадр.
    """
    return f"\x1b[{ansi_24bit(t('fg_primary'))}m"

#: Курсор строки — тот же символ, что у поля ввода, чтобы «где я» читалось сразу.
CURSOR = "❯"
INDENT = 2

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_RESET_RE = re.compile(r"\x1b\[0?m")
#: Символы, из которых состояли старые разделители и рамки. Внутри виджета их
#: быть не должно — заказчик просил убрать все линии, кроме рамки ввода.
_RULE_CHARS = set("─━═╌╍┄┅┈┉—–-_=│┃║╎╏┆┇┊┋┌┐└┘├┤┬┴┼╭╮╰╯╔╗╚╝╠╣╦╩╬▁▔")


# ────────────────────────────── цвет и ширина ───────────────────────────────
def fg(hex_color: str) -> str:
    """ANSI-код цвета текста из HEX (#rrggbb)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return ""
    try:
        return f"\x1b[38;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m"
    except ValueError:
        return ""


def bg(hex_color: str) -> str:
    """ANSI-код цвета фона из HEX (#rrggbb)."""
    h = hex_color.lstrip("#")
    if len(h) != 6:
        return ""
    try:
        return f"\x1b[48;2;{int(h[0:2], 16)};{int(h[2:4], 16)};{int(h[4:6], 16)}m"
    except ValueError:
        return ""


def role_fg(role: str) -> str:
    """Цвет текста по семантической роли темы (accent, success, error…)."""
    return fg(t(role))


def role_bg(role: str) -> str:
    return bg(t(role))


def paint(text: str, role: str = "", *, bold: bool = False, dim: bool = False) -> str:
    """Покрасить кусок текста в цвет роли темы и закрыть стиль."""
    if not text:
        return ""
    prefix = (BOLD if bold else "") + (DIM if dim else "") + (role_fg(role) if role else "")
    return f"{prefix}{text}{RESET}" if prefix else text


def strip_ansi(s: str) -> str:
    return _ANSI_RE.sub("", s)


def cell_width(s: str) -> int:
    """Ширина в ячейках терминала: без ANSI, с учётом двухклеточных эмодзи."""
    plain = strip_ansi(s)
    n = wcswidth(plain)
    return n if n >= 0 else len(plain)


def pad(s: str, width: int, align: str = "left") -> str:
    """Добить строку пробелами до нужной ширины (замена колонок таблицы)."""
    gap = width - cell_width(s)
    if gap <= 0:
        return s
    if align == "right":
        return " " * gap + s
    if align == "center":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


def clip(s: str, width: int, tail: str = "…") -> str:
    """Обрезать по видимой ширине, сохранив ANSI-коды (они ширины не занимают)."""
    if width <= 0:
        return ""
    if cell_width(s) <= width:
        return s
    limit = max(0, width - cell_width(tail))
    out: list[str] = []
    used = 0
    i = 0
    while i < len(s):
        m = _ANSI_RE.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        ch = s[i]
        w = wcswidth(ch)
        w = 1 if w < 0 else w
        if used + w > limit:
            break
        out.append(ch)
        used += w
        i += 1
    return "".join(out) + tail + RESET


def _clip_left(s: str, width: int, head: str = "…") -> str:
    """Обрезать начало строки, оставив ближайший к каретке хвост."""
    if width <= 0:
        return ""
    if cell_width(s) <= width:
        return s
    limit = max(0, width - cell_width(head))
    out: list[str] = []
    used = 0
    for ch in reversed(s):
        w = wcswidth(ch)
        w = 1 if w < 0 else w
        if used + w > limit:
            break
        out.append(ch)
        used += w
    return head + "".join(reversed(out))


def _text_around_cursor(text: str, cursor: int, width: int) -> tuple[str, str]:
    """Вернуть видимые части однострочного текста вокруг каретки."""
    content_width = max(0, width - 1)  # одна ячейка остаётся самой каретке
    before, after = text[:cursor], text[cursor:]
    if cell_width(before) + cell_width(after) <= content_width:
        return before, after

    # При длинной строке держим каретку на экране и оставляем немного
    # контекста справа. Свободное место автоматически уходит той стороне,
    # которой оно действительно нужно.
    after_width = min(cell_width(after), max(2, content_width // 3))
    before_width = content_width - after_width
    if cell_width(before) < before_width:
        after_width += before_width - cell_width(before)
        before_width = cell_width(before)
    elif cell_width(after) < after_width:
        before_width += after_width - cell_width(after)
        after_width = cell_width(after)
    return _clip_left(before, before_width), clip(after, after_width)


def is_divider(label: str) -> bool:
    """True для старых пунктов-разделителей вида `"─" * 30`.

    Такие пункты приходят из меню, которые ещё не переехали на `spacer()`.
    Рисовать их линией нельзя, а выбирать — тем более: курсор на них не встаёт.
    """
    plain = strip_ansi(label).strip()
    return bool(plain) and all(ch in _RULE_CHARS or ch.isspace() for ch in plain)


def strip_rules(text: str) -> str:
    """Выбросить из готового блока строки, целиком состоящие из линий рамок.

    Страховка для панелей, чьи рендереры живут в других модулях: даже если там
    ещё осталась линия-подчёркивание, в нижнюю зону она не попадёт.
    """
    keep = [ln for ln in text.split("\n") if not is_divider(ln)]
    return "\n".join(keep)


# ───────────────────────────── кирпичи разметки ─────────────────────────────
def spacer() -> str:
    """Пустая строка — единственный допустимый разделитель внутри виджета."""
    return ""


def section(title: str, *, right: str = "", width: int = 0, indent: int = INDENT) -> str:
    """Приглушённый заголовок группы. Ни линий, ни фона — только тон."""
    left = " " * indent + DIM + title + RESET
    if not right:
        return left
    return _join_lr(left, DIM + right + RESET, width)


def two_column(left: str, right: str, *, width: int = 0, indent: int = INDENT) -> str:
    """Строка «слева содержимое, справа отметка» без таблиц и рамок."""
    return _join_lr(" " * indent + left, right, width)


def _join_lr(left: str, right: str, width: int) -> str:
    if not right:
        return left
    if width <= 0:
        return f"{left}  {right}"
    gap = width - 1 - cell_width(left) - cell_width(right)
    return left + " " * gap + right if gap >= 2 else f"{left}  {right}"


def key_hints(*pairs: tuple[str, str]) -> str:
    """«↑↓ выбор · enter ок · esc отмена» — единый формат строки подсказок."""
    return " · ".join(f"{k} {v}".strip() for k, v in pairs if k or v)


def more_note(n: int, *, up: bool = True, indent: int = 4) -> str:
    """Отметка «сколько строк осталось за окном» вместо скроллбара."""
    if n <= 0:
        return ""
    arrow = "↑" if up else "↓"
    return f"{' ' * indent}{DIM}{arrow} {tr('shell.more_lines', n=n)}{RESET}"


def scroll_window(total: int, selected: int, budget: int) -> tuple[int, int, int, int]:
    """Окно видимых строк списка: (start, end, скрыто сверху, скрыто снизу).

    Бюджет — это ВСЕ строки, включая отметки «ещё N», поэтому под них место
    резервируется здесь же: иначе список каждый раз вылезал бы на строку выше
    рамки и prompt_toolkit ронял бы раскладку.
    """
    if total <= 0 or budget <= 0:
        return 0, 0, 0, 0
    if total <= budget:
        return 0, total, 0, 0
    span = max(1, budget - 1)
    start = max(0, min(selected - span // 2, total - span))
    if start > 0 and start + span < total and budget > 2:
        # Видны обе отметки — им нужны две строки, а не одна.
        span = max(1, budget - 2)
        start = max(0, min(selected - span // 2, total - span))
    end = min(total, start + span)
    return start, end, start, total - end


def row(label: str, hint: str = "", *, selected: bool = False, width: int = 0,
        role: str = "", mark: str = "", mark_role: str = "", badge: str = "",
        right: str = "", right_role: str = "", label_width: int = 0,
        hint_width: int = 0, dim_label: bool = False, indent: int = INDENT,
        gap: int = 2) -> str:
    """Одна строка плоского списка — общий кирпич всех виджетов нижней зоны.

    Раскладка: `[отступ][курсор][mark][label →label_width][badge][hint →hint_width][right]`.
    `label_width` и `hint_width` — это и есть замена колонкам таблицы: колонки
    держатся пробелами, поэтому ни одной вертикальной линии на экране не нужно.

    Выделение — фон `bg_select` на всю ширину строки, а не рамка и не отдельные
    подсвеченные куски: подсветка кусками выглядела рваной, потому что пробелы
    между колонками оставались на фоне терминала.
    """
    sel_bg = bg(t("bg_select")) if selected else ""
    parts: list[tuple[str, str, bool, bool]] = []   # текст, роль, bold, dim

    lead = " " * indent + (CURSOR + " " if selected else "  ")
    parts.append((lead, "accent" if selected else "", True, False))
    used = cell_width(lead)

    if mark:
        parts.append((mark + " ", mark_role, False, not mark_role))
        used += cell_width(mark) + 1

    body = pad(label, label_width) if label_width else label
    room = (width - 1 - used) if width else 0
    if width and cell_width(body) > room:
        body = clip(body, max(4, room))
    parts.append((body, role, False, dim_label))
    used += cell_width(body)

    hint_cell = pad(hint, hint_width) if hint_width else hint
    for extra, extra_role, extra_dim in ((badge, "", False), (hint_cell, "", True)):
        if not extra:
            continue
        text = " " * gap + extra
        if width:
            free = width - 1 - used - (cell_width(right) + gap if right else 0)
            if free <= gap:
                continue
            text = clip(text, free)
        parts.append((text, extra_role, False, extra_dim))
        used += cell_width(text)

    if right and (not width or used + gap + cell_width(right) <= width - 1):
        parts.append((" " * gap + right, right_role, False, False))
        used += gap + cell_width(right)

    if selected and width and used < width - 1:
        # Фон тянем до края: строка-подсветка должна читаться как одна полоса.
        parts.append((" " * (width - 1 - used), "", False, False))

    out = []
    for text, part_role, bold, dim in parts:
        if not text:
            continue
        sgr = RESET + sel_bg
        if bold or (selected and not dim):
            sgr += BOLD
        if dim and not selected:
            sgr += DIM
        if part_role:
            sgr += role_fg(part_role)
        elif selected:
            sgr += white_fg()
        # Метки из старых меню приходят уже покрашенными; их внутренний RESET
        # погасил бы фон подсветки на середине строки — возвращаем его обратно.
        if sel_bg and "\x1b[" in text:
            text = _RESET_RE.sub(RESET + sel_bg, text)
        out.append(sgr + text)
    return "".join(out) + RESET


def _nav_hint(allow_back: bool, allow_forward: bool) -> str:
    if allow_back and allow_forward:
        return " · ←→ steps"
    if allow_back:
        return " · ← step"
    if allow_forward:
        return " · → step"
    return ""


def _budget(shell, reserve: int = 0, fallback: int = 12) -> int:
    """Сколько строк остаётся под список после «шапки» виджета."""
    try:
        total = shell.overlay_budget() if shell is not None else fallback
    except Exception:
        logger.debug("overlay_budget failed", exc_info=True)
        total = fallback
    return max(1, total - reserve)


def title_lines(title: str, width: int = 0) -> list[str]:
    """Заголовок виджета: приглушённый текст, многострочный — как передали."""
    if not title:
        return []
    out = []
    for line in title.split("\n"):
        if not line.strip():
            out.append("")
        elif "\x1b" in line:
            out.append(f"  {line}")           # заголовок уже покрашен вызывающим
        else:
            out.append(f"  {DIM}{clip(line, width - 3) if width else line}{RESET}")
    return out


# ─────────────────────────── список с выбором ───────────────────────────────
class SelectOverlay(Overlay):
    """Замена `select_menu`: плоский список без рамок, с прокруткой под бюджет.

    Поддерживаемые ключи пункта: `label`, `hint`, `active`, а также новые
    `role` (цвет строки по роли темы), `mark` (глиф слева), `badge` (колонка
    после метки) и `separator` (пустая строка, курсор её пропускает).
    """

    def __init__(self, items: list[dict], current: int = 0, title: str = "",
                 allow_back: bool = False, allow_forward: bool = False) -> None:
        super().__init__()
        self.items = items
        self.selected = max(0, min(current, len(items) - 1)) if items else 0
        self.title = title
        self.allow_back = allow_back
        self.allow_forward = allow_forward
        self._col_cache: tuple[tuple[int, int], tuple[int, int]] | None = None
        if self._is_skipped(self.selected):
            self._step(1)

    # ── разделители ──
    def _is_skipped(self, idx: int) -> bool:
        """Некликабельные строки: пустой разделитель, заголовок секции, старое «────».

        `skip` понимаем наравне с `separator`: так помечают заголовки групп
        подклассы из `commands/menus/*`, и курсор не должен на них садиться.
        """
        if not (0 <= idx < len(self.items)):
            return False
        item = self.items[idx]
        return (bool(item.get("separator")) or bool(item.get("skip"))
                or is_divider(str(item.get("label", ""))))

    def _step(self, delta: int) -> None:
        """Сдвиг курсора мимо разделителей (по кругу, как было раньше)."""
        total = len(self.items)
        idx = self.selected
        for _ in range(total):
            idx = (idx + delta) % total
            if not self._is_skipped(idx):
                self.selected = idx
                return

    def _columns(self, width: int) -> tuple[int, int]:
        """Ширины колонок «метка» и «подсказка» — по всему списку, а не по окну.

        Считать по видимым строкам нельзя: колонка прыгала бы при прокрутке.
        Результат кэшируется: рендер зоны дёргается дважды за кадр при тикере
        10 fps, и мерить ширину каждой метки по двадцать раз в секунду незачем.
        """
        key = (width, len(self.items))
        if self._col_cache is not None and self._col_cache[0] == key:
            return self._col_cache[1]
        labels, hints = [0], [0]
        for i, item in enumerate(self.items):
            if self._is_skipped(i):
                continue
            labels.append(cell_width(str(item.get("label", ""))))
            hints.append(cell_width(str(item.get("hint", ""))))
        label_w = min(max(labels), max(12, int(width * 0.45)))
        # Подсказки выравниваем в колонку только когда справа есть чему
        # выравниваться (маркер «◄» активного пункта).
        hint_w = 0
        if any(item.get("active") for item in self.items):
            hint_w = min(max(hints), max(0, width - label_w - 16))
        self._col_cache = (key, (label_w, hint_w))
        return label_w, hint_w

    def render(self, width: int) -> str:
        head = title_lines(self.title, width)
        if head:
            head.append(spacer())
        budget = _budget(self.shell, reserve=len(head))
        start, end, above, below = scroll_window(len(self.items), self.selected, budget)
        label_w, hint_w = self._columns(width)
        marked = any(item.get("mark") for item in self.items)

        lines = list(head)
        if above:
            lines.append(more_note(above, up=True))
        for i in range(start, end):
            item = self.items[i]
            if self._is_skipped(i):
                lines.append(spacer())
                continue
            # Пустая марка-заглушка там, где у соседей есть глиф: иначе колонка
            # меток разъезжается на две ячейки.
            mark = str(item.get("mark", "")) or (" " if marked else "")
            lines.append(row(
                str(item.get("label", "")),
                str(item.get("hint", "")),
                selected=(i == self.selected),
                width=width,
                role=str(item.get("role", "")),
                mark=mark,
                mark_role=str(item.get("mark_role", "")),
                badge=str(item.get("badge", "")),
                right="◄" if item.get("active", False) else "",
                right_role="success",
                label_width=label_w,
                hint_width=hint_w,
            ))
        if below:
            lines.append(more_note(below, up=False))
        return "\n".join(lines)

    def hint(self) -> str:
        return (f"↑↓ select · enter confirm{_nav_hint(self.allow_back, self.allow_forward)}"
                " · esc cancel")

    def version(self):
        """Список меняется только при смене курсора или набора пунктов."""
        return (self.selected, len(self.items))

    def handle_key(self, key: str, event) -> bool:
        total = len(self.items)
        if total == 0:
            if key in ("escape", "c-c", "enter"):
                self.finish(None)
            return True
        if key in ("up", "k"):
            self._step(-1)
        elif key in ("down", "j"):
            self._step(1)
        elif key == "pageup":
            for _ in range(5):
                self._step(-1)
        elif key == "pagedown":
            for _ in range(5):
                self._step(1)
        elif key == "enter":
            self.finish(self.selected)
        elif key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
        elif key == "left" and self.allow_back:
            self.finish(-(self.selected + 2))
        elif key == "right" and self.allow_forward:
            self.finish(self.selected)
        elif len(key) == 1 and key.isdigit():
            n = int(key)
            if 1 <= n <= total and not self._is_skipped(n - 1):
                self.finish(n - 1)
        return True


# ───────────────────────── панель с внешним рендером ────────────────────────
class PanelOverlay(Overlay):
    """Замена `_panel_menu_direct`.

    `render_fn(selected) -> str` и `on_key(key, selected) -> (handled, sel, total)`
    работают ровно как раньше, поэтому все существующие рендереры панелей
    (сессии, модели, темы, провайдеры) переносятся без переписывания.
    """

    def __init__(self, render_fn: Callable[[int], str], hint_text: str, total: int,
                 initial_selected: int = 0,
                 on_key: Callable[[str, int], tuple[bool, int, int] | None] | None = None,
                 text_input: bool = False, allow_back: bool = False,
                 allow_forward: bool = False) -> None:
        super().__init__()
        self.render_fn = render_fn
        self.hint_text = hint_text
        self.total = total
        self.selected = initial_selected
        self.on_key = on_key
        self.wants_text = text_input
        self.allow_back = allow_back
        self.allow_forward = allow_forward
        self._revision = 0

    def version(self):
        """Версия внешней панели для долгоживущего кэша Shell.

        `/models` может содержать сотни строк. Без версии Shell был вынужден
        вызывать внешний render_fn на каждом кадре стрима агента, даже когда в
        самой панели ничего не происходило.
        """
        return (self.selected, self.total, self.text, self._revision)

    def render(self, width: int) -> str:
        try:
            out = self.render_fn(self.selected)
        except Exception:
            logger.warning("panel render_fn failed", exc_info=True)
            return ""
        # Рендерер живёт в чужом модуле; если там осталась линия-подчёркивание,
        # до экрана она не доедет — политика «внутри виджета линий нет».
        return strip_rules(out)

    def hint(self) -> str:
        return self.hint_text + _nav_hint(self.allow_back, self.allow_forward)

    def _dispatch_custom(self, key: str) -> bool:
        if self.on_key is None:
            return False
        try:
            res = self.on_key(key, self.selected)
        except Exception:
            logger.warning("panel on_key failed", exc_info=True)
            return True
        if res is None:
            return True
        handled, sel, total = res
        self._revision += 1
        if not handled:
            self.finish(None)
            return True
        self.selected, self.total = sel, total
        return True

    def handle_key(self, key: str, event) -> bool:
        if key == "up":
            if self.total > 0:
                self.selected = (self.selected - 1) % self.total
            return True
        if key == "down":
            if self.total > 0:
                self.selected = (self.selected + 1) % self.total
            return True
        if key == "enter":
            if self.total > 0:
                self.finish(self.selected)
            return True
        if key == "c-c":
            self.finish(None)
            return True
        if key == "left" and self.allow_back:
            self.finish(-(self.selected + 2))
            return True
        if key == "right" and self.allow_forward:
            self.finish(self.selected)
            return True
        # Всё прочее — сначала пользовательскому обработчику, затем поведение
        # по умолчанию (esc закрывает, печатные символы идут в фильтр).
        if self.on_key is not None:
            return self._dispatch_custom(key)
        if key == "escape":
            self.finish(None)
            return True
        if self.wants_text:
            if self.edit_text_key(key):
                return True
            if len(key) == 1 and key.isprintable():
                self.shell.overlay_buffer.insert_text(key)
            return True
        return True

    def on_text_changed(self, text: str) -> None:
        self.selected = 0
        self._revision += 1


# ────────────────────────────── поле ввода ──────────────────────────────────
class TextOverlay(Overlay):
    """Свободный ввод внутри нижней зоны.

    Заменяет `console.input()` в меню: тот читал stdin в cooked-режиме, что
    несовместимо с постоянным Application — терминалом владеет он.
    """

    wants_text = True

    def __init__(self, label: str, default: str = "", password: bool = False,
                 validate: Callable[[str], str | None] | None = None) -> None:
        super().__init__()
        self.label = label
        self.default = default
        self.password = password
        self.validate = validate
        self.error: str | None = None

    def render(self, width: int) -> str:
        shown = self.text
        if self.password:
            # Маска обязательна: ключ API не должен появиться на экране ни разу.
            shown = "•" * len(shown)
        cursor = self.shell.overlay_buffer.cursor_position if self.shell else len(shown)
        field_width = max(8, width - cell_width(self.label) - 8)
        before, after = _text_around_cursor(shown, cursor, field_width)
        line = (paint(f"  {CURSOR} ", "accent", bold=True)
                + paint(self.label, "accent", bold=True) + " ")
        line += before + paint("▌", "accent") + after
        out = [line]
        if self.default and not self.text:
            out.append(f"    {DIM}{tr('menu.default_hint', value=self.default)}{RESET}")
        if self.error:
            out.append("    " + paint(f"✗ {self.error}", "error"))
        return "\n".join(out)

    def hint(self) -> str:
        return tr("menu.hint_input")

    def version(self):
        cursor = self.shell.overlay_buffer.cursor_position if self.shell else 0
        return (self.text, cursor, self.error, self.default, self.password)

    def on_text_changed(self, text: str) -> None:
        self.error = None

    def handle_key(self, key: str, event) -> bool:
        if key == "enter":
            value = self.text.strip() or self.default
            if self.validate is not None:
                err = self.validate(value)
                if err:
                    self.error = err
                    return True
            self.finish(value)
            return True
        if key in ("escape", "c-c"):
            self.finish(None)
            return True
        if self.edit_text_key(key):
            self.error = None
            return True
        if len(key) == 1 and key.isprintable():
            self.shell.overlay_buffer.insert_text(key)
            self.error = None
            return True
        return True


# ──────────────────────── публичные async-обёртки ───────────────────────────
async def select_menu(items: list[dict], current: int = 0, title: str = "",
                      allow_back: bool = False,
                      allow_forward: bool = False) -> int | None:
    """Протокол возврата тот же, что у прежней синхронной версии."""
    if not items:
        return None
    shell = get_shell()
    if shell is None:
        from ui.menu import select_menu as legacy
        return legacy(items, current, title, allow_back, allow_forward)
    return await shell.run_overlay(
        SelectOverlay(items, current, title, allow_back, allow_forward))


async def panel_menu(render_fn, hint_text: str, total: int, initial_selected: int = 0,
                     on_key=None, text_input: bool = False, allow_back: bool = False,
                     allow_forward: bool = False) -> int | None:
    shell = get_shell()
    if shell is None:
        import sys

        from ui.menu import _panel_menu_direct as legacy
        return legacy(render_fn, sys.stdout, hint_text, total, initial_selected,
                      on_key, text_input, allow_back, allow_forward)
    return await shell.run_overlay(
        PanelOverlay(render_fn, hint_text, total, initial_selected, on_key,
                     text_input, allow_back, allow_forward))


async def ask_text(label: str, default: str = "", password: bool = False,
                   validate: Callable[[str], str | None] | None = None) -> str | None:
    """Свободный ввод. Возвращает строку либо None при отмене."""
    shell = get_shell()
    if shell is None:
        from rich.console import Console
        try:
            return Console().input(f"  {label} ").strip() or default
        except (EOFError, KeyboardInterrupt):
            return None
    return await shell.run_overlay(TextOverlay(label, default, password, validate))


async def confirm(question: str, yes_label: str | None = None,
                  no_label: str | None = None, danger: bool = False) -> bool:
    """Подтверждение да/нет поверх нижней зоны.

    Подписи по умолчанию берутся из i18n в момент вызова, а не в момент импорта:
    язык переключается на ходу (`/lang`), и захешированный в дефолте аргумента
    перевод остался бы от старого языка до перезапуска.
    """
    items = [{"label": yes_label or tr("common.yes")},
             {"label": no_label or tr("common.no")}]
    if danger:
        # Опасное действие: «да» красное, курсор стоит на «нет».
        items[0]["role"] = "error"
        items[1]["role"] = "success"
    res = await select_menu(items, current=1 if danger else 0, title=question)
    return res == 0


__all__ = [
    "BOLD",
    "CURSOR",
    "DIM",
    "INDENT",
    "RESET",
    "white_fg",
    "PanelOverlay",
    "SelectOverlay",
    "TextOverlay",
    "ask_text",
    "bg",
    "cell_width",
    "clip",
    "confirm",
    "fg",
    "is_divider",
    "key_hints",
    "more_note",
    "pad",
    "paint",
    "panel_menu",
    "role_bg",
    "role_fg",
    "row",
    "scroll_window",
    "section",
    "select_menu",
    "spacer",
    "strip_ansi",
    "strip_rules",
    "title_lines",
    "two_column",
]
