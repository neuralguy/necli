"""Инструмент `poll` — вопросы пользователю посреди хода агента.

Особенность этого виджета в том, что он открывается, когда агент УЖЕ работает.
Раньше он читал клавиши сам (`ui/_keyreader.read_key`) и печатал в stdout с
перемоткой курсора — то есть боролся за терминал с тем, кто в этот момент рисует
ответ. Теперь это обычный оверлей нижней зоны: рисует ту же разметку, но отдаёт
её Shell'у, а клавиши получает от него же.

Разметка — общая с остальными виджетами (`ui/overlays.row/section/spacer`):
никаких рамок, выделение строки фоном, служебные пункты («Свой ответ…»,
«Далее») отбиты пустой строкой и приглушены, длинные списки прокручиваются под
`shell.overlay_budget()`.

Протокол возврата не изменился: одиночный выбор → `str`, множественный →
`list[str]`, отмена → `"(отменено)"` / `["(отменено)"]`, пусто → `"(пропущено)"`.
"""

import asyncio
import logging
import os
import re
import sys

from ui.overlays import (
    DIM,
    RESET,
    key_hints,
    more_note,
    paint,
    row,
    scroll_window,
    spacer,
    two_column,
)
from ui.shell import Overlay, get_shell

logger = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

CANCELLED = "(отменено)"
SKIPPED = "(пропущено)"
CUSTOM_LABEL = "Свой ответ…"
DONE_LABEL = "Далее"


def _visual_line_count(rendered: str, width: int) -> int:
    """Физические строки терминала с учётом переноса длинных логических строк."""
    from rich.cells import cell_len
    total = 0
    for line in rendered.split("\n"):
        cells = cell_len(_ANSI_RE.sub("", line))
        total += max(1, -(-cells // width)) if cells else 1
    return total


def _get_term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


def _accent_ansi() -> str:
    """ANSI SGR-параметры для accent-цвета темы (hex → 24-bit)."""
    from config.themes import ansi_24bit, t
    return ansi_24bit(t("accent"))


def _poll_hint(multiple: bool) -> str:
    if multiple:
        return key_hints(("↑↓", "выбор"), ("space", "отметить"),
                         ("enter", "далее"), ("esc", "отмена"))
    return key_hints(("↑↓", "выбор"), ("enter", "подтвердить"), ("esc", "отмена"))


def _progress(step_info: str) -> str:
    """Прогресс шага «(2/3)» → «●●○ 2/3»: точки видно боковым зрением, цифры точны."""
    m = re.search(r"(\d+)\s*/\s*(\d+)", step_info or "")
    if not m:
        return step_info or ""
    done, total = int(m.group(1)), int(m.group(2))
    if total <= 1 or total > 10:
        return f"{done}/{total}"
    dots = paint("●" * done, "accent") + paint("○" * max(0, total - done), "muted")
    return f"{dots} {DIM}{done}/{total}{RESET}"


def _header(question: str, step_info: str, width: int) -> list[str]:
    """Шапка опроса: знак вопроса акцентом, сам вопрос жирным, прогресс справа."""
    left = paint("?", "accent", bold=True) + " " + paint(question, "accent", bold=True)
    return [two_column(left, _progress(step_info), width=width), spacer()]


def _checkbox(index: int, checked: set[int], enabled: bool) -> tuple[str, str]:
    """(глиф, роль цвета) для чекбокса; у служебных пунктов чекбокса нет."""
    if not enabled:
        return "", ""
    return ("[x]", "success") if index in checked else ("[ ]", "")


def _render_poll(
    question: str,
    options: list[str],
    selected: int,
    step_info: str = "",
    multiple: bool = False,
    checked: set[int] | None = None,
    checkbox_count: int | None = None,
    with_hint: bool = True,
    width: int = 0,
    budget: int = 0,
) -> str:
    """Единый рендер для оверлея и для legacy-пути без Shell."""
    width = width or _get_term_width()
    checked = checked or set()
    n_plain = len(options) if checkbox_count is None else checkbox_count

    lines = _header(question, step_info, width)
    # Служебные пункты («Свой ответ…», «Далее») отбиваем пустой строкой, поэтому
    # они не участвуют в прокрутке — прокручивается только список вариантов.
    service = len(options) - n_plain
    if budget and budget < n_plain + service + 3:
        lines.pop()          # экрана мало — воздух под шапкой убираем первым
    body_budget = (max(1, budget - len(lines) - service - bool(service))
                   if budget else len(options))
    start, end, above, below = scroll_window(n_plain, min(selected, n_plain), body_budget)

    if above:
        lines.append(more_note(above, up=True))
    for i in range(start, end):
        mark, mark_role = _checkbox(i, checked, multiple)
        lines.append(row(options[i], selected=(i == selected), width=width,
                         mark=mark, mark_role=mark_role))
    if below:
        lines.append(more_note(below, up=False))

    if service:
        lines.append(spacer())
    for i in range(n_plain, len(options)):
        label = options[i]
        is_done = label == DONE_LABEL
        hint = ""
        if is_done and multiple:
            hint = f"отмечено: {len(checked)}" if checked else "ничего не отмечено"
        lines.append(row(label, hint, selected=(i == selected), width=width,
                         mark="✓" if is_done else "+",
                         mark_role="success" if (is_done and checked) else "",
                         dim_label=True))

    # В оверлее подсказка живёт под нижней линией рамки (Overlay.hint), поэтому
    # в тело её не дописываем — иначе она продублируется.
    if with_hint:
        lines.append(spacer())
        lines.append(f"  {DIM}{_poll_hint(multiple)}{RESET}")
    return "\n".join(lines)


# ────────────────────────────── оверлей опроса ──────────────────────────────
class PollOverlay(Overlay):
    """Один шаг опроса в нижней зоне.

    Список вариантов мутирует по ходу дела: «Свой ответ…» дописывает в него
    новый пункт, поэтому служебные пункты не хранятся, а достраиваются в
    `all_options` — иначе после каждого дописывания пришлось бы переставлять
    индексы галочек.
    """

    def __init__(self, question: str, options: list[str], step_info: str = "",
                 multiple: bool = False) -> None:
        super().__init__()
        self.question = question
        # Копия: вызывающий код передаёт
        # свой исходный список, и мутация порвала бы его данные.
        self.options = list(options)
        self.step_info = step_info
        self.multiple = multiple
        self.selected = 0
        self.checked: set[int] = set()
        # True, пока поверх нас открыто поле свободного ответа.
        self._asking = False

    @property
    def all_options(self) -> list[str]:
        tail = [CUSTOM_LABEL, DONE_LABEL] if self.multiple else [CUSTOM_LABEL]
        return self.options + tail

    def render(self, width: int) -> str:
        try:
            budget = self.shell.overlay_budget() if self.shell else 0
        except Exception:
            budget = 0
        return _render_poll(
            self.question, self.all_options, self.selected, self.step_info,
            self.multiple, self.checked, len(self.options), with_hint=False,
            width=width, budget=budget,
        )

    def hint(self) -> str:
        return _poll_hint(self.multiple)

    def _cancelled(self):
        return [CANCELLED] if self.multiple else CANCELLED

    def handle_key(self, key: str, event) -> bool:
        # Пока свободный ввод ещё не встал поверх нас, клавиши не должны
        # двигать список: пользователь уже «ушёл» в другой виджет.
        if self._asking:
            return True
        total = len(self.all_options)
        if key in ("up", "k"):
            self.selected = (self.selected - 1) % total
        elif key in ("down", "j"):
            self.selected = (self.selected + 1) % total
        elif key in ("escape", "c-c", "q", "Q"):
            self.finish(self._cancelled())
        elif key in ("enter", "space", " "):
            self._activate(key)
        return True

    def _activate(self, key: str) -> None:
        n = len(self.options)
        if self.multiple and self.selected < n:
            # В множественном режиме и Space, и Enter переключают галочку.
            if self.selected in self.checked:
                self.checked.discard(self.selected)
            else:
                self.checked.add(self.selected)
            return
        if key != "enter":
            return
        if self.selected == n:
            self._ask_custom()
            return
        if self.multiple and self.selected == n + 1:
            answers = [self.options[i] for i in sorted(self.checked)]
            self.finish(answers or [SKIPPED])
            return
        self.finish(self.all_options[self.selected])

    def _ask_custom(self) -> None:
        """Открыть поле свободного ответа поверх опроса.

        Ввод асинхронный, а `handle_key` — нет, поэтому запускаем задачу: сам
        опрос при этом остаётся в стеке оверлеев и вернётся, как только поле
        закроется. Так не мигает нижняя зона и не теряются галочки.
        """
        self._asking = True
        try:
            asyncio.get_running_loop().create_task(self._custom_answer())
        except RuntimeError:
            logger.debug("poll: нет loop'а для свободного ответа", exc_info=True)
            self._asking = False

    async def _custom_answer(self) -> None:
        from ui import overlays
        answer: str | None = None
        try:
            raw_answer = await overlays.ask_text(f"? {self.question}")
            answer = raw_answer.strip() if raw_answer is not None else None
        except Exception:
            logger.warning("poll: свободный ответ не получен", exc_info=True)
        finally:
            self._asking = False
        # Esc закрывает редактор и возвращает к вариантам, а не молча
        # превращает весь одиночный опрос в «пропущено».
        if answer is None:
            self.invalidate()
            return
        if not self.multiple:
            self.finish(answer or SKIPPED)
            return
        if answer:
            self.options.append(answer)
            self.checked.add(len(self.options) - 1)
        # Курсор на «Далее»: дописав вариант, пользователь почти всегда хочет
        # подтвердить, а не искать пункт заново.
        self.selected = len(self.options) + 1
        self.invalidate()


# ─────────────────────────────── публичный API ──────────────────────────────
async def run_poll_step(
    question: str,
    options: list[str],
    step_info: str = "",
    multiple: bool = False,
) -> str | list[str]:
    shell = get_shell()
    if shell is None:
        return _run_poll_step_legacy(question, options, step_info, multiple)
    result = await shell.run_overlay(PollOverlay(question, options, step_info, multiple))
    # None приходит, только если оверлей сняли извне (выход из приложения).
    if result is None:
        return [CANCELLED] if multiple else CANCELLED
    return result


async def run_poll(steps: list[dict]) -> list[dict]:
    steps = steps[:10]
    results = []
    total = len(steps)

    for i, step in enumerate(steps):
        question = step.get("question", "")
        options = step.get("options", [])
        if not question:
            continue

        multiple = bool(
            step.get("multiple")
            or step.get("multi_select")
            or step.get("type") in ("multi", "multiple", "multi-select")
        )
        step_info = f"({i + 1}/{total})" if total > 1 else ""
        answer = await run_poll_step(question, options[:10], step_info, multiple)
        results.append({"question": question, "answer": answer})

    return results


def run_poll_sync(steps: list[dict]) -> list[dict]:
    """Точка входа для реестра инструментов: он вызывает обработчики синхронно.

    Мост живёт в `ui/menu.py` — он один на все виджеты и знает про все три
    ситуации (нет loop'а / рабочий поток executor'а / сам loop).
    """
    from ui.menu import run_ui_sync
    return run_ui_sync(run_poll(steps))


# ───────────────────────────── legacy без Shell ─────────────────────────────
# Работает, когда Application не поднят: headless, не-TTY, ранний старт. Пишет
# в терминал напрямую — это допустимо ровно потому, что владельца терминала в
# такой момент нет.

def _clear_lines(n: int):
    for _ in range(n):
        sys.stdout.write('\033[A\033[2K')
    sys.stdout.write('\r')
    sys.stdout.flush()


def _input_custom_answer(question: str) -> str:
    from rich.console import Console

    from config.themes import t
    console = Console()
    console.print(f"  [bold {t('accent')}]? {question}[/]")
    console.print()
    try:
        sys.stdout.write(f"  \033[1;{_accent_ansi()}m▌ \033[0m")
        sys.stdout.flush()
        answer = input()
        return answer.strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _run_poll_step_legacy(
    question: str,
    options: list[str],
    step_info: str = "",
    multiple: bool = False,
) -> str | list[str]:
    from ui._keyreader import read_key as _read_key

    if not sys.stdin.isatty():
        # Спрашивать некого и нечем: без TTY read_key упал бы на termios.
        return [SKIPPED] if multiple else SKIPPED

    options = list(options)
    all_options = options + ([CUSTOM_LABEL, DONE_LABEL] if multiple else [CUSTOM_LABEL])
    selected = 0
    checked: set[int] = set()

    def draw() -> int:
        rendered = _render_poll(question, all_options, selected, step_info, multiple,
                                checked, len(options))
        sys.stdout.write(rendered + '\n')
        sys.stdout.flush()
        return _visual_line_count(rendered, _get_term_width())

    line_count = draw()

    while True:
        key = _read_key()

        if key == 'up':
            selected = (selected - 1) % len(all_options)
        elif key == 'down':
            selected = (selected + 1) % len(all_options)
        elif multiple and key in (' ', 'enter') and selected < len(options):
            if selected in checked:
                checked.remove(selected)
            else:
                checked.add(selected)
        elif key == 'enter':
            _clear_lines(line_count)
            custom_index = len(options)
            done_index = len(options) + 1
            if selected == custom_index:
                answer = _input_custom_answer(question)
                # Считаем реальное число физических строк, которое напечатал
                # _input_custom_answer: строка вопроса (может переноситься) +
                # пустая строка + строка ввода. Хардкод "3" оставлял артефакты
                # на узких терминалах, когда вопрос переносился.
                _term_w = _get_term_width()
                _q_line = f"  ? {question}"
                _clear_lines(_visual_line_count(_q_line, _term_w) + 2)
                if multiple:
                    if answer:
                        options.append(answer)
                        checked.add(len(options) - 1)
                    all_options = [*options, CUSTOM_LABEL, DONE_LABEL]
                    selected = len(options) + 1
                    line_count = draw()
                    continue
                return answer or SKIPPED
            if multiple and selected == done_index:
                answers = [options[i] for i in sorted(checked)]
                return answers or [SKIPPED]
            return all_options[selected]
        elif key == 'ctrl-c':
            _clear_lines(line_count)
            return [CANCELLED] if multiple else CANCELLED

        _clear_lines(line_count)
        line_count = draw()


__all__ = [
    "CANCELLED", "CUSTOM_LABEL", "DONE_LABEL", "SKIPPED",
    "PollOverlay", "run_poll", "run_poll_step", "run_poll_sync",
]
