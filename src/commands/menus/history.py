import re

from rich.console import Console
from rich.text import Text

from config.i18n import t as _
from config.themes import t
from session import Session
from ui.overlays import key_hints
from ui.shell import Overlay, get_shell

_TOOL_BLOCK_RE = re.compile(
    r'^[ \t]*:{2,3}call[ \t]+(?P<tool>\w+)(?P<header>[^\n]*)\n(?P<body>.*?)(?:\n|^)call:{2,3}[ \t]*(?:\n|$)'
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
            out.append(_indent(prefix))
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
        out.append("     → ", style=f"bold {t('accent')}")
        out.append(summary, style=t("info"))
        pos = m.end()
    suffix = content[pos:].strip()
    if suffix:
        if out.plain:
            out.append("\n")
        out.append(_indent(suffix))
    return out


def _render_tool_result(content: str) -> str:
    """Сжатое резюме tool_result в одну строку."""
    first_line = content.strip().split("\n", 1)[0]
    if len(first_line) > 120:
        first_line = first_line[:117] + "…"
    return first_line


def _indent(text: str, prefix: str = "     ") -> str:
    return "\n".join(prefix + line for line in str(text).splitlines())


class HistoryOverlay(Overlay):
    """Прокручиваемая история без записи в scrollback."""

    def __init__(self, title: Text, body: Text) -> None:
        super().__init__()
        self.title = title
        self.lines = list(body.split("\n", allow_blank=True))
        self.top = 10**9  # первый кадр открывается на самых свежих строках
        self.page = 1

    def render(self, width: int) -> Text:
        budget = self.shell.overlay_budget() if self.shell is not None else 20
        self.page = max(1, budget - 2)  # заголовок + пустой ряд
        max_top = max(0, len(self.lines) - self.page)
        self.top = max(0, min(self.top, max_top))

        out = self.title.copy()
        out.append("\n\n")
        visible = self.lines[self.top:self.top + self.page]
        for i, source in enumerate(visible):
            line = source.copy()
            line.truncate(max(1, width - 2), overflow="ellipsis")
            out.append_text(line)
            if i + 1 < len(visible):
                out.append("\n")
        return out

    def hint(self) -> str:
        return key_hints(
            ("↑↓", _("stats.hint_scroll")),
            ("pgup/pgdn", _("stats.hint_page")),
            ("esc", _("stats.hint_close")),
        )

    def version(self):
        return self.top

    def handle_key(self, key: str, event) -> bool:
        if key in ("escape", "c-c", "q", "Q", "enter"):
            self.finish(None)
        elif key in ("up", "k"):
            self.top -= 1
        elif key in ("down", "j"):
            self.top += 1
        elif key == "pageup":
            self.top -= self.page
        elif key == "pagedown":
            self.top += self.page
        elif key == "home":
            self.top = 0
        elif key == "end":
            self.top = 10**9
        return True


async def show_history(session: Session, n: int) -> None:
    """Отображает последние N действий агента (user + assistant + tool_result group)."""
    if n <= 0:
        n = 10

    # Группируем: каждый USER → ASSISTANT...→ TOOL_RESULT...→ ASSISTANT...
    # Действие = одно сообщение (user/assistant/tool_result), отображаем раздельно.
    msgs = [m for m in session.messages if m.role in ("user", "assistant", "tool_result")]
    if not msgs:
        return

    selected = msgs[-n:]

    # Блок без рамки и линеек-разделителей показывается как динамическое notice.
    title = Text("  " + _("history.title", n=len(selected), total=len(msgs)),
                 style=f"bold {t('accent')}")
    body = Text()
    for i, msg in enumerate(selected):
        if i > 0:
            body.append("\n")
        body.append("\n")

        if msg.role == "user":
            body.append(f"  👤 {_('history.user')}\n", style=f"bold {t('user')}")
            body.append(_indent(msg.content))
        elif msg.role == "assistant":
            body.append(f"  🤖 {_('history.assistant')}\n", style=f"bold {t('accent')}")
            body.append(_render_assistant(msg.content))
        elif msg.role == "tool_result":
            body.append(f"  ⚙ {_('history.tool')} → ", style=f"bold {t('success')}")
            body.append(_render_tool_result(msg.content), style="dim")

    shell = get_shell()
    if shell is None:
        Console().print(title, body)
        return
    await shell.run_overlay(HistoryOverlay(title, body))
