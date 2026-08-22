"""Terminal-cell-aware text measurement and soft wrapping."""

from __future__ import annotations

from prompt_toolkit.layout.processors import (
    Processor,
    Transformation,
    TransformationInput,
)
from prompt_toolkit.layout.utils import explode_text_fragments
from wcwidth import wcswidth


def visible_width(s: str) -> int:
    """Ширина строки в ячейках терминала (эмодзи и CJK занимают две)."""
    n = wcswidth(s)
    return n if n >= 0 else len(s)


def clip_visible(s: str, width: int, tail: str = "…") -> str:
    """Обрезать plain-текст по ячейкам терминала, включая emoji/CJK."""
    if width <= 0:
        return ""
    if visible_width(s) <= width:
        return s
    limit = max(0, width - visible_width(tail))
    out: list[str] = []
    used = 0
    for char in s:
        char_width = max(0, wcswidth(char))
        if used + char_width > limit:
            break
        out.append(char)
        used += char_width
    return "".join(out) + tail


def _word_wrap_padding(chars: list[str], width: int) -> dict[int, int]:
    """Сколько display-пробелов нужно вставить перед каждым словом."""
    width = max(1, width)
    padding: dict[int, int] = {}
    col = 0
    for index, char in enumerate(chars):
        if char and not char.isspace() and (index == 0 or chars[index - 1].isspace()):
            end = index
            while end < len(chars) and not chars[end].isspace():
                end += 1
            word_width = visible_width("".join(chars[index:end]))
            if col and word_width <= width and col + word_width > width:
                if width > col:
                    padding[index] = width - col
                col = width
        char_width = visible_width(char)
        if col + char_width > width:
            col = 0
        col += char_width
    return padding


def _word_wrapped_rows(text: str, width: int) -> int:
    chars = list(text)
    padding = _word_wrap_padding(chars, width)
    width = max(1, width)
    rows = 1
    col = 0
    for index, char in enumerate(chars):
        col += padding.get(index, 0)
        char_width = visible_width(char)
        if col + char_width > width:
            rows += 1
            col = 0
        col += char_width
    return rows


class WordWrapProcessor(Processor):
    """Переносит ввод по границам слов, не меняя текст Buffer.

    Штатный ``Window(wrap_lines=True)`` переносит строку ровно по
    ширине окна и режет последнее слово. Здесь перед словом,
    которое не помещается в остаток строки, добавляются только
    визуальные пробелы. Их нет ни в отправляемом тексте, ни в истории.
    """

    def apply_transformation(self, ti: TransformationInput) -> Transformation:
        fragments = explode_text_fragments(ti.fragments)
        chars = [fragment[1] for fragment in fragments]
        width = max(1, ti.width)
        padding_by_index = _word_wrap_padding(chars, width)
        result: list[tuple] = []
        # Позиция исходного display-текста -> позиция с мягкими отступами.
        source_to_display = [0] * (len(chars) + 1)
        display_to_source: list[int] = []
        col = 0

        for index, fragment in enumerate(fragments):
            char = fragment[1]
            source_to_display[index] = len(result)

            padding = padding_by_index.get(index, 0)
            if padding:
                style = fragment[0]
                for _ in range(padding):
                    result.append((style, " "))
                    display_to_source.append(index)
                col = width

            char_width = visible_width(char)
            if col + char_width > width:
                col = 0
            result.append(fragment)
            display_to_source.append(index)
            col += char_width

        source_to_display[len(chars)] = len(result)
        display_to_source.append(len(chars))
        return Transformation(
            result,
            source_to_display=lambda i: source_to_display[min(i, len(chars))],
            display_to_source=lambda i: display_to_source[
                min(i, len(display_to_source) - 1)
            ],
        )
