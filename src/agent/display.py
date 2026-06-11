"""Terminal rendering of tool commands and their output."""

import re

from rich.console import Console
from rich.syntax import Syntax
from rich.text import Text

import tools
from agent.syntax import _EXT_LEXER_MAP
from config.themes import t
from config.i18n import t as _i18n
from config.ui import ui
from tools._html_unescape import unescape_nested as _unescape_for_display


def is_compact() -> bool:
    """Компактный режим — единственный поддерживаемый (рамочный режим удалён)."""
    return True

console = Console()

# Когда True — рендер-функции не пишут в RenderStore, чтобы replay не зациклился.
_REPLAY_ACTIVE = False


def set_replay_active(active: bool) -> None:
    global _REPLAY_ACTIVE
    _REPLAY_ACTIVE = bool(active)


def _store_tool(call, result, subtitle: str = "") -> None:
    if _REPLAY_ACTIVE:
        return
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
        if ctx is None or getattr(ctx, "render_store", None) is None:
            return
        if result is None:
            ctx.render_store.add_command_only(call, subtitle=subtitle)
        else:
            ctx.render_store.add_tool(call, result, subtitle=subtitle)
    except Exception:
        pass


def _store_command(cmd: str, tool_name: str, args: dict, subtitle: str = "") -> None:
    if _REPLAY_ACTIVE:
        return
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
        if ctx is None or getattr(ctx, "render_store", None) is None:
            return
        call = tools.ToolCall(command=cmd, tool_name=tool_name, args=dict(args or {}), raw="")
        ctx.render_store.add_command_only(call, subtitle=subtitle)
    except Exception:
        pass


def _store_assistant(text: str, subtitle: str = "", message_num: int = 0) -> None:
    if _REPLAY_ACTIVE:
        return
    try:
        from agent.loop import get_current_ctx
        ctx = get_current_ctx()
        if ctx is None or getattr(ctx, "render_store", None) is None:
            return
        ctx.render_store.add_assistant_block(text, subtitle=subtitle, message_num=message_num)
    except Exception:
        pass


def show_plan_update(plan, action: str = "", focus_index: int | None = None) -> None:
    if plan is None or not getattr(plan, "steps", None):
        return
    full = action == "create" or bool(getattr(plan, "is_complete", False))
    if focus_index is None:
        focus_index = getattr(plan, "current_step_index", None)
    if focus_index is None:
        focus_index = 0
    console.print()
    try:
        from planner import plan_to_snapshot, render_plan_panel
        console.print(render_plan_panel(
            plan,
            compact=False,
            focus_index=focus_index,
            full=full,
        ))
        if not _REPLAY_ACTIVE:
            from agent.loop import get_current_ctx
            ctx = get_current_ctx()
            if ctx is not None and getattr(ctx, "render_store", None) is not None:
                ctx.render_store.add_plan(
                    plan_to_snapshot(plan),
                    action=action,
                    focus_index=focus_index,
                )
    except Exception:
        pass

def MAX_RESULT_LENGTH():
    return int(ui.get("limits.max_result_length", 15000))
def RESPONSE_BORDER():
    return t("success")
def MAX_WIDTH():
    return int(ui.get("limits.max_width", 100))

# Инструменты, у которых при успехе output скрывается: пользователь видит факт
# изменения по панели команды и ✓-статусу, текст не нужен.
_SILENT_OK_TOOLS = frozenset({
    "write_file", "create_file", "patch_file",
    "delete_file", "rename_file", "copy_file", "move_file",
    "mkdir", "rmdir", "create_docx",
})


def COMPACT_HEAD_LINES():
    return int(ui.get("limits.compact_head_lines", 10))
def COMPACT_TAIL_LINES():
    return int(ui.get("limits.compact_tail_lines", 10))

# Для верстки с рамками (не compact) — больший лимит на статичный вывод.
def PANEL_HEAD_LINES():
    return int(ui.get("limits.panel_head_lines", 5))
def PANEL_TAIL_LINES():
    return int(ui.get("limits.panel_tail_lines", 5))

def _spinner_frames() -> list[str]:
    frames = ui.get("spinner.frames", None)
    if isinstance(frames, list) and frames:
        return [str(f) for f in frames]
    return [
        "\u280B", "\u2819", "\u2839", "\u2838",
        "\u283C", "\u2834", "\u2826", "\u2827",
        "\u2807", "\u280F",
    ]

class _SpinnerFramesProxy:
    def __iter__(self):
        return iter(_spinner_frames())
    def __getitem__(self, idx):
        return _spinner_frames()[idx]
    def __len__(self):
        return len(_spinner_frames())

SPINNER_FRAMES = _SpinnerFramesProxy()


def exec_spinner_frames() -> list[str]:
    frames = ui.get("spinner.exec_frames", None)
    if isinstance(frames, list) and frames:
        return [str(f) for f in frames]
    return ["\u25F4", "\u25F7", "\u25F6", "\u25F5"]

def _resolve_color(entry: dict, default_role: str) -> str:
    """Прямой 'color' (HEX/имя) перебивает 'color_role'."""
    direct = entry.get("color")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    role = entry.get("color_role", default_role) or default_role
    return t(role)


def _tool_display_entry(tool_name: str) -> tuple[str, str] | None:
    """Возвращает (display_name, color) для известного tool_name из ui.json."""
    entry = ui.get(f"tools.{tool_name}", None)
    if not isinstance(entry, dict):
        return None
    emoji = entry.get("emoji", "") or ""
    label = entry.get("label", tool_name) or tool_name
    display_name = f"{emoji} {label}".strip()
    return (display_name, _resolve_color(entry, "warning"))


def _mcp_display_for(tool_name: str) -> tuple[str, str] | None:
    if not tool_name.startswith("mcp__"):
        return None
    rest = tool_name[5:]
    if "__" not in rest:
        return None
    server, tname = rest.split("__", 1)
    info = ui.mcp_display(server, tname)
    emoji = info.get("emoji", "🔌") or "🔌"
    label = info.get("label", f"{server}.{tname}")
    return (f"{emoji} {label}".strip(), _resolve_color(info, "magenta"))

class _ToolDisplayProxy:
    def get(self, key, default=None):
        entry = _tool_display_entry(key)
        if entry is not None:
            return entry
        mcp = _mcp_display_for(key)
        if mcp is not None:
            return mcp
        return default
    def __getitem__(self, key):
        entry = _tool_display_entry(key)
        if entry is not None:
            return entry
        mcp = _mcp_display_for(key)
        if mcp is not None:
            return mcp
        raise KeyError(key)
    def __contains__(self, key):
        if _tool_display_entry(key) is not None:
            return True
        return _mcp_display_for(key) is not None

TOOL_DISPLAY = _ToolDisplayProxy()


def _w() -> int:
    return min(MAX_WIDTH(), console.width)


def _compact_content(text: str, head: int | None = None, tail: int | None = None) -> str:
    """Compact display: first `head` lines + ... N lines + last `tail` lines."""
    if head is None:
        head = COMPACT_HEAD_LINES()
    if tail is None:
        tail = COMPACT_TAIL_LINES()
    lines = text.split("\n")
    total = len(lines)
    if total <= head + tail + 2:
        return text
    skipped = total - head - tail
    head_lines = lines[:head]
    tail_lines = lines[-tail:]
    return (
        "\n".join(head_lines)
        + f"\n\n... {skipped} lines\n\n"
        + "\n".join(tail_lines)
    )


def _format_path_for_title(path) -> str:
    """Список путей → 'N files' (короткий заголовок); строка — как есть.

    Раньше тут перечислялись все имена файлов через запятую, что забивало
    title рамки и обрезалось терминалом. Имена файлов теперь идут в теле
    панели отдельными строками — каждая со своим bytes/lines.
    """
    if isinstance(path, (list, tuple)):
        names = [str(p) for p in path if p]
        if not names:
            return ""
        if len(names) == 1:
            return names[0]
        return f"{len(names)} files"
    return str(path) if path else ""

def _compact_display_value(value: str) -> str:
    """Compact display: head + ... + tail for large text values.

    В режиме с рамками используем больший лимит (PANEL_*),
    в compact-режиме — узкий (COMPACT_*).
    """
    if not isinstance(value, str):
        return value
    if _EXPANDED_PREVIEW:
        return value
    if is_compact():
        return _compact_content(value, COMPACT_HEAD_LINES(), COMPACT_TAIL_LINES())
    return _compact_content(value, PANEL_HEAD_LINES(), PANEL_TAIL_LINES())


def prepare_display_args(args: dict, tool_name: str) -> dict:
    display_args = {k: _unescape_for_display(v) for k, v in args.items()}

    if "b64" in display_args:
        display_args["b64"] = f"({len(display_args['b64'])} chars base64)"

    # Compact display of content for write_file / create_file / patch_file
    if "content" in display_args and isinstance(display_args["content"], str):
        display_args["content"] = _compact_display_value(display_args["content"])

    # Compact display for patches in patch_file
    if "patches" in display_args and isinstance(display_args["patches"], list):
        compact_patches = []
        for p in display_args["patches"]:
            cp = dict(p)
            for key in ("find", "replace", "insert"):
                if key in cp and isinstance(cp[key], str):
                    cp[key] = _compact_display_value(cp[key])
            compact_patches.append(cp)
        display_args["patches"] = compact_patches

    # Top-level find/replace fields
    for key in ("find", "replace", "insert"):
        if key in display_args and isinstance(display_args[key], str):
            display_args[key] = _compact_display_value(display_args[key])

    return display_args


def show_command(cmd: str, tool_name: str = "shell", args: dict | None = None, subtitle: str = ""):
    """Standalone command panel.

    Используется для web_search (нет результата для объединения) и для
    skipped tool calls при soft interrupt — нужно показать пользователю,
    какой именно вызов был пропущен, для любого tool, не только web_search.
    """
    args = args or {}
    _store_command(cmd, tool_name, args, subtitle=subtitle)
    _show_tool_compact(None, None, cmd, tool_name, args, subtitle=subtitle)


def _file_link_style(raw_path, base_color: str) -> str:
    """Если raw_path — реальный файловый путь, возвращает стиль с file:// link."""
    if not raw_path or not isinstance(raw_path, str):
        return f"bold {base_color}"
    try:
        from tools._paths import resolve_path
        p = resolve_path(raw_path)
        return f"bold underline {base_color} link file://{p}"
    except Exception:
        return f"bold {base_color}"


def _compact_title_text(
    tool_name: str, args: dict, status_icon: str = "", status_color: str = "",
    lead_frame: str = "",
) -> Text:
    """Заголовок для compact-режима: ✨ Tool(path) ✓ 1.2s — тот же display_name что и с рамками.

    Если задан lead_frame (кадр анимации) — он рисуется ВМЕСТО эмодзи в начале
    display_name (используется во время выполнения инструмента).
    """
    display_name, color = TOOL_DISPLAY.get(tool_name, ("⏺ Tool", "yellow"))
    if lead_frame:
        # display_name = "🔎 Grep" → отрезаем эмодзи, ставим кадр анимации.
        parts = display_name.split(" ", 1)
        label = parts[1] if len(parts) == 2 else display_name
        display_name = f"{lead_frame} {label}"
    txt = Text()
    raw_path = args.get("path", "")
    if not raw_path and tool_name in ("copy_file", "rename_file", "move_file"):
        raw_path = args.get("src") or args.get("source") or ""
    path_disp = _format_path_for_title(raw_path)
    arg_disp = path_disp
    is_file_path = bool(path_disp)
    if path_disp and tool_name in ("copy_file", "rename_file", "move_file"):
        dest_raw = args.get("dest") or args.get("destination") or args.get("dst") \
            or args.get("new_path") or ""
        dest_disp = _format_path_for_title(dest_raw)
        if dest_disp:
            arg_disp = f"{path_disp} → {dest_disp}"
            is_file_path = False
    if tool_name == "grep_files":
        pat = args.get("pattern", "") or args.get("query", "") or ""
        gpath_raw = args.get("path", "") or "."
        gpath = _format_path_for_title(gpath_raw) or "."
        txt.append(f"{display_name}(", style=f"bold {color}")
        if pat:
            txt.append(pat, style=f"bold {color}")
            txt.append(" → ", style=f"bold {color}")
        txt.append(gpath, style=_file_link_style(gpath if isinstance(gpath_raw, str) else "", color))
        txt.append(")", style=f"bold {color}")
        if status_icon:
            txt.append("  ")
            txt.append(status_icon, style=status_color)
        return txt
    elif not arg_disp and tool_name == "web_search":
        urls = args.get("urls", []) or []
        arg_disp = args.get("query", "") or args.get("url", "") or (", ".join(urls) if urls else "")
    elif not arg_disp and tool_name == "shell":
        cmd = args.get("command", "") or ""
        # Однострочная команда — целиком; многострочная — первая строка + …
        if "\n" in cmd:
            first = cmd.split("\n", 1)[0]
            arg_disp = first[:80] + (" …" if len(first) > 80 or "\n" in cmd else "")
        else:
            arg_disp = cmd[:120] + ("…" if len(cmd) > 120 else "")
    elif not arg_disp and tool_name == "ssh":
        host = args.get("host", "") or ""
        cmd = args.get("command", "") or args.get("command_str", "") or ""
        if not cmd and args.get("upload"):
            cmd = f"↑ {args.get('upload')} → {args.get('dest', '')}"
        elif not cmd and args.get("download"):
            cmd = f"↓ {args.get('download')} → {args.get('dest', '')}"
        if "\n" in cmd:
            first = cmd.split("\n", 1)[0]
            cmd = first[:80] + (" …" if len(first) > 80 or "\n" in cmd else "")
        else:
            cmd = cmd[:120] + ("…" if len(cmd) > 120 else "")
        arg_disp = f"{host}: {cmd}" if host and cmd else (cmd or host)
    if arg_disp:
        txt.append(f"{display_name}(", style=f"bold {color}")
        if is_file_path and isinstance(raw_path, str):
            txt.append(arg_disp, style=_file_link_style(raw_path, color))
        elif is_file_path and isinstance(raw_path, list):
            # Несколько файлов — без линка (там display "N files: a, b, c")
            txt.append(arg_disp, style=f"bold {color}")
        else:
            txt.append(arg_disp, style=f"bold {color}")
        txt.append(")", style=f"bold {color}")
    else:
        txt.append(display_name, style=f"bold {color}")
    if status_icon:
        txt.append("  ")
        txt.append(status_icon, style=status_color)
    return txt


def _compact_separator(width: int | None = None) -> Text:
    w = width if width is not None else _w()
    return Text("  " + "─" * max(10, w - 4), style="dim")


def _compact_summary_line(tool_name: str, args: dict, result: tools.ToolResult | None, cmd: str) -> str:
    """Одна короткая строка-сводка по инструменту для compact-режима."""
    if result is not None and result.status != "ok":
        first_line = (result.output or "").strip().split("\n")[0][:80]
        return first_line or _i18n("compact.error")

    if tool_name == "web_search":
        if result is not None:
            out = (result.output or "").strip()
            if not out:
                return ""
            n_results = len(re.findall(r"(?m)^\[\d+\] ", out))
            if n_results:
                return _i18n("compact.results_n", n=n_results)
            # web_fetch: первая строка вида "=== URL ===" → показываем URL
            m = re.match(r"^=== (.+?) ===\s*$", out.split("\n", 1)[0])
            if m:
                return m.group(1)[:120]
            return out.split("\n", 1)[0][:80]
        return ""

    if tool_name in ("read_files", "read_file"):
        if result is not None:
            infos: list[str] = []
            for line in (result.output or "").split("\n"):
                s = line.strip()
                if s.startswith("[") and s.endswith("]") and ("lines" in s or "·" in s or "bytes" in s):
                    infos.append(s.strip("[]"))
            if not infos:
                return ""
            # Свёрнутый вид: "N files" одной строкой; развёрнутый — дерево.
            if len(infos) >= 2 and not _EXPANDED_PREVIEW:
                return _i18n("compact.files_n", n=len(infos))
            return "\n".join(infos)
        return ""

    if tool_name in ("ls", "tree", "find_files", "grep_files"):
        if result is None:
            return ""
        out = (result.output or "").strip()
        if tool_name == "ls":
            m = re.search(r"(\d+)\s+dirs,\s+(\d+)\s+files", out)
            if m:
                return _i18n("compact.dirs_files_n", dirs=int(m.group(1)), files=int(m.group(2)))
            return ""
        if tool_name == "grep_files":
            n = len([ln for ln in out.split("\n")[1:] if ln.strip()])
            return _i18n("compact.found_n", n=n)
        if tool_name == "find_files":
            m = re.search(r"(\d+)", out)
            if m:
                return _i18n("compact.found_n", n=int(m.group(1)))
            return out.split("\n", 1)[0][:100]
        # tree — считаем строки-элементы (без корневой строки пути)
        n = max(0, len([ln for ln in out.split("\n") if ln.strip()]) - 1)
        return _i18n("compact.found_n", n=n)

    if tool_name == "docx_screenshot":
        if result is None:
            return ""
        out = (result.output or "").strip()
        m = re.search(r"pages?\s+([\d, ]+?)\s+of", out)
        if m:
            nums = m.group(1).strip()
            if "," in nums:
                return _i18n("compact.pages_n", pages=nums)
            return _i18n("compact.page_n", n=nums)
        return ""

    if tool_name in ("write_file", "create_file"):
        if result is None or result.status != "ok":
            return ""
        n = None
        if isinstance(args.get("content"), str):
            c = args["content"]
            n = c.count("\n") + (1 if c and not c.endswith("\n") else 0)
        else:
            m = re.search(r"(\d+)\s+lines", result.output or "")
            if m:
                n = int(m.group(1))
        return _i18n("compact.lines_n", n=n) if n is not None else ""

    if tool_name == "shell":
        return cmd.split("\n", 1)[0][:100]

    if tool_name == "patch_file":
        if result is not None:
            for line in (result.output or "").split("\n"):
                s = line.strip()
                if s.startswith("✓") or "patch" in s.lower() or s.startswith("⚠") or s.startswith("✗"):
                    return s[:100]
        return ""

    if tool_name in _SILENT_OK_TOOLS:
        return ""

    if result is not None:
        out = (result.output or "").strip()
        if out:
            first = out.split("\n", 1)[0][:100]
            return first
    return ""


def COMPACT_PREVIEW_LINES():
    return int(ui.get("limits.compact_preview_lines", 8))
def COMPACT_PREVIEW_LINES_SHELL():
    return int(ui.get("limits.compact_preview_lines_shell", 5))

# Когда True — compact-preview показывает все строки, без обрезки и без "… +N lines"
_EXPANDED_PREVIEW = False


def set_expanded_preview(active: bool) -> None:
    global _EXPANDED_PREVIEW
    _EXPANDED_PREVIEW = bool(active)


def is_expanded_preview() -> bool:
    return _EXPANDED_PREVIEW


def _preview_limit() -> int | None:
    """None = без ограничения (Ctrl+O expand), иначе COMPACT_PREVIEW_LINES."""
    return None if _EXPANDED_PREVIEW else COMPACT_PREVIEW_LINES()


def _compact_preview_content(tool_name: str, args: dict, result: tools.ToolResult | None) -> list | None:
    """Превью контента под compact-заголовком.

    Возвращает список Rich-renderable строк (Text/Syntax) или None.
    """
    # patch_file — diff-preview из output
    if tool_name == "patch_file" and result is not None and result.status == "ok":
        return _compact_patch_preview(args, result)

    # write_file / create_file — summary "N строк" + нумерованный листинг контента
    if tool_name in ("write_file", "create_file") and isinstance(args.get("content"), str):
        content = args["content"]
        lines = content.split("\n")
        if lines and lines[-1] == "":
            lines = lines[:-1]
        total = len(lines)
        if total == 0:
            return None

        out: list = []
        n = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        out.append(Text(
            f"   {ui.get('symbols.summary_prefix', '⎿  ')}{_i18n('compact.lines_n', n=n)}",
            style=t("info"),
        ))

        path = args.get("path", "")
        ext_m = re.match(r".*\.(\w+)$", path or "")
        lexer = _EXT_LEXER_MAP.get(ext_m.group(1).lower(), "text") if ext_m else "text"
        limit = _preview_limit()
        head = lines if limit is None else lines[:limit]
        num_w = len(str(total))
        for i, ln in enumerate(head, start=1):
            num = Text(f"      {str(i).rjust(num_w)} ", style="white")
            try:
                code = Syntax(
                    ln or " ", lexer, theme="monokai", line_numbers=False,
                    padding=(0, 0), background_color="default", word_wrap=False,
                ).highlight(ln or " ")
                if code.plain.endswith("\n"):
                    code.right_crop(1)
                out.append(num + code)
            except Exception:
                out.append(num + Text(ln))
        if total > len(head):
            rest = total - len(head)
            out.append(Text("        " + _i18n("compact.more_lines", n=rest), style=f"italic {t('dim_text')}"))
        return out

    # shell — превью stdout (первые N строк) + "… +M lines"
    if tool_name == "shell" and result is not None:
        output = (result.output or "").rstrip("\n")
        if not output:
            return None
        lines = output.split("\n")
        total = len(lines)
        limit = None if _EXPANDED_PREVIEW else COMPACT_PREVIEW_LINES_SHELL()
        head = lines if limit is None else lines[:limit]
        num_w = len(str(total))
        out: list = []
        for i, ln in enumerate(head, start=1):
            num = Text(f"      {str(i).rjust(num_w)} ", style="white")
            out.append(num + Text(ln))
        if total > len(head):
            rest = total - len(head)
            out.append(Text("        " + _i18n("compact.more_lines", n=rest), style="dim italic"))
        return out

    # grep_files / find_files / lsp_* — первые 3 результата + "… +N строк"
    if tool_name in (
        "grep_files", "find_files",
        "lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics",
    ) and result is not None and result.status == "ok":
        return _compact_result_list_preview(tool_name, result)

    return None


def _compact_result_list_preview(tool_name: str, result: tools.ToolResult) -> list | None:
    """Превью списка результатов (grep/find/lsp): первые 3 строки + остаток."""
    output = (result.output or "").rstrip("\n")
    if not output:
        return None
    raw = output.split("\n")

    # У grep/find/lsp_diagnostics первая строка — заголовок-сводка, не результат.
    has_header = tool_name in ("grep_files", "find_files", "lsp_diagnostics")
    header = raw[0] if has_header else ""
    rows = raw[1:] if has_header else raw
    rows = [ln for ln in rows if ln.strip()]
    if not rows:
        return None

    out: list = []
    if header.strip():
        out.append(Text(
            f"   {ui.get('symbols.summary_prefix', '⎿  ')}{header.strip()}",
            style=t("info"),
        ))

    limit = None if _EXPANDED_PREVIEW else 2
    head = rows if limit is None else rows[:limit]
    for ln in head:
        out.append(Text("      " + ln.strip(), style=t("dim_text")))
    if len(rows) > len(head):
        rest = len(rows) - len(head)
        out.append(Text(
            "        " + _i18n("compact.more_lines", n=rest),
            style=f"italic {t('dim_text')}",
        ))
    return out


def _compact_patch_preview(args: dict, result: tools.ToolResult) -> list:
    """Diff-preview для patch_file: минусы и плюсы с нумерацией."""
    out: list = []
    # Заголовок-сообщение
    summary = ""
    for line in (result.output or "").split("\n"):
        s = line.strip()
        if s.startswith("✓"):
            summary = s.lstrip("✓").strip()
            break
    if summary:
        m = re.match(r"^.*?\s+updated\s+\((.+)\)\s*$", summary)
        if m:
            stats = m.group(1)
            parts = []
            for chunk in stats.split(","):
                c = chunk.strip()
                m1 = re.match(r"^(\d+)\s+changed$", c)
                m2 = re.match(r"^\+(\d+)\s+added$", c)
                m3 = re.match(r"^-(\d+)\s+removed$", c)
                if m1:
                    parts.append(_i18n("patch.stats_changed", n=int(m1.group(1))))
                elif m2:
                    parts.append(_i18n("patch.stats_added", n=int(m2.group(1))))
                elif m3:
                    parts.append(_i18n("patch.stats_removed", n=int(m3.group(1))))
                else:
                    parts.append(c)
            summary = ", ".join(parts) if parts else stats
        out.append(Text(f"   {ui.get('symbols.summary_prefix', '⎿  ')}{summary}", style=t("warning")))

    file_path = args.get("path", "") or ""
    m = re.match(r".*\.(\w+)$", file_path)
    lexer = _EXT_LEXER_MAP.get(m.group(1).lower(), "text") if m else "text"

    from agent.diff_render import _locate_find_in_file as _locate

    # Стартовые строки блоков, посчитанные patch_file'ом по ИСХОДНОМУ файлу
    # (до правки). Надёжнее _locate: после записи find_text в файле уже нет.
    line_starts = list(getattr(result, "line_starts", None) or [])
    _block_idx = [0]

    def _split(text: str) -> list[str]:
        lns = (text or "").split("\n")
        if lns and lns[-1] == "":
            lns = lns[:-1]
        return lns

    # Собираем блоки (find, replace) с абсолютной стартовой строкой в файле.
    # Структура minus_lines / plus_lines: список (abs_line_number, text).
    minus_lines: list[tuple[int, str]] = []
    plus_lines: list[tuple[int, str]] = []

    def _add_block(find_text: str, replace_text: str, insert_text: str = "") -> None:
        find_lns = _split(find_text)
        repl_lns = _split(replace_text or insert_text)
        bi = _block_idx[0]
        _block_idx[0] += 1
        if bi < len(line_starts):
            start = line_starts[bi]
        else:
            start = _locate(file_path, find_text) if find_text else 1
        # Срезаем общий префикс/суффикс — это анкорные строки, они не менялись.
        pref = 0
        while pref < len(find_lns) and pref < len(repl_lns) and find_lns[pref] == repl_lns[pref]:
            pref += 1
        suf = 0
        while (
            suf < len(find_lns) - pref
            and suf < len(repl_lns) - pref
            and find_lns[len(find_lns) - 1 - suf] == repl_lns[len(repl_lns) - 1 - suf]
        ):
            suf += 1
        find_core = find_lns[pref: len(find_lns) - suf] if suf else find_lns[pref:]
        repl_core = repl_lns[pref: len(repl_lns) - suf] if suf else repl_lns[pref:]
        for k, ln in enumerate(find_core):
            minus_lines.append((start + pref + k, ln))
        for k, ln in enumerate(repl_core):
            plus_lines.append((start + pref + k, ln))

    patches = args.get("patches")
    if isinstance(patches, list):
        for p in patches:
            if isinstance(p, dict):
                _add_block(p.get("find", ""), p.get("replace", ""), p.get("insert", ""))
    if "find" in args or "replace" in args or "insert" in args:
        _add_block(args.get("find", ""), args.get("replace", ""), args.get("insert", ""))

    total = len(minus_lines) + len(plus_lines)
    if total == 0:
        return out

    # Inline-формат (как в Claude Code): сначала все удалённые строки (-),
    # затем все добавленные (+), каждая со своим абсолютным номером, фон на
    # всю ширину терминала.
    total_lines = len(minus_lines) + len(plus_lines)
    limit = _preview_limit()
    if limit is None:
        minus_show = minus_lines
        plus_show = plus_lines
    else:
        minus_show = minus_lines[:limit]
        remaining = max(0, limit - len(minus_show))
        plus_show = plus_lines[:remaining]
    shown = len(minus_show) + len(plus_show)

    max_abs = max(
        max((n for n, _ in minus_show), default=0),
        max((n for n, _ in plus_show), default=0),
    )
    num_w = max(1, len(str(max_abs or 1)))

    def _hl(ln: str) -> Text:
        try:
            code = Syntax(
                ln or " ", lexer, theme="monokai", line_numbers=False,
                padding=(0, 0), background_color="default", word_wrap=False,
            ).highlight(ln or " ")
            if code.plain.endswith("\n"):
                code.right_crop(1)
            return code
        except Exception:
            return Text(ln)

    bg_del = ui.get("diff_colors.bg_delete", "#2a0808")
    bg_add = ui.get("diff_colors.bg_add", "#082a08")
    fg_del = ui.get("diff_colors.fg_delete", "#ff6b6b")
    fg_add = ui.get("diff_colors.fg_add", "#6bff6b")
    pref_del = ui.get("diff_colors.prefix_delete", "- ")
    pref_add = ui.get("diff_colors.prefix_add", "+ ")

    term_w = console.width
    # layout: "      NN " + sign(2) + body — фон тянется на всю ширину строки.
    prefix_w = 6 + num_w + 1
    body_w = max(8, term_w - prefix_w - len(pref_del) - 2)

    def _emit(rows: list[tuple[int, str]], sign: str, fg: str, bg: str) -> None:
        for num_val, text_ln in rows:
            num_str = str(num_val).rjust(num_w)
            prefix = Text(f"      {num_str} ", style="white")
            sign_t = Text(sign, style=f"bold {fg} on {bg}")
            body = _hl(text_ln)
            if len(body.plain) > body_w:
                body = body[: max(1, body_w - 1)]
                body.append("\u2026")
            body.stylize(f"on {bg}")
            pad = body_w - len(body.plain)
            if pad > 0:
                body.append(" " * pad, style=f"on {bg}")
            out.append(prefix + sign_t + body)

    _emit(minus_show, pref_del, fg_del, bg_del)
    _emit(plus_show, pref_add, fg_add, bg_add)

    rest_rows = total_lines - shown
    if rest_rows > 0:
        out.append(Text("        " + _i18n("compact.more_lines", n=rest_rows), style=f"italic {t('dim_text')}"))
    return out


def _show_tool_compact(
    call: tools.ToolCall | None,
    result: tools.ToolResult | None,
    cmd: str,
    tool_name: str,
    args: dict,
    subtitle: str = "",
):
    """Компактный режим: заголовок Tool(path) ✓ 1.2s + preview/сводка."""
    raw_args = args or {}
    args = prepare_display_args(raw_args, tool_name)

    is_ok = True
    icon = ""
    status_color = "green"
    if result is not None:
        is_ok = result.status == "ok"
        if is_ok:
            icon = "✓"
        elif result.exit_code == -1:
            icon = "✗"
        else:
            icon = f"✗ exit {result.exit_code}"
        status_color = "green" if is_ok else "red"

    elapsed = (result.elapsed if result else 0.0) or 0.0
    time_str = f" {elapsed:.1f}s" if elapsed else ""
    status_full = f"{icon}{time_str}" if icon else ""

    console.print()
    console.print(_compact_title_text(tool_name, args, status_full, status_color))

    # Сначала пробуем богатое превью контента (только если успех).
    # Используем НЕурезанные raw_args — _compact_preview_content сам ограничивает
    # количество строк через _preview_limit().
    if result is None or result.status == "ok":
        preview = _compact_preview_content(tool_name, raw_args, result)
        if preview:
            for line in preview:
                console.print(line)
            return

    summary = _compact_summary_line(tool_name, args, result, cmd)
    if summary:
        sum_color = t("error") if (result is not None and result.status != "ok") else t("info")
        lines = summary.split("\n")
        single = len(lines) == 1
        for i, line in enumerate(lines):
            if single:
                indent = "   "
                prefix = ui.get("symbols.summary_prefix", "⎿  ")
            else:
                indent = "   "
                prefix = ui.get("symbols.tree_last", "└─ ") if i == len(lines) - 1 else ui.get("symbols.tree_branch", "├─ ")
            console.print(Text(f"{indent}{prefix}{line}", style=sum_color))



def show_tool_combined(
    call: tools.ToolCall,
    result: tools.ToolResult,
    subtitle: str = "",
):
    """Render a single unified panel: command args on top, separator, output below."""
    tool_name = call.tool_name
    args = call.args or {}
    cmd = call.command.strip()

    _store_tool(call, result, subtitle=subtitle)
    _show_tool_compact(call, result, cmd, tool_name, args, subtitle=subtitle)
    return

def show_output(result: tools.ToolResult):
    """Legacy wrapper — used when call is not available. Renders output-only panel."""
    _show_tool_compact(None, result, "", result.name, {}, subtitle="")
    return


def render_md_panel(text: str, subtitle: str = "", message_num: int = 0):
    from rich.markdown import Markdown
    from ui.formatting import latex_to_unicode

    from ui.formatting import escape_md_underscores

    _store_assistant(text, subtitle=subtitle, message_num=message_num)
    text = latex_to_unicode(text)
    md = Markdown(escape_md_underscores(text), code_theme="monokai", inline_code_theme="monokai")

    from rich.console import Group as RGroup
    from agent.stream_render import _inline_md
    stripped = text.lstrip("\n").rstrip()
    # Первая строка склеивается с "●". Если она — block-element
    # (заголовок/список/цитата/fence) — рендерим всё как Markdown под header.
    # Inline-разметка (bold/italic/code) в первой строке конвертируется
    # в rich-markup через _inline_md.
    first_nl = stripped.find("\n")
    first_line = stripped if first_nl < 0 else stripped[:first_nl]
    rest = "" if first_nl < 0 else stripped[first_nl + 1:].lstrip("\n")
    is_block = bool(re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|~~~)", first_line))
    header = Text()
    header.append("● ", style=f"bold {t('success')}")
    if first_line and not is_block:
        header.append(Text.from_markup(_inline_md(first_line)))
        if not rest:
            return header
        rest_md = Markdown(escape_md_underscores(rest), code_theme="monokai", inline_code_theme="monokai")
        return RGroup(header, rest_md)
    return RGroup(header, md)


# Subagent display — вынесено в agent/subagent_display.py
from agent.subagent_display import (  # noqa: E402, F401
    show_subagent_start,
    show_subagent_status,
    show_subagent_done,
)
