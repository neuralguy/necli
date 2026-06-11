"""Форматирование чисел и визуальные элементы для терминала."""

import re

from pylatexenc.latex2text import LatexNodes2Text

_l2t = LatexNodes2Text()


def _latex_fragment_to_unicode(latex: str) -> str:
    """Convert a single LaTeX fragment (without $ delimiters) to Unicode."""
    try:
        return _l2t.latex_to_text(latex)
    except Exception:
        return latex


_DISPLAY_MATH_RE = re.compile(r'\$\$(.*?)\$\$', re.DOTALL)
_INLINE_MATH_RE = re.compile(r'(?<!\\)\$(.+?)(?<!\\)\$')
_LATEX_BLOCK_RE = re.compile(
    r'\\\[(.*?)\\\]'
    r'|\\\((.*?)\\\)',
    re.DOTALL,
)


_LATEX_FENCE_RE = re.compile(
    r'(?:```|~~~)(?:latex|math)\s*\n(.*?)(?:```|~~~)',
    re.DOTALL | re.IGNORECASE,
)
_BARE_FENCE_MATH_RE = re.compile(
    r'(?:```|~~~)\s*\n(\s*\$\$.*?\$\$\s*)\n(?:```|~~~)',
    re.DOTALL,
)


def latex_to_unicode(text: str) -> str:
    """Replace LaTeX math expressions with Unicode equivalents.

    Handles $...$, $$...$$, \\[...\\], \\(...\\) and ```latex/```math fenced blocks.
    Regular code blocks (```python, ```bash, etc.) are preserved as-is.
    """
    if '$' not in text and '\\(' not in text and '\\[' not in text \
            and not _LATEX_FENCE_RE.search(text):
        return text

    # Convert ```latex / ```math / ~~~latex / ~~~math blocks first
    def _replace_fence(m: re.Match) -> str:
        return _latex_fragment_to_unicode(m.group(1).strip())

    result = _LATEX_FENCE_RE.sub(_replace_fence, text)

    # Unwrap bare ``` / ~~~ fences containing only $$...$$ math
    def _unwrap_bare_fence(m: re.Match) -> str:
        return m.group(1).strip()

    result = _BARE_FENCE_MATH_RE.sub(_unwrap_bare_fence, result)

    # Protect remaining code blocks from conversion
    code_blocks: list[str] = []
    def _save_code(m: re.Match) -> str:
        code_blocks.append(m.group(0))
        return f'\x00CODE{len(code_blocks) - 1}\x00'

    protected = re.sub(r'(?:```|~~~).*?(?:```|~~~)', _save_code, result, flags=re.DOTALL)
    protected = re.sub(r'`[^`]+`', _save_code, protected)

    def _replace_match(m: re.Match) -> str:
        latex = (m.group(1) or m.group(2)) if (m.lastindex and m.lastindex >= 2) else m.group(1)
        if latex is None:
            return m.group(0)
        return _latex_fragment_to_unicode(latex.strip())

    def _replace_dollar(m: re.Match) -> str:
        return _latex_fragment_to_unicode(m.group(1).strip())

    # Order matters: display math before inline
    protected = _DISPLAY_MATH_RE.sub(_replace_dollar, protected)
    protected = _INLINE_MATH_RE.sub(_replace_dollar, protected)
    protected = _LATEX_BLOCK_RE.sub(_replace_match, protected)

    # Restore code blocks
    for i, block in enumerate(code_blocks):
        protected = protected.replace(f'\x00CODE{i}\x00', block)

    return protected


def escape_md_underscores(text: str) -> str:
    """Экранирует одиночные `_` вне code-блоков, чтобы имена вроде
    `mcp__server__tool` или `do_something` не превращались в курсив при
    рендере через rich.markdown.Markdown.

    Защищает содержимое ```/~~~ fenced-блоков и `inline code` — там
    подчёркивания не трогаем.
    """
    if "_" not in text:
        return text

    saved: list[str] = []

    def _save(m: re.Match) -> str:
        saved.append(m.group(0))
        return f"\x00U{len(saved) - 1}\x00"

    protected = re.sub(r"(?:```|~~~).*?(?:```|~~~)", _save, text, flags=re.DOTALL)
    protected = re.sub(r"`[^`\n]+`", _save, protected)

    # Экранируем только `_` ВНУТРИ слов (буква/цифра с обеих сторон) —
    # это идентификаторы вроде do_something / mcp__srv__tool. Структурные
    # подчёркивания (HR `___`, разделители, начало/конец строки) не трогаем,
    # чтобы не ломать блочную разметку.
    protected = re.sub(r"(?<=\w)_(?=\w)", "\\_", protected)

    for i, block in enumerate(saved):
        protected = protected.replace(f"\x00U{i}\x00", block)

    return protected


def format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return "\u2014"
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m {s}s"


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f}K"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}M"
    return f"{size / (1024 * 1024 * 1024):.1f}G"

def format_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}K"
    return f"{n / 1_000_000:.2f}M"


def format_cost(cost: float) -> str:
    if cost < 0.001:
        return f"${cost:.6f}"
    if cost < 1:
        return f"${cost:.4f}"
    return f"${cost:.2f}"


# Маркеры для цветного бара в prompt_toolkit
BAR_FILLED_START = "\x00BF\x00"
BAR_FILLED_END = "\x00BE\x00"
BAR_EMPTY_START = "\x00ES\x00"
BAR_EMPTY_END = "\x00EE\x00"


def progress_bar(current: int, total: int, width: int = 10) -> str:
    """Сегментированный прогресс-бар с маркерами для цветного отображения."""
    if total <= 0:
        ratio = 0.0
    else:
        ratio = min(current / total, 1.0)
    filled = int(width * ratio)
    empty = width - filled
    filled_str = "▮" * filled
    empty_str = "▯" * empty
    return (
        BAR_FILLED_START + filled_str + BAR_FILLED_END
        + BAR_EMPTY_START + empty_str + BAR_EMPTY_END
    )
