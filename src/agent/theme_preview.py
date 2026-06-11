"""Live-превью темы: компактный набор реальных UI-блоков с заданной палитрой.

Используется в меню /themes для визуализации блоков tool/response/diff
с подсвеченной темой. Высота — строго ~12-14 строк, чтобы суммарная
высота меню (список + превью + hint) умещалась в стандартный терминал.
"""

from io import StringIO

from rich.console import Console, Group
from rich.panel import Panel
from rich.text import Text


_SAMPLE_PATCH = """- def old_handler(req):
- 	    return req.data
+ def new_handler(req):
+ 	    return req.data.strip()"""


def render_theme_preview(colors: dict, width: int = 76) -> str:
    """Рендерит компактные блоки UI в заданной палитре."""
    accent = colors.get("accent", "#4a9eff")
    success = colors.get("success", "#50fa7b")
    warning = colors.get("warning", "#f1fa8c")
    error = colors.get("error", "#ff5555")
    info = colors.get("info", "#8be9fd")
    magenta = colors.get("magenta", "#ff79c6")
    purple = colors.get("purple", "#bd93f9")
    dim_text = colors.get("dim_text", "#666666")

    # Response (3 строки с рамкой)
    response = Panel(
        Text("Done: renamed old_handler → new_handler.", style="default"),
        title=f"[bold {success}]— Response[/bold {success}]",
        title_align="left",
        subtitle=f"[{dim_text}]2.1s · 42 tk · $0.001[/{dim_text}]",
        subtitle_align="right",
        border_style=success,
        padding=(0, 1),
        width=width,
    )

    # Shell
    shell = Panel(
        Text("$ ls -la src/", style=f"{warning}"),
        title=f"[bold {warning}]⏺ Shell[/bold {warning}]",
        title_align="left",
        subtitle=f"[{success}]✓[/{success}][{dim_text}] 0.1s[/{dim_text}]",
        subtitle_align="right",
        border_style=f"dim {warning}",
        padding=(0, 1),
        width=width,
    )

    # Patch diff (2 строки контента + рамка = 4 строки)
    diff_text = Text()
    lines = _SAMPLE_PATCH.split("\n")
    for idx, line in enumerate(lines):
        nl = "" if idx == len(lines) - 1 else "\n"
        if line.startswith("- "):
            diff_text.append(line + nl, style=error)
        elif line.startswith("+ "):
            diff_text.append(line + nl, style=success)
        else:
            diff_text.append(line + nl, style=dim_text)
    patch = Panel(
        diff_text,
        title=f"[bold {warning}]🔧 Patch[/bold {warning}] [{dim_text}]main.py[/{dim_text}]",
        title_align="left",
        subtitle=f"[{success}]✓[/{success}][{dim_text}] 0.3s[/{dim_text}]",
        subtitle_align="right",
        border_style=f"dim {warning}",
        padding=(0, 1),
        width=width,
    )

    # Delete-блок (1 строка контента)
    delete = Panel(
        Text("  removed: old_handler.py", style=f"dim {error}"),
        title=f"[bold {error}]🗑  Delete[/bold {error}] [{dim_text}]old_handler.py[/{dim_text}]",
        title_align="left",
        subtitle=f"[{error}]✓[/{error}][{dim_text}] 0.0s[/{dim_text}]",
        subtitle_align="right",
        border_style=f"dim {error}",
        padding=(0, 1),
        width=width,
    )

    # Grep-блок (1 строка совпадения)
    grep_text = Text()
    grep_text.append("main.py:42: ", style=f"dim {info}")
    grep_text.append("def ", style=magenta)
    grep_text.append("new_handler", style=f"bold {success}")
    grep_text.append("(req):", style="default")
    grep = Panel(
        grep_text,
        title=f"[bold {magenta}]🔎 Grep[/bold {magenta}] [{dim_text}]new_handler[/{dim_text}]",
        title_align="left",
        subtitle=f"[{success}]✓[/{success}][{dim_text}] 3 matches[/{dim_text}]",
        subtitle_align="right",
        border_style=f"dim {magenta}",
        padding=(0, 1),
        width=width,
    )

    # Строка с разными ролями (mode/ssh/info/accent) под превью
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

    body = Group(response, shell, patch, delete, grep, misc)

    buf = StringIO()
    render_console = Console(
        file=buf, highlight=False, force_terminal=True,
        width=width, color_system="truecolor",
    )
    render_console.print(body)
    return buf.getvalue()