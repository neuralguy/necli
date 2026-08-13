"""Общий вид меню: карточка-шапка внутри виджета и плоские списки без рамок.

Три правила, ради которых модуль существует.

1. **Ни одной вертикальной линии.** Заказчик просил убрать рамки во всех
   интерактивных инструментах. Строки, заголовки, подсветку и окно прокрутки
   рисуют общие кирпичи из `ui/overlays.py` (`row`, `section`, `two_column`,
   `scroll_window`, `more_note`) — свой параллельный стиль тут не заводится.

2. **Шапка и события живут в динамике.** Раньше карточка провайдера/навыка/
   сервера печаталась через `print_static` на каждом витке цикла меню, и
   scrollback забивался её копиями. Теперь шапка живёт внутри виджета, а короткие
   события «сохранено» / «удалено» / «ошибка» идут в динамическое notice,
   не в статический scrollback.

3. **Виджет рисует только видимое.** `SelectOverlay` уже режет список по
   бюджету Shell; `CardMenu` вычитает из бюджета шапку и превью.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Sequence

from config.i18n import t as tr
from config.themes import t
from ui import overlays
from ui.menu import Palette, cell, columns, overlay_rows, row_avail, search_line
from ui.overlays import (
    DIM,
    RESET,
    cell_width,
    clip,
    fg,
    more_note,
    paint,
    row,
    scroll_window,
    section,
    spacer,
    two_column,
)
from ui.shell import get_shell

__all__ = [
    "CardMenu",
    "card_menu",
    "confirm_delete",
    "facts_line",
    "with_spinner",
]

#: Кадры как у rich-спиннера "dots" — вид прежний, но крутит их ticker Shell.
_SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def facts_line(*parts: str) -> str:
    """Склеивает непустые куски строки-факта одним разделителем."""
    return " · ".join(p for p in parts if p)


async def with_spinner(label: str, fn, *args):
    """Гоняет блокирующий fn в потоке, показывая спиннер в динамической зоне.

    Замена console.status(): тот держит rich.live.Live, а Live лезет курсором в
    терминал, которым владеет Application. Плюс поток не морозит event loop,
    поэтому спиннер реально крутится, пока идёт запрос.
    """
    from rich.text import Text

    sh = get_shell()
    if sh is None:
        return fn(*args)

    def frame() -> Text:
        i = int(time.monotonic() * 10) % len(_SPIN)
        return Text(f"  {_SPIN[i]} {label}", style=t("info"))

    sh.set_dynamic("menu-wait", frame)
    try:
        return await asyncio.to_thread(fn, *args)
    finally:
        sh.clear_dynamic("menu-wait")


async def confirm_delete(question: str) -> bool:
    """Подтверждение удаления. Курсор по умолчанию на «отмене»."""
    return await overlays.confirm(
        question,
        yes_label=tr("common.yes_delete"),
        no_label=tr("common.cancel"),
        danger=True,
    )


class CardMenu(overlays.SelectOverlay):
    """Список действий с шапкой-карточкой, колонками и превью под списком.

    Наследуемся от `SelectOverlay`, а не пишем свой оверлей: оттуда бесплатно
    приходят раскладка клавиш (стрелки, pageup/pagedown, enter, esc, цифры),
    пропуск разделителей и протокол возврата (`int` / `None` / `-(selected+2)`),
    который ломать нельзя.

    Добавлено только то, чего в базовом виджете нет:
      * шапка-карточка (заголовок, статус, строки фактов);
      * произвольные выровненные колонки `cols` и палитра `swatch`;
      * `footer_fn(selected)` — живое превью под списком (темы).
    """

    #: Ключи пункта: label, hint, icon(+icon_style), badge(+badge_style),
    #: cols (list[str], прижаты вправо), swatch (list[hex]), active, action, skip.
    #: action=True выделяет пункт как действие, а не как сущность/настройку.

    wants_text = True

    def __init__(
        self,
        items: list[dict],
        *,
        title: str = "",
        status: str = "",
        status_style: str = "muted",
        facts: Sequence[str] = (),
        current: int = 0,
        hint_text: str | None = None,
        footer_fn: Callable[[int], str] | None = None,
        allow_back: bool = False,
        expand: bool = False,
        multi: bool = False,
        checked: set[int] | None = None,
        split_focus: bool = False,
    ) -> None:
        self._all_items = list(items)
        self._visible_indices = list(range(len(items)))
        super().__init__(list(items), current=current, title="", allow_back=allow_back)
        self.card_title = title
        self.status = status
        self.status_style = status_style
        self.facts = list(facts)
        self.hint_text = hint_text
        self.footer_fn = footer_fn
        self.expand_height = expand
        self.multi = multi
        self.checked: set[int] = set(checked or ())
        self.split_focus = split_focus
        self.search_focused = False
        self.wants_text = not split_focus
        self._widths: dict[int, tuple] = {}
        self._show_search = (
            split_focus
            or sum(not self._is_original_skipped(item) for item in self._all_items) >= 10
        )

    def hint(self) -> str:
        if self.split_focus:
            target = "models" if self.search_focused else "search"
            search = f"tab {target} · "
        else:
            search = "type to search · " if self._show_search or self.text else ""
        if self.multi:
            return (
                f"{search}↑↓ select · space toggle · enter confirm"
                f"{' · esc cancel' if not self.allow_back else ''}"
            )
        return (
            self.hint_text
            if self.hint_text is not None
            else (f"{search}↑↓ select · enter confirm · esc cancel")
        )

    def version(self):
        """Метка для кэша отрисовки Shell.

        Кроме курсора в карточке ничего не анимируется, поэтому на кадрах без
        нажатий рендер можно не повторять вовсе.
        """
        return (self.selected, len(self.items), len(self.checked), self.text, self.search_focused)

    def _original_index(self, visible_index: int | None = None) -> int:
        pos = self.selected if visible_index is None else visible_index
        if 0 <= pos < len(self._visible_indices):
            return self._visible_indices[pos]
        return 0

    @staticmethod
    def _haystack(item: dict) -> str:
        values = [
            item.get("label", ""),
            item.get("hint", ""),
            item.get("badge", ""),
            item.get("search", ""),
            item.get("haystack", ""),
            *(item.get("cols", ()) or ()),
        ]
        return " ".join(str(value) for value in values if value).casefold()

    def on_text_changed(self, text: str) -> None:
        tokens = text.casefold().split()
        if not tokens:
            indices = list(range(len(self._all_items)))
        else:
            indices = [
                index
                for index, item in enumerate(self._all_items)
                if not self._is_original_skipped(item)
                and all(token in self._haystack(item) for token in tokens)
            ]
        self._visible_indices = indices
        self.items = [self._all_items[index] for index in indices]
        self.selected = 0
        self._widths.clear()

    @staticmethod
    def _is_original_skipped(item: dict) -> bool:
        return (
            bool(item.get("separator"))
            or bool(item.get("skip"))
            or overlays.is_divider(str(item.get("label", "")))
        )

    def handle_key(self, key: str, event) -> bool:
        if self.split_focus and key == "tab":
            self.search_focused = not self.search_focused
            self.wants_text = self.search_focused
            return True
        if self.split_focus and self.search_focused and key in ("space", " "):
            if self.shell is not None:
                self.shell.overlay_buffer.insert_text(" ")
            return True
        if self.multi and key in ("space", " "):
            if not self.split_focus and self.text and self.shell is not None:
                self.shell.overlay_buffer.insert_text(" ")
                return True
            if not self._is_skipped(self.selected):
                original = self._original_index()
                if original in self.checked:
                    self.checked.discard(original)
                else:
                    self.checked.add(original)
            return True
        if not self.multi and key in ("space", " "):
            if self.shell is not None:
                self.shell.overlay_buffer.insert_text(" ")
            return True
        if key == "enter":
            if self.items and not self._is_skipped(self.selected):
                self.finish(self._original_index())
            return True
        if key == "escape" and self.text:
            self.text = ""
            return True
        if key == "left" and self.allow_back:
            self.finish(-(self._original_index() + 2))
            return True
        if key == "right" and self.allow_forward:
            self.finish(self._original_index())
            return True
        if key in (
            "backspace",
            "delete",
            "c-delete",
            "c-w",
            "c-u",
            "c-k",
            "left",
            "right",
            "home",
            "end",
            "c-left",
            "c-right",
            "c-a",
            "c-e",
        ):
            return self.edit_text_key(key)
        return super().handle_key(key, event)

    # ── ширины колонок: раз на ширину экрана, а не на кадр ──
    def _layout(self, width: int) -> tuple:
        cached = self._widths.get(width)
        if cached is not None:
            return cached
        live = [it for i, it in enumerate(self.items) if not self._is_skipped(i)]
        icon_w = (
            3 if self.multi else max((cell_width(it.get("icon", "")) for it in live), default=0)
        )
        label_w = max((cell_width(it.get("label", "")) for it in live), default=0)
        badge_w = max((cell_width(it.get("badge", "")) for it in live), default=0)
        swatch_w = max((len(it.get("swatch", ())) * 2 for it in live), default=0)
        n_cols = max((len(it.get("cols", ())) for it in live), default=0)
        col_w = [
            max(
                (cell_width(it.get("cols", ())[i]) for it in live if len(it.get("cols", ())) > i),
                default=0,
            )
            for i in range(n_cols)
        ]
        # Ширину глифа считаем настоящую: 🔒 занимает две ячейки, и «одна
        # ячейка на иконку» съедала последний символ правой колонки.
        free = row_avail(width, "x" * icon_w if icon_w else "")
        # Маркер «◄» активного пункта рисует overlays.row справа — если не
        # оставить ему места, он молча пропадёт, и «текущий» станет невидимым.
        active_w = 3 if any(it.get("active") for it in live) else 0
        fixed = (
            sum(w + 2 for w in col_w if w)
            + (badge_w + 2 if badge_w else 0)
            + (swatch_w + 2 if swatch_w else 0)
            + active_w
        )
        # Подсказка отдаёт место первой: без неё пункт читается, без метки — нет.
        label_cap = max(24, int(free * 0.5))
        hint_w = max(0, free - fixed - min(label_w, label_cap) - 2)
        if hint_w < 8:
            hint_w = 0
        label_w = min(max(label_w, 1), max(8, free - fixed - (hint_w + 2 if hint_w else 0)))
        res = (icon_w, label_w, hint_w, badge_w, swatch_w, col_w)
        self._widths[width] = res
        return res

    def _head(self, width: int, pal: Palette, counter: str, facts: Sequence[str]) -> list[str]:
        lines: list[str] = []
        if self.card_title:
            if self.status:
                colour = getattr(pal, self.status_style, pal.muted)
                right = f"{colour}{clip(self.status, 28)}{RESET}"
                if counter:
                    right += f"  {DIM}{counter}{RESET}"
                lines.append(
                    two_column(
                        paint(clip(self.card_title, max(8, width - 34)), "accent", bold=True),
                        right,
                        width=width,
                    )
                )
            else:
                lines.append(
                    two_column(
                        paint(clip(self.card_title, max(8, width - 16)), "accent", bold=True),
                        f"{DIM}{counter}{RESET}" if counter else "",
                        width=width,
                    )
                )
        lines.extend(f"  {DIM}{clip(fact, max(4, width - 5))}{RESET}" for fact in facts)
        if lines:
            lines.append(spacer())
        return lines

    def render(self, width: int) -> str:
        pal = Palette()
        total = len(self.items)
        budget = max(4, overlay_rows())
        footer = self.footer_fn(self._original_index()) if self.footer_fn and self.items else ""
        footer_lines = footer.rstrip("\n").split("\n") if footer else []

        # Порядок жертв при нехватке высоты: список → факты шапки → превью.
        # Список скроллится, поэтому первым ужимается он; превью (темы) режем
        # только если не помещается даже минимум строк списка.
        facts = self.facts
        search_visible = self._show_search or bool(self.text)
        head_len = len(self._head(width, pal, "", facts)) + int(search_visible)
        min_rows = min(total, 3)
        rows_room = budget - head_len - len(footer_lines)
        if rows_room < min_rows:
            keep = budget - head_len - min_rows
            if keep < 2:
                footer_lines = []
                rows_room = budget - head_len
            else:
                footer_lines = footer_lines[:keep]
                rows_room = min_rows
        if rows_room < min(total, 5) and facts:
            # Ужимаем факты шапки — но футеру не в счёт: место возвращаем списку.
            facts = facts[: max(0, len(facts) - (min(total, 5) - rows_room))]
            head_len = len(self._head(width, pal, "", facts)) + int(search_visible)
            rows_room = budget - head_len - len(footer_lines)
        rows_room = max(1, rows_room)

        start, end, above, below = scroll_window(total, self.selected, rows_room)
        # Считаем только выбираемые пункты: заголовки секций в «3/21» не место.
        pickable = sum(1 for i in range(total) if not self._is_skipped(i))
        position = sum(1 for i in range(self.selected + 1) if not self._is_skipped(i))
        counter = f"{position}/{pickable}" if pickable >= 8 or above or below else ""
        lines = self._head(width, pal, counter, facts)
        if search_visible:
            lines.append(
                search_line(
                    self.text,
                    width,
                    "type to search",
                    pal,
                    focused=not self.split_focus or self.search_focused,
                )
            )

        if not self.items:
            lines.append(f"  {pal.dim}{tr('common.no_data')}{pal.reset}")
            lines.extend(footer_lines)
            return "\n".join(lines)

        icon_w, label_w, hint_w, badge_w, swatch_w, col_w = self._layout(width)
        if above:
            lines.append(more_note(above, up=True))
        for i in range(start, end):
            item = self.items[i]
            if self._is_skipped(i):
                label = str(item.get("label", ""))
                lines.append(
                    section(clip(label, width - 5), width=width)
                    if label and not overlays.is_divider(label)
                    else spacer()
                )
                continue
            selected = i == self.selected and not (self.split_focus and self.search_focused)
            if self.multi:
                checked = self._original_index(i) in self.checked
                mark = "[x]" if checked else "[ ]"
                mark_role = "success" if checked else ""
            else:
                mark = str(item.get("icon", "")) or (" " if icon_w else "")
                mark_role = str(item.get("icon_style", ""))
            label_style = getattr(pal, item.get("role", ""), "")
            if item.get("action") and not item.get("role"):
                label_style = pal.accent + pal.bold
            cells: list[tuple[str, str]] = [(cell(item.get("label", ""), label_w), label_style)]
            if hint_w:
                cells.append(("  " + cell(item.get("hint", ""), hint_w), DIM))
            if swatch_w:
                # Палитра темы: по ячейке на роль, каждая своим цветом. На
                # выделенной строке цвета гаснут — зато палитра целиком видна
                # в превью под списком.
                swatch = item.get("swatch", ())
                cells.append(("  ", ""))
                cells.extend(("█ ", fg(colour)) for colour in swatch)
                cells.append((" " * (swatch_w - len(swatch) * 2), ""))
            for n, cw in enumerate(col_w):
                if not cw:
                    continue
                values = item.get("cols", ())
                cells.append(("  " + cell(values[n] if len(values) > n else "", cw, "right"), DIM))
            if badge_w:
                cells.append(
                    (
                        "  " + cell(item.get("badge", ""), badge_w, "right"),
                        getattr(pal, item.get("badge_style", "dim"), DIM),
                    )
                )
            lines.append(
                row(
                    columns(cells, plain=selected),
                    selected=selected,
                    width=width,
                    mark=mark,
                    mark_role=mark_role,
                    right="◄" if item.get("active") else "",
                    right_role="success",
                )
            )
        if below:
            lines.append(more_note(below, up=False))
        lines.extend(footer_lines)
        return "\n".join(lines)


async def card_menu(
    items: list[dict],
    *,
    title: str = "",
    status: str = "",
    status_style: str = "muted",
    facts: Sequence[str] = (),
    current: int = 0,
    hint_text: str | None = None,
    footer_fn: Callable[[int], str] | None = None,
    allow_back: bool = False,
    expand: bool = False,
    multi: bool = False,
    checked: set[int] | None = None,
    split_focus: bool = False,
) -> int | tuple[int | None, set[int]] | None:
    """Показать меню-карточку. Протокол возврата — как у `select_menu`.

    `expand=True` разрешает оверлею занять всю свободную высоту экрана
    вместо нижней половины — для меню с большим живым превью (/themes).
    `multi=True` включает чекбоксы: пробел отмечает пункты, возвращается
    кортеж `(выбор, set(отмеченные индексы))`.
    """
    if not items:
        return (None, set()) if multi else None
    shell = get_shell()
    if shell is None:
        # Headless / до старта Application: рисовать некому, отдаём тот же
        # синхронный список, на который откатывается и сам overlays.
        from ui.menu import select_menu as legacy

        choice = legacy(items, current, title)
        return (choice, set()) if multi else choice
    overlay = CardMenu(
        items,
        title=title,
        status=status,
        status_style=status_style,
        facts=facts,
        current=current,
        hint_text=hint_text,
        footer_fn=footer_fn,
        allow_back=allow_back,
        expand=expand,
        multi=multi,
        checked=checked,
        split_focus=split_focus,
    )
    choice = await shell.run_overlay(overlay)
    if multi:
        return choice, overlay.checked
    return choice
