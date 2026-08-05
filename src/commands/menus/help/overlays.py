"""Оверлеи справки (уровни 1 и 2) и точка входа help_interactive."""

from __future__ import annotations

from commands.menus.help.content import SECTIONS
from commands.menus.help.models import HSection
from commands.menus.help.render import render_two_columns
from ui.overlays import key_hints, paint, row, scroll_window, spacer, two_column
from ui.shell import Overlay, get_shell


class HelpSectionsOverlay(Overlay):
    """Уровень 1: выбор раздела справки."""

    def __init__(self) -> None:
        super().__init__()
        self.selected = 0
        self.sections = SECTIONS

    def render(self, width: int) -> str:
        head = [
            paint("📖 Справка necli", "accent", bold=True),
            paint("Многоуровневая интерактивная справка. "
                  "Выберите раздел.", "dim_text"),
            spacer(),
        ]

        budget = max(3, self._budget() - len(head))
        start, end, above, below = scroll_window(
            len(self.sections), self.selected, budget)

        lines = list(head)
        if above:
            lines.append(paint(f"  ↑ ещё {above}", "dim_text"))

        for i in range(start, end):
            s = self.sections[i]
            sel = (i == self.selected)
            label = f"{s.icon} {s.name}"
            hint = s.desc
            pages_hint = f"{len(s.pages)} стр."
            lines.append(row(
                label, hint,
                selected=sel, width=width,
                badge=pages_hint,
                mark=str(i + 1),
                mark_role="dim_text",
            ))

        if below:
            lines.append(paint(f"  ↓ ещё {below}", "dim_text"))

        return "\n".join(lines)

    def hint(self) -> str:
        # Подсказки клавиш живут ТОЛЬКО здесь (Shell рисует их под нижней
        # линией рамки). Раньше они дублировались ещё и футером внутри тела
        # оверлея — на экране одни и те же клавиши появлялись дважды.
        return key_hints(
            ("↑↓", "выбор"), ("Enter", "открыть"), ("Esc", "выход"),
            ("1-9", "быстрый выбор"))

    def version(self):
        return (self.selected, len(self.sections))

    def _budget(self) -> int:
        try:
            return self.shell.overlay_budget() if self.shell else 12
        except Exception:
            return 12

    def handle_key(self, key: str, event) -> bool:
        total = len(self.sections)
        if key in ("up", "k"):
            self.selected = (self.selected - 1) % total
        elif key in ("down", "j"):
            self.selected = (self.selected + 1) % total
        elif key == "enter":
            self.finish(self.selected)
        elif key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
        elif len(key) == 1 and key.isdigit():
            n = int(key)
            if 1 <= n <= total:
                self.finish(n - 1)
        return True


class HelpPagesOverlay(Overlay):
    """Уровень 2: страницы раздела с двухколоночной раскладкой."""

    expand_height = True

    def __init__(self, section: HSection) -> None:
        super().__init__()
        self.section = section
        self.page = 0
        self._cache_key: tuple | None = None
        self._cache_text: str = ""

    @property
    def total_pages(self) -> int:
        return len(self.section.pages)

    def render(self, width: int) -> str:
        page = self.section.pages[self.page]
        budget = self._budget()

        # Кэш
        key = (self.page, width, budget)
        if self._cache_key == key:
            return self._cache_text

        # Заголовок: раздел — страница — номер
        head = two_column(
            paint(f"{self.section.icon} {self.section.name} — {page.title}",
                  "accent", bold=True),
            paint(f"Стр. {self.page + 1}/{self.total_pages}", "dim_text"),
            width=width,
        )

        # Тело: двухколоночная раскладка
        body_height = max(1, budget - 3)  # заголовок + пустая + индикатор
        body = render_two_columns(
            page.left, page.right, width, body_height, paired=page.paired)

        # Индикатор страниц
        dots = []
        for i in range(self.total_pages):
            if i == self.page:
                dots.append(paint("●", "accent"))
            else:
                dots.append(paint("○", "dim_text"))
        indicator = "  " + " ".join(dots)

        result = "\n".join([head, spacer(), body, spacer(), indicator])
        self._cache_key = key
        self._cache_text = result
        return result

    def hint(self) -> str:
        return key_hints(
            ("←→", "страницы"), ("Esc", "назад"),
            ("Home", "первая"), ("End", "последняя"))

    def version(self):
        return (self.page, self.section.name)

    def _budget(self) -> int:
        try:
            return self.shell.overlay_budget() if self.shell else 16
        except Exception:
            return 16

    def handle_key(self, key: str, event) -> bool:
        if key in ("escape", "c-c", "q", "Q"):
            self.finish(None)
        elif key == "left":
            self.page = (self.page - 1) % self.total_pages
        elif key == "right":
            self.page = (self.page + 1) % self.total_pages
        elif key == "home":
            self.page = 0
        elif key == "end":
            self.page = self.total_pages - 1
        elif key in ("up", "k"):
            self.page = (self.page - 1) % self.total_pages
        elif key in ("down", "j"):
            self.page = (self.page + 1) % self.total_pages
        elif key == "pageup":
            self.page = max(0, self.page - 3)
        elif key == "pagedown":
            self.page = min(self.total_pages - 1, self.page + 3)
        elif len(key) == 1 and key.isdigit():
            n = int(key)
            if 1 <= n <= self.total_pages:
                self.page = n - 1
        return True


async def help_interactive() -> None:
    """Интерактивная многоуровневая справка.

    Уровень 1: список разделов (↑↓, Enter, Esc).
    Уровень 2: страницы раздела (←→, Esc — назад).
    """
    shell = get_shell()
    if shell is None:
        # Headless: печатаем плоский список
        from rich.console import Console
        con = Console()
        con.print("\n[bold]Справка necli[/bold]\n")
        for s in SECTIONS:
            con.print(f"  {s.icon} [bold]{s.name}[/bold] — {s.desc}")
            for p in s.pages:
                con.print(f"      • {p.title}")
        con.print()
        return

    while True:
        # Уровень 1: выбор раздела
        section_idx = await shell.run_overlay(HelpSectionsOverlay())
        if section_idx is None:
            break  # выход

        section = SECTIONS[section_idx]

        # Уровень 2: страницы раздела
        await shell.run_overlay(HelpPagesOverlay(section))
        # Esc из страниц → назад к разделам (цикл продолжается)
