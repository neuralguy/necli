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

from rich.text import Text

from config import settings as _settings
from config.themes import t as _theme
from config.ui import ui

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
    """Remove Markdown syntax from model thoughts before displaying or storing them."""
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
    # High-water mark строк стрим-окна: достигнув max_lines, кадр больше не
    # сжимается (текст может временно укорачиваться, напр. при закрытии
    # блока мысль чистится от markdown).
    peak_lines: int = 0

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


def _think_duration(log: ThinkLog) -> int:
    """Секунды, потраченные на рассуждение (от первой до последней мысли)."""
    if not log.steps:
        return 0
    span = log.steps[-1].created_at - log.steps[0].created_at
    return max(1, round(span))


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

    if is_compact:
        from rich.console import Group as RGroup
        header = Text()
        header.append(f"{emoji} {label}", style=f"bold {_theme('magenta')}")

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

        # Сохраняем оригинальные \n — каждая строка word-wrap'ится отдельно.
        paragraphs = full_text.split("\n")
        all_lines: list[str] = []
        for para in paragraphs:
            if not para.strip():
                all_lines.append("")
                continue
            words = para.split(" ")
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
            # Стрим: окно растёт от 1 строки до max_lines, дальше — прокрутка
            # хвоста. Достигнув max_lines, кадр больше не сжимается
            # (peak_lines — high-water mark: текст может временно
            # укорачиваться, напр. при закрытии блока мысль чистится от
            # markdown). Лимит НАМЕРЕННО маленький (не высота терминала):
            # живой Live — transient, и кадр близкий к высоте окна он не
            # может стереть курсором → каждый refresh оставляет старый кадр
            # в scrollback («спам пустых строк»).
            max_lines = int(ui.get("limits.think_stream_lines", 6))
            if log.peak_lines >= max_lines or len(all_lines) > max_lines:
                vis_lines = all_lines[-max_lines:]
                if len(vis_lines) < max_lines:
                    vis_lines = [""] * (max_lines - len(vis_lines)) + vis_lines
                log.peak_lines = max(log.peak_lines, max_lines)
            else:
                vis_lines = all_lines
                log.peak_lines = max(log.peak_lines, len(all_lines))
            hidden = 0
        elif is_expanded_preview():
            vis_lines = all_lines
            hidden = 0
        else:
            # Финал: шапка с секундами размышления зелёным через пробел +
            # одна строка «…N строк (ctrl+o развернуть)». Полный текст —
            # по Ctrl+O.
            header.append(" ")
            header.append(
                Text(_i18n("compact.think_seconds", n=_think_duration(log)), style=_theme("success"))
            )
            summary = Text("   " + prefix, style=muted)
            summary.append(_i18n("compact.think_expand", n=len(all_lines)), style="dim italic")
            return RGroup(header, summary)

        out: list = [header]
        for i, ln in enumerate(vis_lines):
            pad = f"   {prefix}" if i == 0 else "      "
            line = Text(pad, style=muted)
            line.append(ln, style=f"italic {muted}")
            out.append(line)
        if hidden > 0:
            out.append(Text("        " + _i18n("compact.think_expand", n=hidden), style="dim italic"))
        return RGroup(*out)
