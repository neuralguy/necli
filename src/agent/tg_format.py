"""Конвертер Markdown → Telegram-HTML.

Telegram HTML поддерживает ограниченный набор тегов: b/i/u/s/code/pre/a/
blockquote/tg-spoiler. Markdown-ответы модели (заголовки, списки, **bold**,
`code`, fenced-блоки) нужно привести к этому подмножеству, иначе пользователь
видит сырой markdown с символами `*`, `#`, ```` ``` ````.

Стратегия: построчный проход. Fenced-блоки (```...```) → <pre><code>.
Внутри обычных строк inline-markdown заменяется через регексы поверх
html-escaped текста (escape СНАЧАЛА, потом вставляем теги — иначе теги
самого markdown были бы экранированы).
"""

from __future__ import annotations

import html
import re

# Телеграм-теги, которые мы генерируем сами и НЕ должны экранироваться.
_PLACEHOLDER_OPEN = "\x00"
_PLACEHOLDER_CLOSE = "\x01"

_FENCE_RE = re.compile(r"^```([^\n]*)$")

# inline: `code` (защищаем первым, внутри не трогаем bold/italic)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
# **bold** / __bold__
_BOLD_RE = re.compile(r"\*\*([^\*\n]+?)\*\*|__([^_\n]+?)__")
# *italic* / _italic_  (после bold, чтобы не съесть **)
_ITALIC_RE = re.compile(r"(?<![\*\w])\*(\S(?:[^\*\n]*?\S)?)\*(?![\*\w])|(?<![_\w])_(\S(?:[^_\n]*?\S)?)_(?![_\w])")
# ~~strike~~
_STRIKE_RE = re.compile(r"~~([^~\n]+?)~~")
# [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
# Заголовки markdown
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
# Списки -, *, +, 1.
_ULIST_RE = re.compile(r"^(\s*)[-*+]\s+(.*)$")
_OLIST_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
# Горизонтальная линия
_HR_RE = re.compile(r"^\s*([-*_])\1{2,}\s*$")
# blockquote
_QUOTE_RE = re.compile(r"^>\s?(.*)$")


def _esc(text: str) -> str:
    return html.escape(text, quote=False)


def _inline(text: str) -> str:
    """Применяет inline-markdown к строке (text НЕ экранирован на входе)."""
    # 1. Вырезаем `code`-фрагменты в плейсхолдеры, чтобы внутри не сработали bold/italic.
    code_spans: list[str] = []

    def _stash_code(m: re.Match) -> str:
        code_spans.append(m.group(1))
        return f"{_PLACEHOLDER_OPEN}C{len(code_spans) - 1}{_PLACEHOLDER_CLOSE}"

    text = _INLINE_CODE_RE.sub(_stash_code, text)

    # 2. Вырезаем ссылки (текст ссылки может содержать markdown — оставим как есть).
    links: list[tuple[str, str]] = []

    def _stash_link(m: re.Match) -> str:
        links.append((m.group(1), m.group(2)))
        return f"{_PLACEHOLDER_OPEN}L{len(links) - 1}{_PLACEHOLDER_CLOSE}"

    text = _LINK_RE.sub(_stash_link, text)

    # 3. Экранируем весь оставшийся текст.
    text = _esc(text)

    # 4. Применяем форматирование (теги вставляем после escape).
    text = _BOLD_RE.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = _STRIKE_RE.sub(lambda m: f"<s>{m.group(1)}</s>", text)
    text = _ITALIC_RE.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)

    # 5. Возвращаем code-фрагменты (экранируем их содержимое).
    def _restore_code(m: re.Match) -> str:
        idx = int(m.group(1))
        return f"<code>{_esc(code_spans[idx])}</code>"

    text = re.sub(
        f"{_PLACEHOLDER_OPEN}C(\\d+){_PLACEHOLDER_CLOSE}", _restore_code, text,
    )

    # 6. Возвращаем ссылки.
    def _restore_link(m: re.Match) -> str:
        idx = int(m.group(1))
        label, url = links[idx]
        return f'<a href="{_esc(url)}">{_esc(label)}</a>'

    text = re.sub(
        f"{_PLACEHOLDER_OPEN}L(\\d+){_PLACEHOLDER_CLOSE}", _restore_link, text,
    )
    return text


def md_to_tg_html(text: str) -> str:
    """Конвертирует markdown в Telegram-совместимый HTML.

    Поддержка: заголовки (→bold), списки (→•/нумерация), **bold**, *italic*,
    `code`, ```fenced``` (→<pre>), [links](url), > blockquote, --- (→линия).
    """
    if not text:
        return ""
    out: list[str] = []
    in_fence = False
    fence_lang = ""
    fence_buf: list[str] = []

    for line in text.split("\n"):
        fence_m = _FENCE_RE.match(line.rstrip())
        if fence_m and not in_fence:
            in_fence = True
            fence_lang = fence_m.group(1).strip()
            fence_buf = []
            continue
        if in_fence:
            if line.rstrip() == "```":
                code = _esc("\n".join(fence_buf))
                if fence_lang:
                    out.append(
                        f'<pre><code class="language-{_esc(fence_lang)}">{code}</code></pre>'
                    )
                else:
                    out.append(f"<pre>{code}</pre>")
                in_fence = False
                fence_lang = ""
                fence_buf = []
            else:
                fence_buf.append(line)
            continue

        if _HR_RE.match(line):
            out.append("➖➖➖➖➖")
            continue

        h = _HEADING_RE.match(line)
        if h:
            level = len(h.group(1))
            content = _inline(h.group(2).strip())
            prefix = "▸ " if level >= 3 else ""
            out.append(f"<b>{prefix}{content}</b>")
            continue

        q = _QUOTE_RE.match(line)
        if q:
            out.append(f"<blockquote>{_inline(q.group(1))}</blockquote>")
            continue

        ul = _ULIST_RE.match(line)
        if ul:
            indent = "  " * (len(ul.group(1)) // 2)
            out.append(f"{indent}• {_inline(ul.group(2))}")
            continue

        ol = _OLIST_RE.match(line)
        if ol:
            indent = "  " * (len(ol.group(1)) // 2)
            out.append(f"{indent}{ol.group(2)}. {_inline(ol.group(3))}")
            continue

        out.append(_inline(line))

    # Незакрытый fence в конце (стрим оборвался) — отдаём как pre.
    if in_fence and fence_buf:
        out.append(f"<pre>{_esc(chr(10).join(fence_buf))}</pre>")

    return "\n".join(out)
