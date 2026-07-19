"""Режим рассуждения (think mode).

Модель ведёт пошаговые мысли вслух перед инструментами через
fenced-блоки :::call think ... call:::. Поведение похоже на план, но:
- шаги последовательны, без перескоков;
- статус автоматически: текущий шаг = последний добавленный;
- в UI отображается одной компактной строкой "▶ N/M текст шага".

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

from rich.panel import Panel
from rich.text import Text

from agent.display import _w
from config import settings as _settings
from config.themes import t as _theme
from config.ui import ui

# Кэш для _think_enabled: значение читается на каждом chunk LiveStream
# (parse_partial_thought + strip_think_blocks + has_think_blocks + parse_think_blocks),
# что давало 4+ обращения к settings на тик. Инвалидируется при изменении
# settings (settings.set вызывает invalidate_caches → _SETTINGS_VERSION++).
_THINK_CACHE: tuple[int, bool] | None = None


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
    created_at: float = field(default_factory=time.time)


@dataclass
class ThinkLog:
    steps: list[ThoughtStep] = field(default_factory=list)

    def add(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.steps.append(ThoughtStep(text=text))

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def current(self) -> ThoughtStep | None:
        return self.steps[-1] if self.steps else None

    def render_line(self, partial: str | None = None, streaming: bool = False) -> Text:
        """Компактная одна строка: '💭 N текст…'.

        partial — частичный текст текущей (ещё не закрытой) мысли. Если задан,
        отображается вместо последней закрытой и счётчик показывает следующий
        номер. streaming=True добавляет курсор и dim-стиль.
        """
        if partial is not None:
            snippet = partial.replace("\n", " ").strip()
            shown_num = self.total + 1
        else:
            cur = self.current
            if not cur:
                return Text()
            snippet = cur.text.replace("\n", " ").strip()
            shown_num = self.total
        max_len = int(ui.get("limits.think_snippet_max_len", 140))
        if len(snippet) > max_len:
            snippet = snippet[: max_len - 1] + ui.get("symbols.ellipsis", "…")
        emoji = ui.get("symbols.thinking_emoji", "💭")
        accent = _theme("purple")
        muted = _theme("dim_text")
        t = Text()
        t.append(f"  {emoji} ", style=f"bold {accent}")
        t.append(f"{shown_num}", style=f"bold {accent}")
        t.append("  ", style=muted)
        style = f"italic dim {muted}" if streaming else f"italic {muted}"
        t.append(snippet, style=style)
        if streaming:
            t.append(ui.get("symbols.cursor", "▌"), style=accent)
        return t


_THINK_BLOCK_RE = re.compile(
    r':{2,3}call[ \t]+think[^\n]*\n'
    r'(?P<body>.*?)'
    r'(?:\n|^)call:::[ \t]*(?:\n|$)',
    re.DOTALL | re.MULTILINE,
)

_THINK_BLOCK_OPEN_RE = re.compile(
    r':{2,3}call[ \t]+think[^\n]*\n'
    r'(?P<body>.*)\Z',
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
        return body.strip() or None

    m = re.search(r'"(thought|text|content)"\s*:\s*"', body)
    if not m:
        # Ключ ещё не дошёл в стриме — пока показывать нечего.
        return None

    _SIMPLE_ESC = {'n': '\n', 't': '\t', 'r': '\r', '"': '"', '\\': '\\', '/': '/'}  # noqa: N806
    i = m.end()
    out: list[str] = []
    n = len(body)
    while i < n:
        ch = body[i]
        if ch == '\\':
            nxt = body[i + 1] if i + 1 < n else ''
            if not nxt:
                # Незаконченная escape-последовательность в хвосте стрима.
                break
            if nxt in _SIMPLE_ESC:
                out.append(_SIMPLE_ESC[nxt])
                i += 2
                continue
            if nxt == 'u' and i + 5 < n:
                try:
                    out.append(chr(int(body[i + 2:i + 6], 16)))
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
    text = "".join(out).strip()
    return text or None


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
    body = body.strip()
    if not body:
        return None
    # Пробуем JSON
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            t = data.get("thought") or data.get("text") or data.get("content")
            if isinstance(t, str) and t.strip():
                return t.strip()
    except (json.JSONDecodeError, ValueError):
        # Fallback: чиним одинарные кавычки и trailing commas
        try:
            fixed = body.replace("'", '"')
            fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
            data = json.loads(fixed)
            if isinstance(data, dict):
                t = data.get("thought") or data.get("text") or data.get("content")
                if isinstance(t, str) and t.strip():
                    return t.strip()
        except (json.JSONDecodeError, ValueError):
            pass
    # Если не JSON — берём всё тело как мысль
    return body if body else None


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


def has_think_blocks(text: str) -> bool:
    if not text or not _think_enabled():
        return False
    return bool(_THINK_BLOCK_RE.search(text))


def render_think_static(log: ThinkLog, streaming: bool = False):
    """Список мыслей.

    streaming=True (во время стрима) — разворачивается ПОЛНОСТЬЮ.
    streaming=False (финал) — превью 3 строки + футер 'ctrl+o развернуть',
    либо целиком если развёрнуто через Ctrl+O (is_expanded_preview).
    """
    muted = _theme("dim_text")

    from agent.display import is_compact, is_expanded_preview
    from config.i18n import t as _i18n
    emoji = ui.get("symbols.thinking_emoji", "💭")
    label = _i18n("ui.thinking")

    if is_compact():
        from rich.console import Group as RGroup
        header = Text()
        header.append(f"{emoji} {label}", style="bold magenta")

        full_text = "\n".join(
            ln for ln in "\n\n".join(step.text.strip() for step in log.steps).split("\n")
            if ln.strip()
        )

        prefix = ui.get("symbols.summary_prefix", "⎿  ")

        # Визуальные строки с учётом переноса по ширине терминала (одна длинная
        # мысль без \n иначе считалась бы одной строкой и не резалась).
        try:
            import os as _os
            term_w = _os.get_terminal_size().columns
        except Exception:
            term_w = 80
        avail = max(20, term_w - 6)  # отступ "      "

        words = full_text.replace("\n", " ").split(" ")
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

        if streaming:
            # Стрим: прокручиваем — показываем небольшой стабильный ХВОСТ.
            # Лимит НАМЕРЕННО маленький (не высота терминала): живой Live —
            # transient, и кадр близкий к высоте окна он не может стереть
            # курсором → каждый refresh оставляет старый кадр в scrollback
            # («спам пустых строк»). Низкий стабильный кадр перерисовывается
            # на месте.
            max_lines = int(ui.get("limits.think_stream_lines", 6))
            vis_lines = all_lines[-max_lines:] if len(all_lines) > max_lines else all_lines
            hidden = 0
        elif is_expanded_preview():
            vis_lines = all_lines
            hidden = 0
        else:
            max_lines = int(ui.get("limits.think_preview_lines", 3))
            vis_lines = all_lines[:max_lines]
            hidden = len(all_lines) - len(vis_lines)

        out: list = [header]
        for i, ln in enumerate(vis_lines):
            pad = f"   {prefix}" if i == 0 else "      "
            line = Text(pad, style=muted)
            line.append(ln, style=f"italic {muted}")
            out.append(line)
        if hidden > 0:
            out.append(Text("        " + _i18n("compact.think_expand", n=hidden), style="dim italic"))
        return RGroup(*out)

    full = "\n".join(step.text.strip() for step in log.steps)
    if streaming:
        # Стрим (non-compact): небольшой стабильный хвост (не высота терминала)
        # — иначе transient-Live с кадром ≈ высоте окна не стирается и плодит
        # пустые строки в scrollback.
        lines = full.split("\n")
        max_lines = int(ui.get("limits.think_stream_lines", 6))
        if len(lines) > max_lines:
            full = "\n".join(lines[-max_lines:])
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
