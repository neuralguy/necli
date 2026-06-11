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

def _render_poll(question: str, options: list[str], selected: int, step_info: str = "") -> str:
    lines = []
    accent = _accent_ansi()

    # Header
    header = f"  \033[1;{accent}m\u2753 {question}\033[0m"
    if step_info:
        header += f"  \033[2m{step_info}\033[0m"
    lines.append(header)
    lines.append("")

    for i, opt in enumerate(options):
        if i == selected:
            lines.append(f"  \033[1;{accent}m\u25b8 {opt}\033[0m")
        else:
            lines.append(f"  \033[2m  {opt}\033[0m")

    lines.append("")
    lines.append("  \033[2m\u2191\u2193 \u2014 \u0432\u044b\u0431\u043e\u0440  \u23ce \u2014 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c\033[0m")
    return "\n".join(lines)


def _clear_lines(n: int):
    for _ in range(n):
        sys.stdout.write('\033[A\033[2K')
    sys.stdout.write('\r')
    sys.stdout.flush()


def _input_custom_answer(question: str) -> str:
    from rich.console import Console  # noqa: F811 — local instance
    from config.themes import t
    console = Console()
    console.print(f"  [bold {t('accent')}]\u2753 {question}[/bold {t('accent')}]")
    console.print()
    try:
        sys.stdout.write(f"  \033[1;{_accent_ansi()}m\u25b8 \033[0m")
        sys.stdout.flush()
        answer = input()
        return answer.strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def run_poll_step(question: str, options: list[str], step_info: str = "") -> str:
    all_options = options + ["\u0421\u0432\u043e\u0439 \u043e\u0442\u0432\u0435\u0442\u2026"]
    selected = 0

    rendered = _render_poll(question, all_options, selected, step_info)
    sys.stdout.write(rendered + '\n')
    sys.stdout.flush()
    line_count = _visual_line_count(rendered, _get_term_width())

    while True:
        key = _read_key()

        if key == 'up':
            selected = (selected - 1) % len(all_options)
        elif key == 'down':
            selected = (selected + 1) % len(all_options)
        elif key == 'enter':
            _clear_lines(line_count)
            if selected == len(all_options) - 1:
                answer = _input_custom_answer(question)
                _clear_lines(3)
                if not answer:
                    answer = "(\u043f\u0440\u043e\u043f\u0443\u0449\u0435\u043d\u043e)"
                return answer
            else:
                return all_options[selected]
        elif key == 'ctrl-c':
            _clear_lines(line_count)
            return "(\u043e\u0442\u043c\u0435\u043d\u0435\u043d\u043e)"

        _clear_lines(line_count)
        rendered = _render_poll(question, all_options, selected, step_info)
        sys.stdout.write(rendered + '\n')
        sys.stdout.flush()
        line_count = _visual_line_count(rendered, _get_term_width())


def run_poll(steps: list[dict]) -> list[dict]:
    results = []
    total = len(steps)

    for i, step in enumerate(steps):
        question = step.get("question", "")
        options = step.get("options", [])
        if not question:
            continue

        step_info = f"({i + 1}/{total})" if total > 1 else ""
        answer = run_poll_step(question, options[:4], step_info)
        results.append({"question": question, "answer": answer})

    return results
