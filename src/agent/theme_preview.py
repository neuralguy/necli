"""Live-превью темы: реальные compact-блоки CLI с заданной палитрой.

Блоки повторяют то, что агент реально рисует в компактном режиме
(agent/display.py, agent/think.py): заголовки инструментов берутся из
config.ui — того же источника, что и у боевого рендера, — а нумерация
строк, «⎿»-сводки, diff-строки с фоном и строка ввода сохраняют вид.
"""

from io import StringIO

from rich.console import Console, Group
from rich.text import Text

from config import ui
from config.i18n import t as _i18n
from config.themes import BUILTIN_THEMES, DEFAULT_THEME


def _darken(color: str, factor: float) -> str:
    """Статичный кадр тёмной волны для превью темы."""
    raw = str(color or "").removeprefix("#")
    if len(raw) == 3:
        raw = "".join(char * 2 for char in raw)
    if len(raw) != 6:
        return color
    try:
        channels = [int(raw[index:index + 2], 16) for index in (0, 2, 4)]
    except ValueError:
        return color
    return "#" + "".join(
        f"{round(channel * factor):02x}" for channel in channels
    )


def _tool_disp(tool: str) -> tuple[str, str]:
    """(display_name, роль палитры) инструмента — как у боевого рендера."""
    info = ui.tool(tool)
    emoji = (info.get("emoji", "") or "").strip()
    label = info.get("label", tool) or tool
    role = info.get("color_role", "warning") or "warning"
    return f"{emoji} {label}".strip(), role


def _hdr(tool: str, arg: str, colors: dict, *,
         status: tuple[str, str] | None = None,
         status_color: str | None = None) -> Text:
    """Заголовок блока: `⏺ Shell(ls -la src/)  ✓ 0.1s`."""
    name, role = _tool_disp(tool)
    color = colors.get(role, colors.get("warning"))
    txt = Text()
    txt.append(f"{name}(", style=f"bold {color}")
    txt.append(arg, style=f"bold {color}")
    txt.append(")", style=f"bold {color}")
    if status:
        icon, elapsed = status
        txt.append("  ", style="default")
        txt.append(icon, style=status_color or colors.get("success"))
        if elapsed:
            txt.append(f" {elapsed}", style="dim")
    return txt


def render_theme_preview(colors: dict, width: int = 76) -> str:
    """Рендерит реальные compact-блоки CLI в заданной палитре."""
    # Частичная палитра (кастомные оверрайды) дополняется дефолтной темой,
    # чтобы превью никогда не зависело от хардкода.
    merged = dict(BUILTIN_THEMES[DEFAULT_THEME])
    if isinstance(colors, dict):
        merged.update(colors)
    colors = merged

    accent = colors["accent"]
    success = colors["success"]
    warning = colors["warning"]
    info = colors["info"]
    dim_text = colors["dim_text"]
    fg_primary = colors["fg_primary"]

    parts: list = []

    # Response — «● текст»
    response = Text()
    response.append("● ", style=f"bold {success}")
    response.append("Done: renamed old_handler → new_handler.", style="default")
    parts.append(response)
    parts.append(Text(""))

    # Read — заголовок + сводка пути
    parts.append(_hdr("read", "main.py", colors))
    read_sum = Text()
    read_sum.append("   ⎿  ", style=info)
    read_sum.append("main.py lines 1-42", style=info)
    parts.append(read_sum)
    parts.append(Text(""))

    # Shell — заголовок + нумерованное превью вывода
    parts.append(_hdr("shell", "ls -la src/", colors, status=("✓", "0.1s")))
    shell_line = Text("      1 ", style=colors["fg_primary"])
    shell_line.append("total 24", style="default")
    parts.append(shell_line)
    parts.append(Text(
        "        " + _i18n("compact.more_lines", n=7),
        style=f"italic {dim_text}",
    ))
    parts.append(Text(""))

    # Patch — заголовок + сводка + inline-diff с фоном (как в display.py)
    parts.append(_hdr("patch_file", "main.py", colors, status=("✓", "0.3s")))
    patch_sum = Text("   ⎿  ", style=warning)
    patch_sum.append("1 changed", style=warning)
    parts.append(patch_sum)

    bg_del = colors["diff_del_bg"]
    bg_add = colors["diff_add_bg"]
    fg_del = colors["diff_del_fg"]
    fg_add = colors["diff_add_fg"]
    pref_del = ui.get("diff_colors.prefix_delete", "- ")
    pref_add = ui.get("diff_colors.prefix_add", "+ ")
    body_w = max(8, width - 6 - 1 - len(pref_del) - 2)

    def _diff_row(num: str, sign: str, text: str, fg: str, bg: str) -> Text:
        prefix = Text(f"      {num} ", style=colors["fg_primary"])
        sign_t = Text(sign, style=f"bold {fg} on {bg}")
        body = Text(text, style=f"{fg} on {bg}")
        pad = body_w - len(text)
        if pad > 0:
            body.append(" " * pad, style=f"on {bg}")
        return prefix + sign_t + body

    parts.append(_diff_row("12", pref_del, "return req.data", fg_del, bg_del))
    parts.append(_diff_row("12", pref_add, "return req.data.strip()", fg_add, bg_add))
    parts.append(Text(""))

    # Create — заголовок без контента (мгновенная тихая операция)
    parts.append(_hdr("create_file", "new_handler.py", colors,
                      status=("✓", ""), status_color=success))
    parts.append(Text(""))

    # Grep — заголовок + сводка из первой строки вывода
    parts.append(_hdr("grep", "new_handler -> src/", colors, status=("✓", "0.1s")))
    grep_sum = Text("   ⎿  ", style=info)
    grep_sum.append("main.py:42: def new_handler(req):", style=info)
    parts.append(grep_sum)
    parts.append(Text(""))

    # Единый раундовый Working-блок: акцент с тёмной волной + живая сводка.
    working_hdr = Text()
    factors = (1.0, 0.92, 0.70, 0.45, 0.60, 0.84, 1.0)
    for char, factor in zip("Working", factors, strict=True):
        color = _darken(accent, factor)
        working_hdr.append(char, style=f"bold {color}")
    working_hdr.append(" 3s", style="dim")
    parts.append(working_hdr)
    working_sum = Text("   ⎿  ", style="dim")
    working_sum.append("2 calls · 8.4k tokens", style=fg_primary)
    parts.append(working_sum)
    parts.append(Text(""))

    # Строка ввода: «🚀agent ❯»
    prompt = Text()
    prompt.append("🚀agent ", style=f"bold {accent}")
    prompt.append("❯", style=f"bold {success}")
    parts.append(prompt)

    body = Group(*parts)

    buf = StringIO()
    render_console = Console(
        file=buf, highlight=False, force_terminal=True,
        width=width, color_system="truecolor",
    )
    render_console.print(body)
    return buf.getvalue()
