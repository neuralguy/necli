"""Live-превью темы: компактный набор реальных UI-блоков с заданной палитрой.

Используется в меню /themes для визуализации блоков tool/response/diff
с подсвеченной темой в текущем (compact, без рамок) формате вывода.
"""

from io import StringIO

from rich.console import Console, Group
from rich.text import Text


def render_theme_preview(colors: dict, width: int = 76) -> str:
    """Рендерит компактные блоки UI в заданной палитре (формат без рамок)."""
    accent = colors.get("accent", "#4a9eff")
    success = colors.get("success", "#50fa7b")
    warning = colors.get("warning", "#f1fa8c")
    error = colors.get("error", "#ff5555")
    info = colors.get("info", "#8be9fd")
    magenta = colors.get("magenta", "#ff79c6")
    purple = colors.get("purple", "#bd93f9")
    dim_text = colors.get("dim_text", "#666666")

    parts: list = []

    # Response — header "● текст"
    response = Text()
    response.append("● ", style=f"bold {success}")
    response.append("Done: renamed old_handler → new_handler.", style="default")
    parts.append(response)
    parts.append(Text(""))

    # Shell — заголовок + нумерованное превью
    shell_hdr = Text()
    shell_hdr.append("⏺ Shell", style=f"bold {warning}")
    shell_hdr.append("(ls -la src/)", style=f"bold {warning}")
    shell_hdr.append("  ", style="default")
    shell_hdr.append("✓", style=success)
    shell_hdr.append(" 0.1s", style="dim")
    parts.append(shell_hdr)
    shell_line = Text("      1 ", style="white")
    shell_line.append("total 24", style="default")
    parts.append(shell_line)
    parts.append(Text(""))

    # Patch — заголовок + inline diff с фоном
    patch_hdr = Text()
    patch_hdr.append("🔧 Patch", style=f"bold {warning}")
    patch_hdr.append("(main.py)", style=f"bold {warning}")
    patch_hdr.append("  ", style="default")
    patch_hdr.append("✓", style=success)
    patch_hdr.append(" 0.3s", style="dim")
    parts.append(patch_hdr)
    summary = Text("   ⎿  ", style=warning)
    summary.append("1 changed", style=warning)
    parts.append(summary)

    bg_del = "#2a0808"
    bg_add = "#082a08"
    fg_del = error
    fg_add = success
    body_w = max(8, width - 11)

    def _diff_row(num: int, sign: str, text: str, fg: str, bg: str) -> Text:
        prefix = Text(f"      {num} ", style="white")
        sign_t = Text(sign, style=f"bold {fg} on {bg}")
        body = Text(text, style=f"{fg} on {bg}")
        pad = body_w - len(text)
        if pad > 0:
            body.append(" " * pad, style=f"on {bg}")
        return prefix + sign_t + body

    parts.append(_diff_row(12, "- ", "return req.data", fg_del, bg_del))
    parts.append(_diff_row(12, "+ ", "return req.data.strip()", fg_add, bg_add))
    parts.append(Text(""))

    # Delete — заголовок без превью
    delete_hdr = Text()
    delete_hdr.append("🗑  Delete", style=f"bold {error}")
    delete_hdr.append("(old_handler.py)", style=f"bold {error}")
    delete_hdr.append("  ", style="default")
    delete_hdr.append("✓", style=error)
    delete_hdr.append(" 0.0s", style="dim")
    parts.append(delete_hdr)
    parts.append(Text(""))

    # Grep — заголовок + summary + результат
    grep_hdr = Text()
    grep_hdr.append("🔎 Grep", style=f"bold {magenta}")
    grep_hdr.append("(new_handler → src/)", style=f"bold {magenta}")
    grep_hdr.append("  ", style="default")
    grep_hdr.append("✓", style=success)
    grep_hdr.append(" 3 matches", style="dim")
    parts.append(grep_hdr)
    grep_sum = Text("   ⎿  ", style=info)
    grep_sum.append("found 3", style=info)
    parts.append(grep_sum)
    grep_line = Text("      ", style="default")
    grep_line.append("main.py:42: ", style=f"dim {info}")
    grep_line.append("def ", style=magenta)
    grep_line.append("new_handler", style=f"bold {success}")
    grep_line.append("(req):", style="default")
    parts.append(grep_line)
    parts.append(Text(""))

    # Строка с разными ролями (mode/read/ssh/prompt/thinking)
    misc = Text()
    misc.append("  [agent]", style=f"bold {purple}")
    misc.append("  ", style="default")
    misc.append("📖 read", style=info)
    misc.append("  ", style="default")
    misc.append("🔗 ssh", style=magenta)
    misc.append("  ", style="default")
    misc.append("❯ prompt", style=f"bold {accent}")
    misc.append("  ", style="default")
    from config.i18n import t as _i18n
    misc.append(f"⌛ {_i18n('ui.thinking')}…", style=f"italic {dim_text}")
    parts.append(misc)

    body = Group(*parts)

    buf = StringIO()
    render_console = Console(
        file=buf, highlight=False, force_terminal=True,
        width=width, color_system="truecolor",
    )
    render_console.print(body)
    return buf.getvalue()
