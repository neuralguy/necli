"""Привязки основного интерактивного prompt."""

from unittest.mock import Mock

from prompt_toolkit.keys import Keys

from ui.prompt import InputPrompt


def _binding(keys):
    prompt = InputPrompt.__new__(InputPrompt)
    return next(b for b in prompt._make_bindings().bindings if b.keys == keys)


def test_empty_enter_submits_empty_input():
    event = Mock()
    event.current_buffer.complete_state = None
    event.current_buffer.document.current_line_before_cursor = ""

    _binding((Keys.Enter,)).handler(event)

    event.current_buffer.validate_and_handle.assert_called_once_with()


def test_ctrl_c_clears_input_without_aborting_prompt():
    event = Mock()

    _binding((Keys.ControlC,)).handler(event)

    event.current_buffer.reset.assert_called_once_with()
