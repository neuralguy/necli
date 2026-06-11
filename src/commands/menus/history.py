import re

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config.themes import t
from session import Session

console = Console()


_TOOL_BLOCK_RE = re.compile(
    r':::call[ \t]+(?P<tool>\w+)(?P<header>[^\n]*)\n(?P<body>.*?)(?:\n|^)call:::[ \t]*(?:\n|$)'
    r"|(?P<fence>`{3,}|~{3,})call[ \t]+(?P<old_tool>\w+)[^\n]*\n(?P<old_body>.*?)(?:\n|^)(?P=fence)[ \t]*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)


def _extract_tool_summary(body: str, tool: str) -> str:
    """Однострочное резюме вызова инструмента."""
    body = body.strip()
    if not body:
        return tool
    first_line = body.split("\n", 1)[0].strip()
    # JSON-args: пробуем вытащить path/command/query
    if first_line.startswith("{"):
        # path
        m = re.search(r'"path"\s*:\s*"([^"]+)"', body)
        if m:
            return f"{tool} {m.group(1)}"
        m = re.search(r'"command"\s*:\s*"([^"]+)"', body)
        if m:
            cmd = m.group(1)
            return f"{tool} `{cmd[:80]}{'…' if len(cmd) > 80 else ''}`"
        m = re.search(r'"query"\s*:\s*"([^"]+)"', body)
        if m:
            q = m.group(1)
            return f"{tool} `{q[:80]}{'…' if len(q) > 80 else ''}`"
        m = re.search(r'"name"\s*:\s*"([^"]+)"', body)
        if m:
            return f"{tool} {m.group(1)}"
    # path в шапке fence (write/patch/create)
    return tool


def _render_assistant(content: str) -> Text:
    """Заменяет tool-блоки на однострочные иконки, оставляет текст."""
    out = Text()
    pos = 0
    for m in _TOOL_BLOCK_RE.finditer(content):
        prefix = content[pos:m.start()].strip()
        if prefix:
            if out.plain:
                out.append("\n")
            out.append(prefix)
        # Извлекаем имя tool из шапки fence (включая path="…")
        head_line = m.group(0).split("\n", 1)[0]
        tool_name = m.group("tool") or m.group("old_tool")
        body = m.group("body") if m.group("tool") else m.group("old_body")
        path_match = re.search(r'path="([^"]+)"', head_line)
        if path_match:
            summary = f"{tool_name} {path_match.group(1)}"
        else:
            summary = _extract_tool_summary(body or "", tool_name)
        if out.plain:
            out.append("\n")
        out.append("  → ", style=f"bold {t('accent')}")
        out.append(summary, style="cyan")
        pos = m.end()
    suffix = content[pos:].strip()
    if suffix:
        if out.plain:
            out.append("\n")
        out.append(suffix)
    return out


def _render_tool_result(content: str) -> str:
    """Сжатое резюме tool_result в одну строку."""
    first_line = content.strip().split("\n", 1)[0]
    if len(first_line) > 120:
        first_line = first_line[:117] + "…"
    return first_line


def show_history(session: Session, n: int) -> None:
    """Отображает последние N действий агента (user + assistant + tool_result group)."""
    if n <= 0:
        n = 10

    # Группируем: каждый USER → ASSISTANT...→ TOOL_RESULT...→ ASSISTANT...
    # Действие = одно сообщение (user/assistant/tool_result), отображаем раздельно.
    msgs = [m for m in session.messages if m.role in ("user", "assistant", "tool_result")]
    if not msgs:
        console.print("  [dim]History is empty[/dim]")
        return

    selected = msgs[-n:]

    body = Text()
    for i, msg in enumerate(selected):
        if i > 0:
            body.append("\n\n")
            body.append("─" * 60, style="dim")
            body.append("\n\n")

        if msg.role == "user":
            body.append("👤 USER\n", style=f"bold {t('user')}")
            body.append(msg.content)
        elif msg.role == "assistant":
            body.append("🤖 ASSISTANT\n", style=f"bold {t('accent')}")
            body.append(_render_assistant(msg.content))
        elif msg.role == "tool_result":
            body.append("⚙ TOOL → ", style=f"bold {t('success')}")
            body.append(_render_tool_result(msg.content), style="dim")

    title = f"History · last {len(selected)} of {len(msgs)}"
    console.print(Panel(
        body,
        title=title,
        border_style=t("accent"),
        padding=(1, 2),
    ))