"""agent/block_stream.py — BlockStreamer: дубль ответа при повторном update после finalize."""

import io

from rich.console import Console

from agent.block_stream import BlockStreamer


def _capture():
    buf = io.StringIO()
    return Console(file=buf, width=80, force_terminal=False), buf


class TestNoDoublePrint:
    def test_update_after_finalize_is_noop(self):
        """Повторный update тем же буфером после finalize НЕ должен перепечатывать.

        Это корень бага дублирования ответа: stop() → _compact_feed_blocks()
        вызывает update() после того, как блок уже финализирован. finalize()
        раньше сбрасывал _printed_blocks=0, и весь текст печатался заново.
        """
        console, buf = _capture()
        s = BlockStreamer(console)
        text = "# Head\n\npar one\n\npar two"
        s.update(text)
        s.finalize()
        out_after_first = buf.getvalue()

        # Сценарий из stream.py:stop() — повторный feed того же буфера.
        s.update(text)
        s.finalize()
        out_after_second = buf.getvalue()

        # Ничего нового не напечатано — второй раунд молчит.
        assert out_after_first == out_after_second
        # "par one" встречается ровно один раз (не задвоено).
        assert out_after_first.count("par one") == 1

    def test_finalize_idempotent(self):
        console, buf = _capture()
        s = BlockStreamer(console)
        s.update("# A\n\np1")
        s.finalize()
        snap = buf.getvalue()
        s.finalize()
        assert buf.getvalue() == snap

    def test_reset_opens_new_page(self):
        """После reset() (новая страница за tool-блоком) стрим снова печатает."""
        console, buf = _capture()
        s = BlockStreamer(console)
        s.update("# A\n\np1")
        s.finalize()
        s.reset()
        s.update("# B\n\np2")
        s.finalize()
        out = buf.getvalue()
        assert out.count("p1") == 1
        assert out.count("p2") == 1
