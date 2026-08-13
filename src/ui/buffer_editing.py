"""Shared keyboard editing primitives for prompt-toolkit buffers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from prompt_toolkit.buffer import Buffer

if TYPE_CHECKING:
    from .shell import Shell


def edit_buffer_key(buf: Buffer, key: str) -> bool:
    """Readline-подобные операции редактирования произвольного Buffer."""
    if key == "left":
        buf.cursor_left()
    elif key == "right":
        buf.cursor_right()
    elif key == "up":
        buf.auto_up()
    elif key == "down":
        buf.auto_down()
    elif key == "home":
        buf.cursor_position += buf.document.get_start_of_line_position()
    elif key == "end":
        buf.cursor_position += buf.document.get_end_of_line_position()
    elif key == "c-left":
        offset = buf.document.find_previous_word_beginning(WORD=True)
        buf.cursor_position += offset if offset is not None else -buf.cursor_position
    elif key == "c-right":
        offset = buf.document.find_next_word_beginning(WORD=True)
        if offset is None:
            offset = len(buf.text) - buf.cursor_position
        buf.cursor_position += offset
    elif key == "c-a":
        buf.cursor_position += buf.document.get_start_of_line_position()
    elif key == "c-e":
        buf.cursor_position += buf.document.get_end_of_line_position()
    elif key == "backspace":
        buf.delete_before_cursor(1)
    elif key == "delete":
        buf.delete(1)
    elif key == "c-delete":
        # Ctrl+Delete — удалить слово ПОСЛЕ курсора (как emacs kill-word),
        # симметрично Alt+Backspace, который стирает слово перед курсором.
        offset = buf.document.find_next_word_beginning(WORD=True)
        if offset is None:
            offset = len(buf.text) - buf.cursor_position
        buf.delete(offset)
    elif key in {"a-backspace", "c-backspace"}:
        # Alt+Backspace — удалить слово перед курсором вместе с пробелами
        # (как readline backward-kill-word).
        offset = buf.document.find_start_of_previous_word(WORD=True)
        if offset is None:
            offset = -buf.cursor_position
        buf.delete_before_cursor(-offset)
    elif key == "c-w":
        # Ctrl+W — очистить всё левее курсора до начала строки.
        buf.delete_before_cursor(-buf.document.get_start_of_line_position())
    elif key == "c-u":
        buf.delete_before_cursor(-buf.document.get_start_of_line_position())
    elif key == "c-k":
        buf.delete(buf.document.get_end_of_line_position())
    elif key == "space":
        buf.insert_text(" ")
    else:
        return False
    return True


def _default_buffer_key(shell: Shell, key: str, event) -> None:
    """Поведение навигационных клавиш в обычном поле ввода."""
    buf = shell.input_buffer
    # Штатные bindings PromptSession принимали fish-style подсказку истории
    # стрелкой вправо. Собственный eager-handler Shell перекрыл их и оставил
    # только cursor_right(), который в конце строки ничего не делает.
    if (
        key == "right"
        and buf.suggestion is not None
        and buf.suggestion.text
        and buf.document.is_cursor_at_the_end
    ):
        buf.insert_text(buf.suggestion.text)
        return
    edit_buffer_key(buf, key)
