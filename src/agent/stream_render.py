"""Рендеринг элементов для стриминга: спиннеры, индикаторы, partial tool, live-группы."""

import json
import re

from rich.console import Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

import shutil

from agent.display import (
    TOOL_DISPLAY,
    SPINNER_FRAMES,
)
from config.themes import t
from config.i18n import t as _i18n
from config.ui import ui

THINKING_STYLE = "dim italic"

THINKING_FRAMES = SPINNER_FRAMES
WRITING_FRAMES = SPINNER_FRAMES


def make_thinking_indicator(spinner_frame: str, model: str) -> Text:
    txt = Text()
    txt.append(f"  {spinner_frame} ", style=f"bold {t('accent')}")
    txt.append(model, style=f"bold {t('accent')}")
    from config.i18n import t as _i18n
    suffix = ui.get("indicators.thinking_suffix", f"{_i18n('ui.thinking')}…")
    txt.append(f"  {suffix}", style=THINKING_STYLE)
    return txt


def make_writing_indicator(spinner_frame: str, tool_name: str) -> Text:
    _, color = TOOL_DISPLAY.get(tool_name, ("Tool", t("warning")))
    txt = Text()
    txt.append(f"  {spinner_frame} ", style=f"bold {color}")
    prefix = ui.get("indicators.writing_prefix", "writing ")
    suffix = ui.get("indicators.writing_suffix", "…")
    txt.append(f"{prefix}{tool_name}{suffix}", style="dim")
    return txt


def _inline_md(text: str) -> str:
    """Convert inline markdown (`code`, **bold**, *italic*) to rich markup."""
    from rich.markup import escape as _esc
    out = _esc(text)
    out = re.sub(r"\*\*([^*\n]+?)\*\*", r"[bold]\1[/bold]", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"[italic]\1[/italic]", out)
    out = re.sub(r"`([^`\n]+?)`", r"[cyan]\1[/cyan]", out)
    return out


def _build_markdown(display_text: str, streaming: bool = True):
    from ui.formatting import escape_md_underscores
    suffix = "\u258c" if streaming else ""
    display_text = escape_md_underscores(display_text)
    try:
        return Markdown(display_text + suffix, code_theme="monokai", inline_code_theme="monokai")
    except Exception:
        return Text(display_text + suffix)


def _stream_max_lines() -> int:
    """Сколько строк контента влезает в Live-панель Response без скролла."""
    term_h = shutil.get_terminal_size((80, 24)).lines
    reserved = int(ui.get("live_stream.reserved_lines", 14))
    min_visible = int(ui.get("live_stream.min_visible_lines", 10))
    return max(term_h - reserved, min_visible)


def render_streaming_response(text: str, message_num: int = 0, streaming: bool = True):
    text = text.strip()
    if not text:
        header = Text()
        header.append("● ", style=f"bold {t('success')}")
        header.append("\u258c", style=t("success"))
        return header

    lines = text.split("\n")
    total = len(lines)
    max_lines = _stream_max_lines()
    if total > max_lines:
        # Для live-стрима показываем только хвост БЕЗ префикса
        # "... N lines above ..." — он сам занимает строку и при stop()
        # остаётся видимым обрывком над финальной полной панелью.
        # Счётчик строк (total) уже показывается в title как "(N lines)".
        display_text = "\n".join(lines[-max_lines:])
    else:
        display_text = text

    from ui.formatting import latex_to_unicode
    display_text = latex_to_unicode(display_text)

    md = _build_markdown(display_text, streaming=streaming)

    stripped = display_text.lstrip("\n").rstrip()
    first_nl = stripped.find("\n")
    first_line = stripped if first_nl < 0 else stripped[:first_nl]
    rest = "" if first_nl < 0 else stripped[first_nl + 1:].lstrip("\n")
    if streaming:
        first_line = first_line.rstrip("\u258c").rstrip()
    header = Text()
    header.append("● ", style=f"bold {t('success')}")
    if total > max_lines:
        header.append(_i18n("stream.lines_count", n=total) + " ", style="dim")
    is_block = bool(re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|~~~)", first_line))
    if first_line and not is_block:
        header.append(Text.from_markup(_inline_md(first_line)))
        if not rest:
            return header
        rest_md = _build_markdown(rest, streaming=streaming)
        return Group(header, rest_md)
    return Group(header, md)


def _compact_stream_block(text):
    """Show only tail that fits terminal, with skipped-lines indicator."""
    lines = text.split("\n")
    total = len(lines)
    term_h = shutil.get_terminal_size((80, 24)).lines
    max_visible = max(term_h - 8, 10)
    if total <= max_visible:
        return text
    skipped = total - max_visible
    return (
        _i18n("stream.lines_above", n=skipped) + "\n\n"
        + "\n".join(lines[-max_visible:])
    )


def _extract_path_from_body(body):
    m = re.search(r'"path"\s*:\s*"([^"]+)"', body)
    return m.group(1) if m else None


_PATCH_SECTION_RE = re.compile(
    r"^[ \t]*---[ \t]+(FIND|REPLACE|INSERT)[ \t]+---[ \t]*$",
    re.MULTILINE,
)


def _lang_from_path(path: str | None) -> str:
    if not path:
        return "text"
    ext_m = re.search(r'\.(\w+)$', path)
    if not ext_m:
        return "text"
    from agent.syntax import _EXT_LEXER_MAP
    return _EXT_LEXER_MAP.get(ext_m.group(1).lower(), "text")


def _format_patch_body_for_stream(body: str, attrs_header: str):
    """Превращает FIND/REPLACE/INSERT-секции в diff-подобный текст для стрима.

    Возвращает (display_text, file_path, lang). Незакрытые секции в конце
    стрима показываются как есть — продолжат расти при доп. чанках.
    """
    file_path = None
    m_path = re.search(r'path\s*=\s*"([^"]+)"', attrs_header or "")
    if m_path:
        file_path = m_path.group(1)
    lang = _lang_from_path(file_path)

    matches = list(_PATCH_SECTION_RE.finditer(body))
    if not matches:
        return body, file_path, lang

    out_lines: list[str] = []
    for i, m in enumerate(matches):
        kind = m.group(1)
        start = m.end()
        if start < len(body) and body[start] == "\n":
            start += 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].rstrip("\n")
        pref_del = ui.get("diff_colors.prefix_delete", "- ")
        pref_add = ui.get("diff_colors.prefix_add", "+ ")
        prefix = {"FIND": pref_del, "REPLACE": pref_add, "INSERT": pref_add}[kind]
        for ln in content.split("\n"):
            out_lines.append(prefix + ln)
        if kind == "REPLACE" and i + 1 < len(matches):
            out_lines.append("")

    return "\n".join(out_lines), file_path, "diff"


def _decode_json_body_for_display(body, tool_name, attrs_header: str = ""):
    """Decode body для отображения partial-tool панели.

    - shell: as-is, lang=bash
    - patch_file: FIND/REPLACE → diff-подобный текст
    - write_file/create_file/create_docx: вытаскиваем content (JSON или из attrs)
    - JSON-инструменты: декодируем строки внутри как plain text
    """
    if tool_name == "shell":
        return body, None, "bash"

    file_path = None
    if attrs_header:
        m_path = re.search(r'path\s*=\s*"([^"]+)"', attrs_header)
        if m_path:
            file_path = m_path.group(1)
    if file_path is None:
        file_path = _extract_path_from_body(body)

    if tool_name == "patch_file":
        return _format_patch_body_for_stream(body, attrs_header)

    if tool_name in ("write_file", "create_file", "create_docx"):
        if attrs_header and "path=" in attrs_header:
            return body, file_path, _lang_from_path(file_path)
        m = re.search(r'"content"\s*:\s*"', body)
        if m:
            raw = body[m.end():]
            decoded = raw.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
            return decoded, file_path, _lang_from_path(file_path)

    stripped = body.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        decoded = body.replace('\\n', '\n').replace('\\t', '\t').replace('\\"', '"').replace('\\\\', '\\')
        return decoded, file_path, "text"
    return body, file_path, "text"


def _PARTIAL_LANG_BYPASS_THRESHOLD() -> int:
    return int(ui.get("limits.partial_lang_bypass_threshold", 50_000))
_PARTIAL_SYNTAX_CACHE: dict = {}
_PARTIAL_SYNTAX_CACHE_MAX = 4


def _build_partial_syntax(code: str, lang: str, cache_token):
    """Build Syntax with a tiny LRU keyed by cache_token.

    Pygments highlight на 50K+ символов лагает Live при refresh_per_second=8.
    Кэш переиспользует ОДИН Syntax-объект между тиками, пока буфер не вырос.
    """
    cached = _PARTIAL_SYNTAX_CACHE.get(cache_token)
    if cached is not None:
        return cached
    from agent.display import is_compact
    bg = "default" if is_compact() else t("bg_code")
    pad = (0, 0) if is_compact() else (0, 2)
    syn = Syntax(
        code, lang, theme="monokai", line_numbers=False,
        padding=pad, background_color=bg, word_wrap=True,
    )
    if len(_PARTIAL_SYNTAX_CACHE) >= _PARTIAL_SYNTAX_CACHE_MAX:
        _PARTIAL_SYNTAX_CACHE.pop(next(iter(_PARTIAL_SYNTAX_CACHE)))
    _PARTIAL_SYNTAX_CACHE[cache_token] = syn
    return syn


def _render_compact_write_preview(
    tool_name: str, file_path: str | None, display_text: str,
    elapsed_seconds: float, spinner_frame: str,
):
    """Compact-стрим для write_file/create_file/create_docx — формат как в финале.

    ✨ Create(path)  N.Ns
        ... N lines
        1 line one
        2 line two
        ...
    """
    from agent.syntax import _EXT_LEXER_MAP
    from agent.display import COMPACT_PREVIEW_LINES
    cpl = COMPACT_PREVIEW_LINES() if callable(COMPACT_PREVIEW_LINES) else COMPACT_PREVIEW_LINES

    display_name, color = TOOL_DISPLAY.get(tool_name, ("Tool", t("warning")))
    raw_lines = display_text.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]
    total = len(raw_lines)

    header = Text()
    if spinner_frame:
        header.append(f"{spinner_frame} ", style=f"bold {color}")
    name_only = display_name
    header.append(name_only, style=f"bold {color}")
    if file_path:
        header.append("(", style=f"bold {color}")
        header.append(file_path, style=f"bold {color}")
        header.append(")", style=f"bold {color}")
    if elapsed_seconds > 0:
        header.append(f"  {elapsed_seconds:.1f}s", style="dim")

    parts = [header]

    if total == 0:
        return Group(*parts)

    ext_m = re.search(r"\.(\w+)$", file_path or "")
    lexer = _EXT_LEXER_MAP.get(ext_m.group(1).lower(), "text") if ext_m else "text"

    tail_n = cpl
    tail_lines = raw_lines[-tail_n:] if total > tail_n else raw_lines
    start_idx = total - len(tail_lines) + 1
    num_w = len(str(total))

    parts.append(Text(f"  ... {total} lines", style="dim"))

    for offset, ln in enumerate(tail_lines):
        i = start_idx + offset
        num = Text(f"  {str(i).rjust(num_w)} ", style="dim")
        try:
            code = Syntax(
                ln or " ", lexer, theme="monokai", line_numbers=False,
                padding=(0, 0), background_color="default", word_wrap=False,
            ).highlight(ln or " ")
            if code.plain.endswith("\n"):
                code.right_crop(1)
            parts.append(num + code)
        except Exception:
            parts.append(num + Text(ln))

    return Group(*parts)


def _render_compact_patch_preview(
    file_path: str | None, body: str, attrs_header: str,
    elapsed_seconds: float, spinner_frame: str,
):
    """Compact-стрим для patch_file — финальный формат diff'а с минусами/плюсами."""
    import tools as _tools
    from agent.display import _compact_patch_preview

    display_name, color = TOOL_DISPLAY.get("patch_file", ("Tool", t("warning")))

    header = Text()
    if spinner_frame:
        header.append(f"{spinner_frame} ", style=f"bold {color}")
    header.append(display_name, style=f"bold {color}")
    if file_path:
        header.append("(", style=f"bold {color}")
        header.append(file_path, style=f"bold {color}")
        header.append(")", style=f"bold {color}")
    if elapsed_seconds > 0:
        header.append(f"  {elapsed_seconds:.1f}s", style="dim")

    # Парсим текущие секции из частичного body
    matches = list(_PATCH_SECTION_RE.finditer(body))
    sections: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        kind = m.group(1)
        start = m.end()
        if start < len(body) and body[start] == "\n":
            start += 1
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        content = body[start:end].rstrip("\n")
        sections.append((kind, content))

    args: dict = {"path": file_path or ""}
    pairs = []
    pending_find = None
    insert_section = None
    for kind, content in sections:
        if kind == "INSERT":
            insert_section = content
            break
        if kind == "FIND":
            pending_find = content
        elif kind == "REPLACE" and pending_find is not None:
            pairs.append({"find": pending_find, "replace": content})
            pending_find = None
    # незакрытая пара FIND без REPLACE — покажем как чистый минус
    if pending_find is not None:
        pairs.append({"find": pending_find, "replace": ""})

    if insert_section is not None:
        args["insert"] = insert_section
    elif len(pairs) == 1:
        args["find"] = pairs[0]["find"]
        args["replace"] = pairs[0]["replace"]
    elif len(pairs) > 1:
        args["patches"] = pairs
    else:
        # ни одной секции — показываем только заголовок
        return Group(header)

    fake_result = _tools.ToolResult(
        name="patch_file", status="ok", output="", exit_code=0, command="patch_file",
    )
    preview = _compact_patch_preview(args, fake_result)
    return Group(header, *preview)


def _shorten_subagent_text(value: object, limit: int = 72) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _extract_subagent_tasks_for_stream(body: str) -> list[dict]:
    stripped = (body or "").strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        tasks = []
        starts = [m.start() for m in re.finditer(r'"prompt"\s*:', stripped)]
        for i, start in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else len(stripped)
            chunk = stripped[start:end]
            task: dict[str, object] = {}
            for key in ("role", "mode", "model", "preset", "label", "phase", "prompt"):
                m = re.search(rf'"{key}"\s*:\s*"((?:\\.|[^"\\])*)', chunk)
                if m:
                    task[key] = m.group(1).replace(r"\"", '"').replace(r"\n", " ")
            m_dep = re.search(r'"depends_on"\s*:\s*\[([^\]]*)', chunk)
            if m_dep:
                nums = re.findall(r"\d+", m_dep.group(1))
                if nums:
                    task["depends_on"] = [int(n) for n in nums]
            tasks.append(task)
        return tasks
    if isinstance(data, dict):
        raw_tasks = data.get("tasks")
        if isinstance(raw_tasks, list):
            return [t for t in raw_tasks if isinstance(t, dict)]
        if isinstance(data.get("prompt"), str):
            return [data]
    return []


def _render_subagent_partial_preview(body: str, elapsed_seconds: float, spinner_frame: str):
    tasks = _extract_subagent_tasks_for_stream(body)
    total = len(tasks)
    title_color = TOOL_DISPLAY.get("subagent", ("Subagent", t("magenta")))[1]

    header = Text()
    if spinner_frame:
        header.append(f"{spinner_frame} ", style=f"bold {title_color}")
    header.append("Subagent", style=f"bold {title_color}")
    if total:
        header.append(f" · {total} agent{'s' if total != 1 else ''}", style="dim")
    else:
        header.append(" · preparing…", style="dim")
    if elapsed_seconds > 0:
        header.append(f" · {elapsed_seconds:.1f}s", style="dim")

    rows: list[Text] = [header]
    if not tasks:
        rows.append(Text("  waiting for task list…", style="dim"))
    else:
        max_rows = 8
        for idx, task in enumerate(tasks[:max_rows], start=1):
            role = _shorten_subagent_text(task.get("role") or task.get("preset") or "agent", 18)
            mode = _shorten_subagent_text(task.get("mode") or "agent", 10)
            model = _shorten_subagent_text(task.get("model") or "", 18)
            label = _shorten_subagent_text(task.get("label") or task.get("phase") or task.get("prompt"), 52)
            deps = task.get("depends_on")
            dep_text = ""
            if isinstance(deps, list) and deps:
                dep_text = " · ↳ " + ",".join(str(d) for d in deps[:4])
            row = Text()
            row.append(f"  {idx:>2}. ", style="dim")
            row.append(role, style=t("success") if role else "dim")
            row.append(f" · {mode}", style=t("purple"))
            if model:
                row.append(f" · {model}", style="dim")
            row.append(dep_text, style="dim")
            if label:
                row.append(f"  — {label}", style=t("dim_text"))
            rows.append(row)
        if total > max_rows:
            rows.append(Text(f"      … +{total - max_rows} agents", style=f"italic {t('dim_text')}"))

    return Panel(
        Group(*rows),
        border_style=title_color,
        padding=(0, 1),
        width=max(40, min(int(ui.get("subagent.max_width", 100)), shutil.get_terminal_size((80, 24)).columns)),
    )


def render_partial_tool(body, tool_name, spinner_frame="", attrs_header="", elapsed_seconds: float = 0.0):
    """Панель partial tool — показывает decoded content с dynamic tail compact.

    Performance: для гигантских content (>50K chars) подсветка отключается;
    Syntax кэшируется между тиками Live по (tool_name, len(body)).
    """
    # think-блок рендерится отдельной thinking-line в live_group, тут не
    # дублируем generic-панель с сырым JSON {"thought": "..."}.
    if tool_name == "think":
        return None
    if tool_name == "subagent":
        return _render_subagent_partial_preview(body, elapsed_seconds, spinner_frame)
    _, color = TOOL_DISPLAY.get(tool_name, ("Tool", "yellow"))
    display_name, _ = TOOL_DISPLAY.get(tool_name, ("Tool", "yellow"))

    display_text, file_path, lang = _decode_json_body_for_display(body, tool_name, attrs_header)

    # Стрим для write/create/create_docx — тот же формат, что и в финале
    if tool_name in ("write_file", "create_file", "create_docx"):
        return _render_compact_write_preview(
            tool_name, file_path, display_text, elapsed_seconds, spinner_frame,
        )

    # Стрим для patch_file — тот же diff-формат, что и в финале
    if tool_name == "patch_file":
        return _render_compact_patch_preview(
            file_path, body, attrs_header, elapsed_seconds, spinner_frame,
        )

    lines = display_text.split("\n")
    total_lines = len(lines)

    if len(display_text) > _PARTIAL_LANG_BYPASS_THRESHOLD():
        lang = "text"

    cursor = ui.get("symbols.cursor", "\u258c")
    compact = _compact_stream_block(display_text)
    if tool_name == "shell" and "\n" not in display_text:
        code = f"$ {compact}{cursor}"
    else:
        code = compact + cursor

    shell_title = TOOL_DISPLAY.get("shell", ("\u23fa Shell", t("warning")))[0]
    title_name = shell_title if tool_name == "shell" else display_name
    title_color = "yellow" if tool_name == "shell" else color

    syntax = _build_partial_syntax(code, lang, (tool_name, len(body)))

    path_info = f" {file_path}" if file_path else ""
    term_h = shutil.get_terminal_size((80, 24)).lines
    max_visible = max(term_h - 8, 10)
    line_info = f" \u2014 {total_lines} lines" if total_lines > max_visible else ""

    header = Text()
    if spinner_frame:
        header.append(f"{spinner_frame} ", style=f"bold {title_color}")
    else:
        header.append("● ", style=f"bold {title_color}")
    header.append(title_name, style=f"bold {title_color}")
    if path_info:
        header.append(path_info, style="dim")
    if line_info:
        header.append(line_info, style="dim")
    return Group(header, Text(ui.get("symbols.compact_separator_prefix", "└─"), style="dim"), syntax)


def render_reasoning_panel(text: str, streaming: bool = False):
    """Панель с реальными мыслями ИИ (reasoning_content) — формат think-блока с пометкой raw."""
    from ui.formatting import latex_to_unicode
    from agent.display import is_compact, _w
    from rich.panel import Panel

    muted = t("dim_text")
    emoji = ui.get("symbols.thinking_emoji", "💭")
    label = _i18n("ui.thinking") + " (raw)"

    full = latex_to_unicode(text.strip())
    if not full:
        return Text("")

    if streaming:
        # Низкий стабильный кадр в Live, иначе transient не стирается.
        lines = full.split("\n")
        max_lines = int(ui.get("limits.think_stream_lines", 6))
        if len(lines) > max_lines:
            full = "\n".join(lines[-max_lines:])
        full = full + "\u258c"

    if is_compact():
        from rich.console import Group as RGroup
        header = Text()
        header.append(f"{emoji} {label}", style="bold magenta")
        prefix = ui.get("symbols.summary_prefix", "⎿  ")

        try:
            import os as _os
            term_w = _os.get_terminal_size().columns
        except Exception:
            term_w = 80
        avail = max(20, term_w - 6)

        words = full.replace("\n", " ").split(" ")
        all_lines: list[str] = []
        cur = ""
        for w in words:
            cand = (cur + " " + w).strip() if cur else w
            if len(cand) <= avail:
                cur = cand
            else:
                if cur:
                    all_lines.append(cur)
                cur = w
        if cur:
            all_lines.append(cur)

        out: list = [header]
        for i, ln in enumerate(all_lines):
            pad = f"   {prefix}" if i == 0 else "      "
            line = Text(pad, style=muted)
            line.append(ln, style=f"italic {muted}")
            out.append(line)
        return RGroup(*out)

    body = Text(full, style=f"italic {muted}")
    title = f"[bold magenta]{emoji} {label}[/bold magenta]"
    return Panel(
        body,
        title=title,
        title_align="left",
        border_style="magenta",
        padding=tuple(ui.get("paddings.think_panel", [0, 2])),
        width=_w(),
    )


def render_live_group(
    current_text: str,
    has_partial: bool,
    partial_body: str,
    partial_tool: str,
    spinner_frame: str,
    writing_frame: str,
    model: str,
    message_num: int = 0,
    reasoning_text: str = "",
    reasoning_done: bool = False,
    think_log=None,
    partial_thought: str | None = None,
    partial_attrs: str = "",
    response_streaming: bool = True,
    partial_elapsed: float = 0.0,
) -> Group:
    parts = []

    if think_log is not None:
        if partial_thought:
            # Во время стрима показываем полноценную розовую рамку thinking
            # со стримящейся мыслью внутри (а не компактную одну строку),
            # чтобы пользователь видел полный текст рассуждения сразу.
            from agent.think import ThinkLog, ThoughtStep, render_think_static
            tmp_log = ThinkLog(steps=list(think_log.steps) + [
                ThoughtStep(text=partial_thought + "\u258c"),
            ])
            parts.append(render_think_static(tmp_log, streaming=True))
        elif think_log.total > 0:
            from agent.think import render_think_static
            parts.append(render_think_static(think_log, streaming=True))

    if reasoning_text and reasoning_text.strip():
        if parts:
            parts.append(Text(""))
        parts.append(render_reasoning_panel(reasoning_text, streaming=not reasoning_done))

    if current_text and current_text.strip():
        if parts:
            parts.append(Text(""))
        parts.append(render_streaming_response(
            current_text,
            message_num=message_num,
            streaming=response_streaming,
        ))

    if has_partial and partial_tool == "think":
        # think уже отображается через think_log.render_line выше с partial-текстом
        # (parse_partial_thought на text-буфере). Generic panel НЕ нужна.
        pass
    elif has_partial and partial_body:
        partial_panel = render_partial_tool(
            partial_body, partial_tool,
            spinner_frame=writing_frame, attrs_header=partial_attrs,
            elapsed_seconds=partial_elapsed,
        )
        if partial_panel is not None:
            parts.append(Text(""))
            parts.append(partial_panel)
    elif has_partial:
        parts.append(Text(""))
        parts.append(make_writing_indicator(writing_frame, partial_tool))

    if not parts:
        parts.append(make_thinking_indicator(spinner_frame, model))

    return Group(*parts)


def make_interrupt_indicator(dots: int = 1) -> Text:
    t = Text()
    marker = ui.get("symbols.interrupt_marker", "■ ")
    base_text = ui.get("indicators.interrupt_text", "Waiting for response")
    t.append(f"  {marker}", style="bold yellow")
    t.append(f"{base_text}{'.' * dots}", style="dim yellow")
    t.append(f"  {ui.get('indicators.interrupt_hint', '(Ctrl+C = stop)')}", style="dim")
    return t
