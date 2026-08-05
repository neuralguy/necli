"""Terminal rendering of tool commands and their output."""

import asyncio
import json
import re

from rich.console import Console, Group
from rich.syntax import Syntax
from rich.text import Text

import tools
from agent.syntax import _EXT_LEXER_MAP
from config.i18n import t as _i18n
from config.themes import t
from config.ui import ui
from tools._html_unescape import unescape_nested as _unescape_for_display

is_compact = True

console = Console()

#: Консоль-перехватчик статики. Ставится только на время Ctrl+O replay: он
#: собирает всю историю в один буфер и отдаёт её Shell'у одной строкой, иначе
#: каждый элемент истории лез бы в терминал своим run_in_terminal и рамка
#: перерисовывалась бы столько раз, сколько в сессии сообщений.
_STATIC_CAPTURE: Console | None = None

# Последний семантический compact-блок в live scrollback. Инструмент сразу
# после текста агента продолжает ход без пустой строки, а соседние инструменты
# должны быть разделены одной пустой строкой. Replay ведёт spacing сам и в это
# состояние не вмешивается.
_LAST_LIVE_COMPACT_KIND: str | None = None


def set_static_capture(target: Console | None) -> None:
    global _STATIC_CAPTURE
    _STATIC_CAPTURE = target


def get_static_capture() -> Console | None:
    return _STATIC_CAPTURE


def mark_compact_assistant_output() -> None:
    """Отметить, что последним live-блоком был текст ответа агента."""
    global _LAST_LIVE_COMPACT_KIND
    if _STATIC_CAPTURE is None:
        _LAST_LIVE_COMPACT_KIND = "assistant"


def _space_compact_tool(parts: list) -> list:
    """Добавить spacer только между двумя соседними live-инструментами."""
    global _LAST_LIVE_COMPACT_KIND
    if _STATIC_CAPTURE is not None:
        return parts
    adjacent_tool = _LAST_LIVE_COMPACT_KIND == "tool"
    _LAST_LIVE_COMPACT_KIND = "tool"
    return [Text(""), *parts] if adjacent_tool else parts


def print_static(renderable) -> None:
    """Единственный канал вывода агента в scrollback.

    Почему не console.print: под patch_stdout Rich пишет в StdoutProxy, а тот
    копит строки в собственной очереди и сбрасывает их отдельным
    run_in_terminal с задержкой. Shell печатает через run_in_terminal сразу —
    два канала перемешивались, и шапка инструмента могла оказаться выше своего
    же результата. Один канал = порядок гарантирован.

    Инструменты выполняются в рабочем потоке (loop.run_in_executor), а
    run_in_terminal требует живого loop'а. Поэтому из потока прыгаем на loop
    через call_soon_threadsafe: прямая запись в stdout попала бы в середину
    кадра Application и порвала рамку.

    Без Shell (headless, -p, не-TTY, тесты) печатаем прежней консолью: она сама
    решает, включать ли ANSI, поэтому вывод в файл остаётся чистым.
    """
    target = _STATIC_CAPTURE
    if target is not None:
        target.print(renderable)
        return
    from ui.shell import get_shell
    shell = get_shell()
    if shell is None:
        console.print(renderable)
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = getattr(getattr(shell, "app", None), "loop", None)
        if loop is not None and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(shell.print_static, renderable)
                return
            except RuntimeError:
                pass
    shell.print_static(renderable)


# Когда True — рендер-функции не пишут в RenderStore, чтобы replay не зациклился.
_REPLAY_ACTIVE = False


def set_replay_active(active: bool) -> None:
    global _REPLAY_ACTIVE
    _REPLAY_ACTIVE = bool(active)


def _render_store():
    """Текущий RenderStore или None (None также при активном replay)."""
    if _REPLAY_ACTIVE:
        return None
    from agent.loop import get_current_ctx
    ctx = get_current_ctx()
    if ctx is None:
        return None
    return getattr(ctx, "render_store", None)


def _store_tool(call, result, subtitle: str = "") -> None:
    try:
        store = _render_store()
        if store is None:
            return
        if result is None:
            store.add_command_only(call, subtitle=subtitle)
        else:
            store.add_tool(call, result, subtitle=subtitle)
    except Exception:
        pass


def _store_command(cmd: str, tool_name: str, args: dict, subtitle: str = "") -> None:
    try:
        store = _render_store()
        if store is None:
            return
        call = tools.ToolCall(command=cmd, tool_name=tool_name, args=dict(args or {}), raw="")
        store.add_command_only(call, subtitle=subtitle)
    except Exception:
        pass


def _store_assistant(text: str, subtitle: str = "", message_num: int = 0) -> None:
    try:
        store = _render_store()
        if store is None:
            return
        store.add_assistant_block(text, subtitle=subtitle, message_num=message_num)
    except Exception:
        pass


def show_plan_update(plan, action: str = "", focus_index: int | None = None) -> None:
    if plan is None or not getattr(plan, "steps", None):
        return
    if focus_index is None:
        focus_index = getattr(plan, "current_step_index", None)
    if focus_index is None:
        focus_index = 0
    try:
        from planner import plan_to_snapshot, render_plan_panel
        # Ведущая пустая строка и панель уходят одним print_static: два вызова
        # означали бы два run_in_terminal, то есть лишний перерисованный кадр
        # рамки между пустой строкой и самой панелью.
        print_static(Group(Text(""), render_plan_panel(
            plan,
            compact=False,
            focus_index=focus_index,
        )))
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

def MAX_WIDTH():  # noqa: N802
    return int(ui.get("limits.max_width", 100))

# Инструменты, у которых при успехе output скрывается: пользователь видит факт
# изменения по панели команды и ✓-статусу, текст не нужен.
_TOOL_TITLE_ARG = {
    "web_fetch": "urls",
    "web_search": "queries",
    "skill": "name",
    "memory_read": "name",
    "memory_write": "name",
    "poll": "question",
    "subagent": "prompt",
    "expand_tool_result": "id",
    "read": "path",
}

_SILENT_OK_TOOLS = frozenset({
    "patch_file",
    "create_docx",
})


def COMPACT_HEAD_LINES():  # noqa: N802
    return int(ui.get("limits.compact_head_lines", 10))
def COMPACT_TAIL_LINES():  # noqa: N802
    return int(ui.get("limits.compact_tail_lines", 10))

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
    def _lookup(self, key):
        return _tool_display_entry(key) or _mcp_display_for(key)
    def get(self, key, default=None):
        entry = self._lookup(key)
        return entry if entry is not None else default
    def __getitem__(self, key):
        entry = self._lookup(key)
        if entry is None:
            raise KeyError(key)
        return entry
    def __contains__(self, key):
        return self._lookup(key) is not None

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
    # lines[-0:] вернул бы ВЕСЬ список (а не пустой хвост) — явно гасим tail==0.
    tail_lines = lines[-tail:] if tail > 0 else []
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
        names: list[str] = []
        for p in path:
            if isinstance(p, dict):
                p = p.get("path", str(p))
            if p:
                names.append(str(p))
        if not names:
            return ""
        return names[0] if len(names) == 1 else f"{len(names)} files"
    if isinstance(path, dict):
        path = path.get("path", str(path))
    return str(path) if path else ""

def _compact_display_value(value: str) -> str:
    """Compact display: head + ... + tail for large text values."""
    if not isinstance(value, str):
        return value
    if _EXPANDED_PREVIEW:
        return value
    return _compact_content(value, COMPACT_HEAD_LINES(), COMPACT_TAIL_LINES())


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


def show_command(
    cmd: str,
    tool_name: str = "shell",
    args: dict | None = None,
    subtitle: str = "",
    *,
    skipped: bool = False,
):
    """Standalone command panel.

    Используется для web_search (нет результата для объединения) и для
    skipped tool calls при soft interrupt — нужно показать пользователю,
    какой именно вызов был пропущен, для любого tool, не только web_search.
    """
    args = args or {}
    _store_command(cmd, tool_name, args, subtitle=subtitle)
    _show_tool_compact(
        None,
        None,
        cmd,
        tool_name,
        args,
        subtitle=subtitle,
        skipped=skipped or "skipped" in subtitle.lower(),
    )


def _file_uri(raw_path: str) -> str:
    """Абсолютный file URI для ссылки и авто-распознавания терминалом."""
    try:
        from tools._paths import resolve_path
        return resolve_path(raw_path).resolve().as_uri()
    except Exception:
        from pathlib import Path
        return Path(raw_path).expanduser().resolve().as_uri()


def _file_link_style(raw_path, base_color: str) -> str:
    """Стиль с корректной OSC 8-ссылкой на локальный файл."""
    if not raw_path or not isinstance(raw_path, str):
        return f"bold {base_color}"
    return f"bold underline {base_color} link {_file_uri(raw_path)}"


def _format_elapsed(elapsed: float) -> str:
    """Строка времени для статуса инструмента, или '' если показывать нечего.

    Скрываем «0.0s»: мгновенные операции (read/list — файл в кеше, мелкое
    исполнение) округляются до 0.0 и выглядят как баг таймера. Печатаем время
    только когда оно не схлопнется в 0.0 (порог 0.05s → ≥0.1s после округления).
    """
    elapsed = elapsed or 0.0
    return f" {elapsed:.1f}s" if elapsed >= 0.05 else ""


def _format_tool_tokens(call: tools.ToolCall | None, result: tools.ToolResult) -> str:
    """Показывает токены, записанные инструментом и возвращённые из него."""
    from session.tokens import count_tokens
    from ui import format_tokens

    read_tools = {
        "read", "grep", "lsp_references",
        "lsp_diagnostics", "web_search", "web_fetch", "image_search",
        "docx_screenshot", "skill", "memory_list", "memory_read",
    }
    write_tools = {"create_file", "patch_file", "create_docx", "memory_write"}
    tool_name = call.tool_name if call else result.name
    if tool_name in read_tools:
        return f" ↑{format_tokens(count_tokens(result.output))}"
    if tool_name in write_tools:
        payload = json.dumps(call.args if call else {}, ensure_ascii=False, default=str)
        return f" ↓{format_tokens(count_tokens(payload))}"

    payload = json.dumps(call.args if call else {}, ensure_ascii=False, default=str)
    return (
        f" ↓{format_tokens(count_tokens(payload))}"
        f" ↑{format_tokens(count_tokens(result.output))}"
    )


def _truncate_cmd(cmd: str) -> str:
    """Однострочная команда — целиком (до 120); многострочная — первая строка + …."""
    if "\n" in cmd:
        first = cmd.split("\n", 1)[0]
        return first[:80] + " …"
    return cmd[:120] + ("…" if len(cmd) > 120 else "")


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
    if not raw_path and tool_name == "read":
        _paths = args.get("paths")
        if _paths:
            raw_path = _paths if isinstance(_paths, (list, tuple)) else str(_paths)
    path_disp = _format_path_for_title(raw_path)
    arg_disp = path_disp
    # Синтетический блок нескольких Read: количество нужно только развёрнутым.
    combined_read_count = args.get("_combined_read_count")
    if tool_name == "read" and combined_read_count:
        arg_disp = f"{combined_read_count} files" if _EXPANDED_PREVIEW else ""
    # Несколько поисковых запросов отображаются как grouped Read: в свёрнутом
    # заголовке не перечисляем их (счётчик будет в summary), а в раскрытом
    # оставляем компактный счётчик — сами запросы идут отдельными строками.
    search_queries = args.get("queries")
    grouped_search = (
        tool_name == "web_search"
        and isinstance(search_queries, list)
        and len(search_queries) > 1
    )
    if grouped_search:
        arg_disp = f"{len(search_queries)} queries" if _EXPANDED_PREVIEW else ""
    if tool_name == "grep" and args.get("pattern"):
        pat = str(args["pattern"])[:60]
        arg_disp = f"{pat} -> {path_disp}" if path_disp else pat
    is_file_path = bool(path_disp) and tool_name != "grep"
    if not arg_disp and not grouped_search:
        _title_arg_key = _TOOL_TITLE_ARG.get(tool_name)
        if _title_arg_key:
            val = args.get(_title_arg_key)
            if isinstance(val, list):
                items = [str(v)[:60] for v in val[:3]]
                arg_disp = ", ".join(items)
                if len(val) > 3:
                    arg_disp += ", …"
            elif val:
                arg_disp = str(val)[:120]
    if not arg_disp and tool_name == "shell":
        cmd = args.get("command", "") or ""
        arg_disp = _truncate_cmd(cmd)
    if arg_disp:
        txt.append(f"{display_name}(", style=f"bold {color}")
        link_path: str | None = None
        if is_file_path:
            if isinstance(raw_path, str):
                link_path = raw_path
            elif path_disp and isinstance(path_disp, str) and tool_name == "read":
                # read paths=[{path: '/foo'}, ...] — raw_path список,
                # но path_disp уже извлёк путь; используем его для линка.
                link_path = path_disp
        if link_path:
            txt.append(arg_disp, style=_file_link_style(link_path, color))
        else:
            # Несколько файлов (list) или не-путь — без линка.
            txt.append(arg_disp, style=f"bold {color}")
        txt.append(")", style=f"bold {color}")
    else:
        txt.append(display_name, style=f"bold {color}")
    if status_icon:
        txt.append("  ")
        txt.append(status_icon, style=status_color)
    return txt


def _compact_summary_line(tool_name: str, args: dict, result: tools.ToolResult | None, cmd: str) -> str:
    """Одна короткая строка-сводка по инструменту для compact-режима."""
    if result is not None and result.status != "ok":
        first_line = (result.output or "").strip().split("\n")[0][:80]
        return first_line or _i18n("compact.error")

    if tool_name in ("web_search", "web_fetch"):
        if result is not None:
            out = (result.output or "").strip()
            if not out:
                return ""
            # web_search: считаем результаты
            n_results = len(re.findall(r"(?m)^\[\d+\] ", out))
            if n_results:
                return _i18n("compact.results_n", n=n_results)
            # web_fetch: первая строка вида "=== URL ===" → показываем URL
            m = re.match(r"^=== (.+?) ===\s*$", out.split("\n", 1)[0])
            if m:
                return m.group(1)[:120]
            return out.split("\n", 1)[0][:80]
        return ""

    if tool_name == "image_search":
        if result is not None:
            out = (result.output or "").strip()
            if not out:
                return ""
            n_queries = len(re.findall(r"(?m)^\[Query \d+:", out))
            if n_queries >= 2 and not _EXPANDED_PREVIEW:
                return _i18n("compact.queries_n", n=n_queries)
        return ""

    if tool_name == "read":
        if result is not None:
            infos: list[str] = []
            for line in (result.output or "").split("\n"):
                s = line.strip()
                # Считаем все [...] строки — и partial, и полные, и директории
                if s.startswith("[") and s.endswith("]"):
                    infos.append(s.strip("[]"))
            if not infos:
                return ""
            # Свёрнутый вид: "N files" одной строкой; развёрнутый — дерево.
            if len(infos) >= 2 and not _EXPANDED_PREVIEW:
                return _i18n("compact.files_n", n=len(infos))
            return "\n".join(infos)
        return ""

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

    if tool_name == "create_file":
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
                if s.startswith(("✓", "⚠", "✗")) or "patch" in s.lower():
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


def COMPACT_PREVIEW_LINES():  # noqa: N802
    return int(ui.get("limits.compact_preview_lines", 8))
def COMPACT_PREVIEW_LINES_SHELL():  # noqa: N802
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

    # create_file — summary "N строк" + нумерованный листинг контента
    if tool_name == "create_file" and isinstance(args.get("content"), str):
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
            num = Text(f"      {str(i).rjust(num_w)} ", style=t("fg_primary"))
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

    # shell — превью вывода. При успехе показываем ПЕРВЫЕ N строк, при падении —
    # ПОСЛЕДНИЕ N: суть ошибки (напр. `ValueError: 42`) почти всегда в конце
    # stderr/traceback, а первые строки — это `[stderr]` + начало стека. Раньше
    # длинный traceback обрезал голову и прятал сам error в «… +M lines».
    if tool_name == "shell" and result is not None:
        output = (result.output or "").rstrip("\n")
        if not output:
            return None
        lines = output.split("\n")
        total = len(lines)
        limit = None if _EXPANDED_PREVIEW else COMPACT_PREVIEW_LINES_SHELL()
        failed = result.status != "ok"
        if limit is None or total <= limit:
            head = lines
            offset = 0
        elif failed:
            head = lines[-limit:]          # хвост — там сам текст ошибки
            offset = total - limit
        else:
            head = lines[:limit]
            offset = 0
        num_w = len(str(total))
        out: list = []
        if offset > 0:
            out.append(Text("        " + _i18n("compact.more_lines", n=offset), style="dim italic"))
        for i, ln in enumerate(head, start=offset + 1):
            num = Text(f"      {str(i).rjust(num_w)} ", style=t("fg_primary"))
            out.append(num + Text(ln))
        if offset == 0 and total > len(head):
            rest = total - len(head)
            out.append(Text("        " + _i18n("compact.more_lines", n=rest), style="dim italic"))
        return out

    # Web search — тот же grouped UX, что у Read: в свёрнутом виде счётчик,
    # в раскрытом — все запросы. Раньше generic summary брала первую строку
    # output и всегда показывала только ``[Query 1: ...]``.
    if tool_name == "web_search" and result is not None and result.status == "ok":
        queries = args.get("queries")
        if isinstance(queries, list):
            queries = [str(query).strip() for query in queries if str(query).strip()]
        else:
            queries = []
        if not queries:
            queries = re.findall(r"(?m)^\[Query \d+: (.*)\]$", result.output or "")
        if len(queries) < 2:
            return None

        indent = "   "
        if not _EXPANDED_PREVIEW:
            return [Text(
                f"{indent}\u23bf {_i18n('compact.queries_n', n=len(queries))}",
                style=f"italic {t('dim_text')}",
            )]

        out: list = []
        for i, query in enumerate(queries):
            prefix = "\u23bf " if i == 0 else "  "
            out.append(Text(f"{indent}{prefix}{query}", style=t("info")))
        return out

    # grep_files / lsp_* — первые 3 результата + "… +N строк"
    if tool_name in (
        "lsp_references", "lsp_diagnostics",
    ) and result is not None and result.status == "ok":
        return _compact_result_list_preview(tool_name, result)

    # image_search — в свёрнутом виде None (сводка через _compact_summary_line),
    # в развёрнутом — полный вывод инструмента.
    if tool_name == "image_search" and result is not None and result.status == "ok":
        if not _EXPANDED_PREVIEW:
            return None
        output = (result.output or "").strip()
        if not output:
            return None
        lines = output.split("\n")
        out: list = []
        for ln in lines:
            out.append(Text(f"   {ln}"))
        return out

    # memory_read/memory_write — метаданные + тело (первые 5 строк)
    if tool_name in ("memory_read", "memory_write") and result is not None and result.status == "ok":
        return _compact_memory_preview(result)

    # Read — кликабельный путь: полный файл без суффикса, диапазон через «·».
    if tool_name == "read" and result is not None:
        out = _compact_read_preview(result, args)
        if out is not None:
            return out

    return None


def _compact_read_preview(result: tools.ToolResult, args: dict | None = None) -> list | None:
    """Превью для комбинированного read блока (несколько файлов).

    При _EXPANDED_PREVIEW показывает кликабельные пути файлов.
    При свёрнутом виде возвращает None — _compact_summary_line покажет «N файлов».
    """
    if not _EXPANDED_PREVIEW:
        return None
    output = (result.output or "").strip()
    if not output:
        return None
    lines = output.split("\n")
    # Одиночное чтение: показываем только путь при полном чтении; у диапазона
    # сохраняем метаданные после «·».
    if args and args.get("path") and not args.get("_combined_read_count"):
        path = str(args["path"])
        suffix = ""
        requested_lines = args.get("lines")
        if requested_lines:
            first = lines[0].strip()
            info = first[1:-1] if first.startswith("[") and first.endswith("]") else ""
            marker = info.find("lines ")
            suffix = info[marker:].strip() if marker >= 0 else str(requested_lines)
        uri = _file_uri(path)
        line = Text(f"   {ui.get('symbols.summary_prefix', '⎿  ')}", style=t("info"))
        line.append(uri, style=_file_link_style(path, "info"))
        if suffix:
            line.append(f" · {suffix}", style=t("info"))
        return [line]

    # Проверяем что все строки — [...] info (комбинированный формат)
    info_texts: list[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("[") and s.endswith("]") and ("·" in s or "directory" in s):
            info_texts.append(s[1:-1])
        else:
            continue  # [path] без суффикса = полное чтение, пропускаем
    if not info_texts:
        return None

    out: list = []
    indent = "   "
    for i, txt in enumerate(info_texts):
        prefix = "\u23bf " if i == 0 else "  "
        # "path · suffix" → разделяем
        sep = txt.rfind("·")
        if sep >= 0:
            path_part = txt[:sep].strip()
            suffix_part = txt[sep + 1:].strip()
        else:
            path_part = txt
            suffix_part = ""
        line = Text()
        line.append(f"{indent}{prefix}", style=t("info"))
        if path_part:
            line.append(_file_uri(path_part), style=_file_link_style(path_part, "info"))
        if suffix_part:
            line.append(f" · {suffix_part}", style=t("info"))
        out.append(line)
    return out if out else None


def _compact_result_list_preview(tool_name: str, result: tools.ToolResult) -> list | None:
    """Превью списка результатов (grep/find/lsp): первые 3 строки + остаток."""
    output = (result.output or "").rstrip("\n")
    if not output:
        return None
    raw = output.split("\n")

    # У grep/lsp_diagnostics первая строка — заголовок-сводка, не результат.
    has_header = tool_name == "lsp_diagnostics"
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
        out.append(Text("      " + ln.strip(), style=t("dim_text")))  # noqa: PERF401
    if len(rows) > len(head):
        rest = len(rows) - len(head)
        out.append(Text(
            "        " + _i18n("compact.more_lines", n=rest),
            style=f"italic {t('dim_text')}",
        ))
    return out


def _compact_memory_preview(result: tools.ToolResult) -> list | None:
    """Превью memory_read: метаданные + тело (первые 5 строк)."""
    output = (result.output or "").rstrip("\n")
    if not output:
        return None

    # Отрезаем строку с путём
    rest = re.sub(r"^=== path: .+? ===\s*", "", output).strip()

    # Парсим мета-строку [scope=..., type=..., created=..., updated=...]
    meta_match = re.match(r"^\[(.+?)\]\s*\n?", rest)
    body = rest
    meta_parts = []
    if meta_match:
        meta_str = meta_match.group(1)
        body = rest[meta_match.end():].strip()
        # Вытаскиваем scope и type, остальное убираем
        for kv in meta_str.split(","):
            kv = kv.strip()
            if kv.startswith(("scope=", "type=")):
                meta_parts.append(kv)

    out: list = []
    if meta_parts:
        out.append(Text(
            f"   {ui.get('symbols.summary_prefix', '⎿  ')}{', '.join(meta_parts)}",
            style=t("info"),
        ))

    # Показываем первые 5 строк тела
    body_lines = body.split("\n")
    limit = 5
    head = body_lines[:limit]
    out.extend(Text(f"      {ln}", style=t("dim_text")) for ln in head)
    if len(body_lines) > limit:
        rest_n = len(body_lines) - limit
        out.append(Text(
            "      " + _i18n("compact.more_lines", n=rest_n),
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

    bg_del = ui.get("diff_colors.bg_delete") or t("diff_del_bg")
    bg_add = ui.get("diff_colors.bg_add") or t("diff_add_bg")
    fg_del = ui.get("diff_colors.fg_delete") or t("diff_del_fg")
    fg_add = ui.get("diff_colors.fg_add") or t("diff_add_fg")
    pref_del = ui.get("diff_colors.prefix_delete", "- ")
    pref_add = ui.get("diff_colors.prefix_add", "+ ")

    term_w = console.width
    # layout: "      NN " + sign(2) + body — фон тянется на всю ширину строки.
    prefix_w = 6 + num_w + 1
    body_w = max(8, term_w - prefix_w - len(pref_del) - 2)

    def _emit(rows: list[tuple[int, str]], sign: str, fg: str, bg: str) -> None:
        for num_val, text_ln in rows:
            num_str = str(num_val).rjust(num_w)
            prefix = Text(f"      {num_str} ", style=t("fg_primary"))
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
    *,
    skipped: bool = False,
):
    """Компактный режим: заголовок Tool(path) ✓ 1.2s + preview/сводка."""
    raw_args = args or {}
    # memory_read/memory_write: inject file path from result into raw_args for title
    if tool_name in ("memory_read", "memory_write") and result is not None:
        _out = result.output or ""
        _pm = re.search(r"^=== path: (.+?) ===\s*", _out)
        if _pm:
            raw_args = dict(raw_args)
            raw_args["path"] = _pm.group(1)
    args = prepare_display_args(raw_args, tool_name)

    is_ok = True
    icon = ""
    status_color = "green"
    if skipped:
        icon = "■ skipped (interrupted)"
        status_color = t("warning")
    elif result is not None:
        is_ok = result.status == "ok"
        if is_ok:
            icon = "✓"
        elif result.exit_code == -1:
            icon = "✗"
        else:
            icon = f"✗ exit {result.exit_code}"
        status_color = "green" if is_ok else "red"

    elapsed = (result.elapsed if result else 0.0) or 0.0
    time_str = _format_elapsed(elapsed)
    if skipped:
        status_full = icon
    else:
        status_full = f"{icon}{time_str}{_format_tool_tokens(call, result)}" if icon else ""

    # Весь блок инструмента печатается ОДНИМ print_static. Построчная печать
    # давала бы по run_in_terminal на строку: рамка снималась и возвращалась
    # десять раз на один результат, и между строками мог вклиниться чужой вывод.
    # После текста агента инструмент идёт без пустой строки. Если предыдущим
    # видимым блоком тоже был инструмент, _space_compact_tool добавит ровно
    # один separator, чтобы соседние вызовы не слипались.
    parts: list = _space_compact_tool([
        _compact_title_text(tool_name, args, status_full, status_color),
    ])

    # Сначала пробуем богатое превью контента (только если успех).
    # Используем НЕурезанные raw_args — _compact_preview_content сам ограничивает
    # количество строк через _preview_limit().
    if result is None or result.status == "ok":
        preview = _compact_preview_content(tool_name, raw_args, result)
        if preview:
            parts.extend(preview)
            print_static(Group(*parts))
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
            parts.append(Text(f"{indent}{prefix}{line}", style=sum_color))
    print_static(Group(*parts))



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


def show_read_combined(pairs: list[tuple[tools.ToolCall, tools.ToolResult]]) -> None:
    """Сгруппировать несколько read результатов в один компактный блок.

    Вызывается из executor.execute_and_show, когда в одном вызове агент
    читает несколько файлов подряд — вместо N отдельных блоков показываем
    один заголовок и список файлов.

    По умолчанию блок свёрнут — показывает только «… N файлов (ctrl+o развернуть)».
    При Ctrl+O (replay) раскрывается в список файлов с кликабельными путями.
    """
    if not pairs:
        return

    n = len(pairs)
    all_ok = all(r.status == "ok" for _, r in pairs)
    icon = "✓" if all_ok else "✗"
    status_color = "green" if all_ok else "red"

    total_elapsed = sum((r.elapsed or 0.0) for _, r in pairs)
    time_str = _format_elapsed(total_elapsed)

    from session.tokens import count_tokens
    from ui import format_tokens
    total_tk = sum(count_tokens(r.output) for _, r in pairs)

    # Один список является источником истины и для счётчика, и для обоих видов.
    info_items: list[tuple[str, str]] = []  # (path, suffix)
    all_lines: list[str] = []
    for call, result in pairs:
        path = (call.args or {}).get("path", "") or ""
        item = _format_read_info(call, result)
        if item is not None:
            path, suffix = item
        else:
            suffix = "read"
        info_items.append((path, suffix))
        all_lines.append(f"[{path} · {suffix}]")
    combined_output = "\n".join(all_lines)

    # Синтетический вызов — без path/paths в args, чтобы заголовок был просто «Read»
    combined_call = tools.ToolCall(
        command="read",
        tool_name="read",
        args={"_combined_read_count": n},
        raw="",
    )
    combined_result = tools.ToolResult(
        name="read",
        status="ok" if all_ok else "error",
        output=combined_output,
        exit_code=0,
        command="read",
        elapsed=total_elapsed,
    )

    # Сохраняем один combined entry в RenderStore (для Ctrl+O replay)
    _store_tool(combined_call, combined_result)

    # Рендер заголовка — без скобок (paths в теле блока). Блок целиком уходит
    # одним print_static: см. _show_tool_compact.
    parts: list = _space_compact_tool([_compact_title_text(
        "read", {"_combined_read_count": n},
        f"{icon}{time_str} ↑{format_tokens(total_tk)}",
        status_color,
    )])

    if not info_items:
        print_static(Group(*parts))
        return

    if _EXPANDED_PREVIEW:
        # Раскрытый вид — кликабельные file:// URI
        indent = "   "
        for i, (path, suffix) in enumerate(info_items):
            prefix = "\u23bf " if i == 0 else "  "
            line = Text()
            line.append(f"{indent}{prefix}", style=t("info"))
            line.append(_file_uri(path), style=_file_link_style(path, "info"))
            line.append(f" · {suffix}", style=t("info"))
            parts.append(line)
    else:
        # Свёрнутый вид — "N файлов (ctrl+o развернуть)"
        indent = "   "
        parts.append(Text(
            f"{indent}\u23bf {_i18n('compact.files_n', n=n)}",
            style=f"italic {t('dim_text')}",
        ))
    print_static(Group(*parts))


def _format_read_info(call: tools.ToolCall, result: tools.ToolResult) -> tuple[str, str] | None:
    """Форматирует информацию о прочитанном файле.

    Возвращает (path, suffix) или None, если файл был прочитан полностью
    (без указания диапазона) — в этом случае строка не нужна.
    suffix — например «lines 1-10 of 50» или «directory».
    """
    out = (result.output or "").strip()
    if not out:
        return None

    first = out.split("\n", 1)[0].strip()
    if not first.startswith("[") or not first.endswith("]"):
        return None

    info = first[1:-1]  # убираем скобки
    if not info:
        return None

    path = (call.args or {}).get("path", "") or ""

    # Директория — всегда показываем
    if "directory" in info:
        return (path, "directory")

    # Показываем только partial-чтения (с диапазоном строк) и truncated
    is_partial = False
    if "showing first" in info:
        is_partial = True
    elif "lines " in info and " of " in info:
        # Проверяем что диапазон НЕ покрывает весь файл
        m = __import__("re").search(r"lines (\d+)-(\d+) of (\d+)", info)
        if m:
            start, end, total = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not (start == 1 and end == total):
                is_partial = True
    if not is_partial:
        return None

    # Извлекаем суффикс (диапазон/статус), отбрасывая путь
    suffix = info
    if "·" in info:
        suffix = info.split("·", 1)[-1].strip()
    else:
        li = info.find("lines")
        if li >= 0:
            suffix = info[li:].strip()
    return (path, suffix)


def show_output(result: tools.ToolResult):
    """Legacy wrapper — used when call is not available. Renders output-only panel."""
    _show_tool_compact(None, result, "", result.name, {}, subtitle="")


def render_md_panel(text: str, subtitle: str = "", message_num: int = 0):
    from agent.markdown import ResponseMarkdown
    from ui.formatting import escape_md_underscores, latex_to_unicode

    _store_assistant(text, subtitle=subtitle, message_num=message_num)
    text = latex_to_unicode(text)
    md = ResponseMarkdown(escape_md_underscores(text), code_theme="monokai", inline_code_theme="monokai")

    from rich.console import Group as RGroup

    from agent.stream_render import _inline_md, _is_markdown_block
    stripped = text.lstrip("\n").rstrip()
    # Первая строка склеивается с "●". Если она — block-element
    # (заголовок/список/цитата/fence) — рендерим всё как Markdown под header.
    # Inline-разметка (bold/italic/code) в первой строке конвертируется
    # в rich-markup через _inline_md.
    first_nl = stripped.find("\n")
    first_line = stripped if first_nl < 0 else stripped[:first_nl]
    rest = "" if first_nl < 0 else stripped[first_nl + 1:].lstrip("\n")
    is_block = _is_markdown_block(first_line, rest)
    header = Text()
    header.append("● ", style=f"bold {t('success')}")
    if first_line and not is_block:
        header.append(Text.from_markup(_inline_md(first_line)))
        if not rest:
            return header
        rest_md = ResponseMarkdown(escape_md_underscores(rest), code_theme="monokai", inline_code_theme="monokai")
        return RGroup(header, rest_md)
    return RGroup(header, md)


# Subagent display — вынесено в agent/subagent_display.py
from agent.subagent_display import (  # noqa: E402, F401
    show_subagent_done,
    show_subagent_start,
    show_subagent_status,
)
