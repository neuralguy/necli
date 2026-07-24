from ui.prompt import InputPrompt


class Buffer:
    def __init__(self):
        self.text = ""

    def insert_text(self, text):
        self.text += text


def test_multiline_paste_is_displayed_as_marker_and_expanded_on_send():
    prompt = InputPrompt()
    buffer = Buffer()

    prompt._insert_pasted_text(buffer, "first\nsecond\nthird")

    assert buffer.text == "[Pasted 3 lines]"
    display_text = buffer.text
    expanded_text = prompt._expand_pasted_text(display_text)

    assert expanded_text == "first\nsecond\nthird"
    assert display_text == "[Pasted 3 lines]"


def test_multiline_paste_is_expanded_for_history_without_changing_buffer():
    prompt = InputPrompt()
    buffer = Buffer()
    prompt._insert_pasted_text(buffer, "first\nsecond")

    stored_text = prompt._expand_for_history(buffer.text)

    assert buffer.text == "[Pasted 2 lines]"
    assert stored_text == "first\nsecond"
    assert prompt._submitted_text == "first\nsecond"


def test_single_line_paste_is_not_collapsed():
    prompt = InputPrompt()
    buffer = Buffer()

    prompt._insert_pasted_text(buffer, "single line")

    assert buffer.text == "single line"
    assert prompt._expand_pasted_text(buffer.text) == "single line"
