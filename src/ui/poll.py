import os
import re
import sys

from ui._keyreader import read_key as _read_key

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


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
    """ANSI SGR-параметры для accent-цвета темы (#rrggbb → 24-bit, fallback 38;5;75)."""
    from config.themes import t
    accent = t("accent")
    if accent.startswith("#") and len(accent) == 7:
        r, g, b = int(accent[1:3], 16), int(accent[3:5], 16), int(accent[5:7], 16)
        return f"38;2;{r};{g};{b}"
    return "38;5;75"

def _render_poll(
    question: str,
    options: list[str],
    selected: int,
    step_info: str = "",
    multiple: bool = False,
    checked: set[int] | None = None,
    checkbox_count: int | None = None,
) -> str:
    lines = []
    accent = _accent_ansi()
    checked = checked or set()

    # Header
    header = f"  \033[1;{accent}m? {question}\033[0m"
    if step_info:
        header += f"  \033[2m{step_info}\033[0m"
    lines.append(header)
    lines.append("")

    for i, opt in enumerate(options):
        marker = "\u25b8" if i == selected else " "
        checkbox = ""
        if multiple and (checkbox_count is None or i < checkbox_count):
            checkbox = "[x] " if i in checked else "[ ] "
        text = f"  {marker} {checkbox}{opt}"
        if i == selected:
            lines.append(f"\033[1;{accent}m{text}\033[0m")
        else:
            lines.append(f"\033[2m{text}\033[0m")

    lines.append("")
    if multiple:
        lines.append("  \033[2m\u2191\u2193 \u2014 \u0432\u044b\u0431\u043e\u0440  Space/Enter \u2014 \u043e\u0442\u043c\u0435\u0442\u0438\u0442\u044c  \u0414\u0430\u043b\u0435\u0435 \u2014 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c\033[0m")
    else:
        lines.append("  \033[2m\u2191\u2193 \u2014 \u0432\u044b\u0431\u043e\u0440  \u23ce \u2014 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c\033[0m")
    return "\n".join(lines)


def _clear_lines(n: int):
    for _ in range(n):
        sys.stdout.write('\033[A\033[2K')
    sys.stdout.write('\r')
    sys.stdout.flush()


def _input_custom_answer(question: str) -> str:
    from rich.console import Console

    from config.themes import t
    console = Console()
    console.print(f"  [bold {t('accent')}]? {question}[/bold {t('accent')}]")
    console.print()
    try:
        sys.stdout.write(f"  \033[1;{_accent_ansi()}m\u25b8 \033[0m")
        sys.stdout.flush()
        answer = input()
        return answer.strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def run_poll_step(
    question: str,
    options: list[str],
    step_info: str = "",
    multiple: bool = False,
) -> str | list[str]:
    # \u041a\u043e\u043f\u0438\u0440\u0443\u0435\u043c \u0432\u0445\u043e\u0434\u043d\u043e\u0439 \u0441\u043f\u0438\u0441\u043e\u043a: \u043d\u0438\u0436\u0435 \u043c\u044b \u043c\u043e\u0436\u0435\u043c \u0434\u043e\u043f\u0438\u0441\u044b\u0432\u0430\u0442\u044c \u0432 \u043d\u0435\u0433\u043e custom-\u043e\u0442\u0432\u0435\u0442\u044b,
    # \u0430 \u0432\u044b\u0437\u044b\u0432\u0430\u044e\u0449\u0438\u0439 \u043a\u043e\u0434 (\u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 tools/ssh._confirm_command) \u043f\u0435\u0440\u0435\u0434\u0430\u0451\u0442 \u0441\u0432\u043e\u0439
    # \u0438\u0441\u0445\u043e\u0434\u043d\u044b\u0439 \u0441\u043f\u0438\u0441\u043e\u043a \u2014 \u043c\u0443\u0442\u0430\u0446\u0438\u044f \u043f\u043e\u0440\u0432\u0430\u043b\u0430 \u0431\u044b \u0435\u0433\u043e \u0434\u0430\u043d\u043d\u044b\u0435 \u0438 \u0438\u043d\u0432\u0430\u0440\u0438\u0430\u043d\u0442\u044b \u0441\u0447\u0451\u0442\u0447\u0438\u043a\u043e\u0432.
    options = list(options)
    all_options = options + (["\u0421\u0432\u043e\u0439 \u043e\u0442\u0432\u0435\u0442\u2026", "\u0414\u0430\u043b\u0435\u0435"] if multiple else ["\u0421\u0432\u043e\u0439 \u043e\u0442\u0432\u0435\u0442\u2026"])
    selected = 0
    checked: set[int] = set()

    rendered = _render_poll(question, all_options, selected, step_info, multiple, checked, len(options))
    sys.stdout.write(rendered + '\n')
    sys.stdout.flush()
    line_count = _visual_line_count(rendered, _get_term_width())

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
                    all_options = [*options, "Свой ответ…", "Далее"]
                    selected = len(options) + 1
                    rendered = _render_poll(question, all_options, selected, step_info, multiple, checked, len(options))
                    sys.stdout.write(rendered + '\n')
                    sys.stdout.flush()
                    line_count = _visual_line_count(rendered, _get_term_width())
                    continue
                return answer or "(\u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e)"
            if multiple and selected == done_index:
                answers = [options[i] for i in sorted(checked)]
                return answers or ["(\u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e)"]
            return all_options[selected]
        elif key == 'ctrl-c':
            _clear_lines(line_count)
            return ["(\u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e)"] if multiple else "(\u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e)"

        _clear_lines(line_count)
        rendered = _render_poll(question, all_options, selected, step_info, multiple, checked, len(options))
        sys.stdout.write(rendered + '\n')
        sys.stdout.flush()
        line_count = _visual_line_count(rendered, _get_term_width())


def run_poll(steps: list[dict]) -> list[dict]:
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
        answer = run_poll_step(question, options[:10], step_info, multiple)
        results.append({"question": question, "answer": answer})

    return results
