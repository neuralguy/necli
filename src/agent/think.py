"""Режим рассуждения (think mode).

Модель ведёт пошаговые мысли вслух перед инструментами через
fenced-блоки :::call think ... call:::. Поведение похоже на план, но:
- шаги последовательны, без перескоков;
- статус автоматически: текущий шаг = последний добавленный;
- в UI отображается приглушённым Markdown-блоком без изменения текста.

Формат блока:

    :::call think
    {"thought": "Сначала надо понять структуру парсера..."}
    call:::

Или с явным номером (необязательно):

    :::call think
    {"step": 3, "thought": "..."}
    call:::
"""

import json
import re
import time
from dataclasses import dataclass, field

from rich.cells import cell_len, chop_cells
from rich.text import Text

from config import settings as _settings
from config.display import is_block_full
from config.themes import t as _theme
from config.ui import ui
from ui.text_styles import inline_markdown_text, styled_count_text

# Кэш для _think_enabled: значение читается на каждом chunk LiveStream
# (parse_partial_thought + strip_think_blocks + parse_think_blocks),
# что давало 3+ обращения к settings на тик. Инвалидируется при изменении
# settings (settings.set вызывает invalidate_caches → _SETTINGS_VERSION++).
_THINK_CACHE: tuple[int, bool] | None = None


_MD_LINK_RE = re.compile(r"!?\[([^]]*)\]\([^)]*\)")
_MD_INLINE_RE = re.compile(r"(`+|\*{1,3}|_{1,3}|~~)(.*?)\1")
_MD_LINE_PREFIX_RE = re.compile(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-*+]\s+|\d+[.)]\s+|[-*_]{3,}\s*$)")
_MD_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})[^\n]*$")


def _plain_thought(text: str) -> str:
    """Remove Markdown syntax for the bounded live preview only."""
    lines: list[str] = []
    in_fence = False
    for line in (text or "").splitlines():
        if _MD_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        line = _MD_LINE_PREFIX_RE.sub("", line)
        line = re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s+", "", line)
        line = _MD_LINK_RE.sub(r"\1", line)
        line = _MD_INLINE_RE.sub(r"\2", line)
        lines.append(line)
    return "\n".join(lines).strip()


def _collapsed_thought_source(text: str) -> str:
    """Flatten block Markdown while preserving inline emphasis for compact UI."""
    lines: list[str] = []
    in_fence = False
    for line in (text or "").splitlines():
        if _MD_FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        line = re.sub(r"^\s*[-*+]\s+\[[ xX]\]\s+", "", line)
        line = _MD_LINE_PREFIX_RE.sub("", line)
        if line.strip():
            lines.append(line.strip())
    return " ".join(" ".join(lines).split())


def collapsed_thought_text(text: str, *, base_style: str) -> Text:
    """One-line Rich Text preserving inline markdown emphasis."""
    source = _collapsed_thought_source(text)
    return inline_markdown_text(
        source,
        base_style=base_style,
        code_style=_theme("info"),
    )


def append_expand_hint(target: Text, hint: str, *, base_style: str) -> None:
    """Append an expand hint with its hidden-line count emphasized."""
    match = re.search(r"\d+", hint or "")
    if not match:
        target.append(hint, style=base_style)
        return
    count = int(match.group(0))
    target.append_text(
        styled_count_text(
            hint,
            count,
            base_style=base_style,
            number_style=f"bold {_theme('info')}",
        )
    )


def _settings_version() -> int:
    return getattr(_settings, "_settings_version", 0)


def _think_enabled() -> bool:
    global _THINK_CACHE
    ver = _settings_version()
    cache = _THINK_CACHE
    if cache is not None and cache[0] == ver:
        return cache[1]
    try:
        value = bool(_settings.get("think_enabled", False))
    except Exception:
        value = False
    _THINK_CACHE = (ver, value)
    return value


@dataclass
class ThoughtStep:
    text: str
    raw_text: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass
class ThinkLog:
    steps: list[ThoughtStep] = field(default_factory=list)

    def add(self, text: str) -> None:
        raw_text = text or ""
        text = _plain_thought(raw_text)
        if not text:
            return
        self.steps.append(ThoughtStep(text=text, raw_text=raw_text))

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def current(self) -> ThoughtStep | None:
        return self.steps[-1] if self.steps else None


_THINK_BLOCK_RE = re.compile(
    r":{2,3}call[ \t]+think[^\n]*\n"
    r"(?P<body>.*?)"
    r"(?:\n|^)call:::[ \t]*(?:\n|$)",
    re.DOTALL | re.MULTILINE,
)

_THINK_BLOCK_OPEN_RE = re.compile(
    r":{2,3}call[ \t]+think[^\n]*\n"
    r"(?P<body>.*)\Z",
    re.DOTALL,
)


def _extract_partial_thought(body: str) -> str | None:
    """Из ТЕЛА незакрытого think-блока вытаскивает текущий текст мысли.

    Парсит до первого вхождения "thought"/"text"/"content" в JSON, потом
    декодирует строку посимвольно с поддержкой escape-последовательностей,
    не требуя закрывающей кавычки.
    """
    body = body or ""
    if not body:
        return None

    s = body.lstrip()
    # Если это не JSON (нет ведущей фигурной скобки) — стримим тело как есть.
    if not s.startswith("{"):
        return body if body.strip() else None

    m = re.search(r'"(thought|text|content)"\s*:\s*"', body)
    if not m:
        # Ключ ещё не дошёл в стриме — пока показывать нечего.
        return None

    _SIMPLE_ESC = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "/": "/"}
    i = m.end()
    out: list[str] = []
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == "\\":
            nxt = body[i + 1] if i + 1 < n else ""
            if not nxt:
                # Незаконченная escape-последовательность в хвосте стрима.
                break
            if nxt in _SIMPLE_ESC:
                out.append(_SIMPLE_ESC[nxt])
                i += 2
                continue
            if nxt == "u" and i + 5 < n:
                try:
                    out.append(chr(int(body[i + 2 : i + 6], 16)))
                    i += 6
                    continue
                except ValueError:
                    pass
            out.append(nxt)
            i += 2
            continue
        if ch == '"':
            break
        out.append(ch)
        i += 1
    text = "".join(out)
    return text if text.strip() else None


def parse_partial_thought(text: str) -> str | None:
    """Если в text есть НЕЗАКРЫТЫЙ think-блок в конце — вернуть частичный thought."""
    if not text or not _think_enabled():
        return None
    # Сначала отрезаем все закрытые блоки.
    tail_start = 0
    for m in _THINK_BLOCK_RE.finditer(text):
        tail_start = m.end()
    tail = text[tail_start:]
    om = _THINK_BLOCK_OPEN_RE.search(tail)
    if not om:
        return None
    return _extract_partial_thought(om.group("body"))


def _parse_one(body: str) -> str | None:
    raw_body = body
    if not raw_body.strip():
        return None
    # Пробуем JSON
    try:
        data = json.loads(raw_body)
        if isinstance(data, dict):
            t = data.get("thought") or data.get("text") or data.get("content")
            if isinstance(t, str) and t.strip():
                return t
    except (json.JSONDecodeError, ValueError):
        # Fallback: чиним одинарные кавычки и trailing commas
        try:
            fixed = raw_body.replace("'", '"')
            fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
            data = json.loads(fixed)
            if isinstance(data, dict):
                t = data.get("thought") or data.get("text") or data.get("content")
                if isinstance(t, str) and t.strip():
                    return t
        except (json.JSONDecodeError, ValueError):
            pass
    # Если не JSON — берём всё тело как мысль
    return raw_body


def _think_duration(log: ThinkLog) -> int:
    """Секунды, потраченные на рассуждение (от первой до последней мысли)."""
    if not log.steps:
        return 0
    span = log.steps[-1].created_at - log.steps[0].created_at
    return max(1, round(span))


def _wrapped_lines(text: str, width: int) -> int:
    """Число видимых строк с учётом переносов терминала."""
    total = 0
    for line in (text or "").splitlines() or [""]:
        total += max(1, (len(line) + width - 1) // width)
    return total


def compact_thought_preview(
    text: str,
    prefix: str,
    terminal_width: int,
    available: int,
) -> tuple[str, str | None]:
    """Fit a collapsed thought and its expand hint on one terminal line."""
    from config.i18n import t as _i18n

    payload_width = max(0, terminal_width - 5 - cell_len("   " + prefix))
    if cell_len(text) <= payload_width:
        return text, None

    lines = _wrapped_lines(text, available)
    preview = ""
    suffix = ""
    for _ in range(10):
        suffix = _i18n("compact.think_expand", n=lines)
        preview_width = max(0, payload_width - cell_len(" " + suffix))
        preview = chop_cells(text, preview_width)[0] if preview_width else ""
        updated_lines = _wrapped_lines(text[len(preview) :], available)
        if updated_lines == lines:
            break
        lines = updated_lines

    suffix = _i18n("compact.think_expand", n=lines)
    if cell_len(suffix) > payload_width:
        suffix = chop_cells(suffix, payload_width)[0] if payload_width else ""
        preview = ""
    return preview, suffix


def render_thinking_summary(
    text: str,
    *,
    elapsed: float | int | None = None,
    label: str | None = None,
):
    """Компактная плашка мысли без утечки её содержимого."""
    from rich.console import Group

    from config.i18n import format_duration
    from config.i18n import t as _i18n

    emoji = ui.get("symbols.thinking_emoji", "💭")
    header = Text()
    header.append(
        f"{emoji} {label or _i18n('ui.thinking_stream')}",
        style=f"bold {_theme('magenta')}",
    )
    if elapsed is not None:
        header.append("  ")
        header.append(format_duration(max(1, elapsed)), style=_theme("success"))

    try:
        import os

        terminal_width = os.get_terminal_size().columns
    except OSError:
        terminal_width = 80
    lines = _wrapped_lines(text.strip(), max(20, terminal_width - 6))
    summary = Text(
        "   " + ui.get("symbols.summary_prefix", "⎿  "),
        style=_theme("dim_text"),
    )
    summary.append_text(
        styled_count_text(
            _i18n("compact.think_stream", n=lines),
            lines,
            base_style=f"italic {_theme('dim_text')}",
            number_style=f"bold {_theme('info')}",
        )
    )
    return Group(header, summary)


def parse_think_blocks(text: str) -> list[str]:
    """Извлекает мысли из всех call think блоков."""
    if not text or not _think_enabled():
        return []
    out: list[str] = []
    for m in _THINK_BLOCK_RE.finditer(text):
        thought = _parse_one(m.group("body"))
        if thought:
            out.append(thought)
    return out


def strip_think_blocks(text: str) -> str:
    """Убирает call think блоки из текста."""
    if not text:
        return text or ""
    if not _think_enabled():
        return text
    return _THINK_BLOCK_RE.sub("", text)


def strip_partial_think_block(text: str) -> str:
    """Убирает хвостовой незакрытый call think блок из display-текста."""
    if not text:
        return text or ""
    if not _think_enabled():
        return text
    tail_start = 0
    for m in _THINK_BLOCK_RE.finditer(text):
        tail_start = m.end()
    tail = text[tail_start:]
    om = _THINK_BLOCK_OPEN_RE.search(tail)
    if not om:
        return text
    return text[: tail_start + om.start()]


def render_think_static(
    log: ThinkLog,
    streaming: bool = False,
    elapsed: float | int | None = None,
):
    """Скрывает активную мысль, сохраняя прежний статический рендер."""
    muted = _theme("dim_text")

    from agent.display import is_compact, is_expanded_preview
    from config.i18n import format_duration
    from config.i18n import t as _i18n

    emoji = ui.get("symbols.thinking_emoji", "💭")
    label = _i18n("ui.thinking")

    raw_text = "\n\n".join(step.raw_text or step.text for step in log.steps)
    if streaming:
        return render_thinking_summary(raw_text, elapsed=elapsed)

    if is_compact:
        expanded = is_block_full("think", compact=not is_expanded_preview())
        from rich.console import Group
        from rich.table import Table

        from agent.markdown import ThoughtMarkdown

        header = Text()
        header.append(f"{emoji} {label}", style=f"bold {_theme('magenta')}")
        preview_source = "\n\n".join(step.raw_text or step.text for step in log.steps)
        prefix = ui.get("symbols.summary_prefix", "⎿  ")
        try:
            import os

            terminal_width = os.get_terminal_size().columns
        except OSError:
            terminal_width = 80
        available = max(20, terminal_width - 6)

        if not expanded:
            header.append(" ")
            header.append(
                Text(
                    format_duration(_think_duration(log)),
                    style=_theme("success"),
                )
            )
            formatted = collapsed_thought_text(preview_source, base_style="dim italic")
            preview, expand_hint = compact_thought_preview(
                formatted.plain,
                prefix,
                terminal_width,
                available,
            )
            summary = Text("   " + prefix, style=muted)
            summary.append_text(formatted[: len(preview)])
            if expand_hint is not None:
                if preview:
                    summary.append(" ", style="dim italic")
                append_expand_hint(summary, expand_hint, base_style="dim italic")
            return Group(header, summary)

        body = ThoughtMarkdown(raw_text, style=muted)
        lead = Text("   " + prefix, style=muted)
        content = Table.grid(padding=0, expand=True)
        content.add_column(width=6)
        content.add_column(ratio=1)
        content.add_row(lead, body)
        return Group(header, content)
