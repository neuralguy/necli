"""Поблочный markdown-streamer для compact-режима.

Архитектура (по образцу Claude Code CLI):

1. На каждом обновлении буфера парсим текст в список блоков.
   Блок = paragraph / heading / list / code-fence / blockquote / hr / table.
2. Все блоки КРОМЕ последнего считаются «закрытыми» — за ними есть
   следующий, значит они уже не вырастут. Печатаем их в stdout ОДИН РАЗ
   через console.print(Markdown(...)) — они уходят в scrollback навсегда.
3. Последний блок («активный») держим в маленьком Live, который занимает
   только высоту этого блока. При росте — Live перерисовывается, при
   появлении следующего блока — Live стопается, блок печатается, новый
   Live стартует.
4. Терминал листает естественно: scrollback растёт с каждым закрытым
   блоком, Live в каждый момент занимает 1-10 строк (один блок).

Не пытаемся писать свой парсер inline-markdown — каждый блок рендерится
нативным rich.markdown.Markdown.
"""

import re

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_HR_RE = re.compile(r"^\s*(?:-{3,}|\*{3,}|_{3,})\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+")


def _split_into_blocks(text: str) -> list[str]:
    """Разбивает markdown-текст на список блоков.

    Блок — последовательность строк, не разделённая пустой строкой,
    либо целиком code-fence (от ``` до ``` включительно), либо
    непрерывная таблица.
    """
    lines = text.split("\n")
    blocks: list[list[str]] = []
    current: list[str] = []
    in_fence = False

    def flush():
        nonlocal current
        if current:
            blocks.append(current)
            current = []

    for line in lines:
        if in_fence:
            current.append(line)
            if _FENCE_RE.match(line):
                in_fence = False
                flush()
            continue

        if _FENCE_RE.match(line):
            flush()
            current.append(line)
            in_fence = True
            continue

        if not line.strip():
            flush()
            continue

        # Заголовок и HR — всегда отдельный блок.
        if _HEADING_RE.match(line) or _HR_RE.match(line):
            flush()
            current.append(line)
            flush()
            continue

        current.append(line)

    flush()
    raw = ["\n".join(b) for b in blocks]
    # Соседние блоки-списки, разделённые пустой строкой, склеиваем обратно в
    # один блок: иначе rich.Markdown рендерит каждый пункт отдельным списком и
    # нумерация "1." сбрасывается на каждом пункте.
    merged: list[str] = []
    for b in raw:
        first = b.split("\n", 1)[0]
        prev_last = merged[-1].rsplit("\n", 1)[-1] if merged else ""
        if merged and _LIST_RE.match(first) and _LIST_RE.match(prev_last):
            merged[-1] = merged[-1] + "\n\n" + b
        else:
            merged.append(b)
    return merged


class BlockStreamer:
    """Поблочный стрим markdown.

    Использование:
        s = BlockStreamer(console)
        s.update(full_buffer_v1)   # печатает закрытые блоки, держит активный в Live
        s.update(full_buffer_v2)
        ...
        s.finalize()               # печатает оставшийся активный блок, останавливает Live
    """

    def __init__(self, console: Console, refresh_per_second: int = 8):
        self.console = console
        self._refresh = refresh_per_second
        self._printed_blocks: int = 0       # сколько блоков уже ушло в scrollback
        self._emitted_blocks: list[str] = []  # тексты блоков, уже ушедших в scrollback (по содержимому)
        self._active_text: str = ""          # текст текущего активного блока
        self._live: Live | None = None
        self._done: bool = False             # finalize() вызван — update() игнорируем до reset()
        self._emitted_any: bool = False      # хоть один блок ушёл в scrollback

    def _print_block(self, block_text: str, is_first: bool) -> None:
        """Печатает один блок в scrollback с ровно одной пустой строкой-разделителем.

        Rich Markdown сам добавляет нерегулярные хвостовые пустые строки
        (после списков/таблиц — есть, после параграфов — нет). Захватываем
        вывод, срезаем ведущие/хвостовые пустые строки и сами ставим ровно
        одну пустую строку перед каждым непервым блоком.
        """
        renderable = self._make_renderable(block_text, is_first=is_first)
        # Rich Markdown паддит каждую строку trailing-пробелами до ширины
        # консоли. Строка ровно в ширину терминала вызывает авто-перенос →
        # лишняя пустая строка после блока. Капчурим рендер, rstrip'аем строки
        # и печатаем как Text.from_ansi. Здесь Live НЕ задействован (блок уже
        # закрыт, идёт прямо в scrollback), поэтому подсчёт высоты не ломается.
        from rich.text import Text
        with self.console.capture() as cap:
            self.console.print(renderable)
        body = "\n".join(ln.rstrip() for ln in cap.get().strip("\n").split("\n"))
        if not body:
            return
        if self._emitted_any:
            self.console.print()
        self.console.print(Text.from_ansi(body))
        self._emitted_any = True

    def _make_renderable(self, block_text: str, is_first: bool = False):
        from rich.text import Text
        if not block_text or not block_text.strip():
            return Text("")

        def _md(txt):
            from ui.formatting import escape_md_underscores, latex_to_unicode
            txt = escape_md_underscores(latex_to_unicode(txt))
            try:
                return Markdown(txt, code_theme="monokai", inline_code_theme="monokai")
            except Exception:
                return Text(txt)

        if is_first:
            from rich.console import Group

            from agent.stream_render import _inline_md
            from config.themes import t
            from ui.formatting import latex_to_unicode
            block_text = latex_to_unicode(block_text)
            stripped = block_text.lstrip("\n").rstrip()
            first_nl = stripped.find("\n")
            first_line = stripped if first_nl < 0 else stripped[:first_nl]
            rest = "" if first_nl < 0 else stripped[first_nl + 1:].lstrip("\n")
            is_block = bool(re.match(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|>\s|```|~~~)", first_line))
            header = Text()
            header.append("● ", style=f"bold {t('success')}")
            if first_line and not is_block:
                header.append(Text.from_markup(_inline_md(first_line)))
                if not rest:
                    return header
                return Group(header, _md(rest))
            return Group(header, _md(block_text))
        return _md(block_text)

    def _start_live(self):
        if self._live is not None:
            return
        if not self._active_text:
            return
        try:
            from config.ui import ui
            if not bool(ui.get("live_stream.compact_active_live", False)):
                return
        except Exception:
            return
        self._live = Live(
            console=self.console,
            refresh_per_second=self._refresh,
            transient=True,  # стираем кадр перед финализацией — её мы делаем сами через console.print.
            get_renderable=self._live_renderable,
            auto_refresh=True,
        )
        self._live.start()

    def _tail_active(self, text: str) -> str:
        """Обрезает активный блок до высоты терминала ДЛЯ ЖИВОГО КАДРА.

        Только для Live-превью: если блок (длинный абзац / незакрытый
        code-fence) выше терминала, Rich Live не может перерисоваться на месте
        — кадр уезжает вверх и плодит пустые строки. В scrollback и при
        finalize() блок печатается целиком (там обрезки нет).
        """
        if not text:
            return text
        from agent.stream_render import _stream_max_lines
        max_lines = _stream_max_lines()
        # Высота кадра считается по ВИЗУАЛЬНЫМ строкам с учётом word-wrap, а не
        # по числу \n: длинный абзац без переносов в одну логическую строку при
        # переносе по ширине терминала занимает много экранных строк. Если
        # кадр выше видимой области, transient-Live не может стереть прошлый
        # кадр (он уехал за верх экрана) и каждый refresh печатает новую копию
        # ниже — отсюда дубли. Поэтому обрезаем по реальной экранной высоте.
        width = max(1, self.console.width)
        lines = text.split("\n")
        visual = 0
        for ln in lines:
            visual += max(1, (len(ln) + width - 1) // width)
        if visual <= max_lines:
            return text
        # Берём хвост логических строк, пока их визуальная высота не превысит
        # лимит. Для единственной сверхдлинной строки — режем её по символам.
        tail: list[str] = []
        acc = 0
        for ln in reversed(lines):
            h = max(1, (len(ln) + width - 1) // width)
            if acc + h > max_lines and tail:
                break
            tail.append(ln)
            acc += h
        tail.reverse()
        result = "\n".join(tail)
        if acc > max_lines and len(tail) == 1:
            # Одна строка всё ещё выше экрана — оставляем последние max_lines
            # экранных строк этой строки (по символам).
            keep_chars = max_lines * width
            result = result[-keep_chars:]
        return result

    def _live_renderable(self):
        """Кадр активного блока для Live с ведущей пустой строкой-разделителем."""
        active = self._tail_active(self._active_text)
        inner = self._make_renderable(active, is_first=(self._printed_blocks == 0))
        if not self._emitted_any:
            return inner
        from rich.console import Group
        from rich.text import Text
        return Group(Text(""), inner)

    def _stop_live(self):
        if self._live is None:
            return
        try:
            self._live.stop()
        except Exception:
            from logger import logger
            logger.debug("block_stream: Live.stop() failed", exc_info=True)
        self._live = None

    def update(self, full_text: str) -> None:
        """Принимает полный накопленный буфер (НЕ дельту)."""
        if self._done:
            # После finalize() поток считается закрытым. Повторный update()
            # с тем же буфером (приходит из stop()→_compact_feed_blocks)
            # перепечатал бы всё заново — это и есть дубль ответа. Игнорируем
            # до явного reset() (новая «страница» после tool-блока).
            return
        blocks = _split_into_blocks(full_text)
        if not blocks:
            return

        # Все блоки кроме последнего считаются закрытыми. Если у нас появились
        # новые закрытые блоки — нужно сначала остановить Live (он стирает
        # активный кадр), потом напечатать в scrollback ВСЕ ещё не выведенные
        # закрытые блоки, потом стартануть новый Live с новым активным блоком.
        total = len(blocks)
        closed = blocks[:total - 1]  # последний — активный
        # Печатаем закрытые блоки ПО СОДЕРЖИМОМУ, а не по индексу: разбиение
        # _split_into_blocks между тиками может сдвигать границы (склейка
        # списков в merged, дозревающие переносы), из-за чего индекс closed_count
        # «дрожит» и уже напечатанный блок печатался бы заново. Сверяемся с
        # фактически выведенными текстами — дубль исключён.
        new_closed = closed[len(self._emitted_blocks):]
        if new_closed:
            # Сначала стираем Live (он сейчас держит то что раньше было активным).
            self._stop_live()
            for block in new_closed:
                self._print_block(block, is_first=(len(self._emitted_blocks) == 0))
                self._emitted_blocks.append(block)
            self._printed_blocks = len(self._emitted_blocks)
            # Активный блок поменялся — будет новый Live ниже.
            self._active_text = ""

        # Обновляем активный (последний) блок.
        new_active = blocks[-1] if total > 0 else ""
        if new_active != self._active_text:
            self._active_text = new_active
            if self._live is None and self._active_text:
                self._start_live()
            # Если Live уже работает — он сам перерисуется через get_renderable.

    def finalize(self) -> None:
        """Завершает стрим: останавливает Live и печатает активный блок в scrollback."""
        if self._done:
            return
        self._stop_live()  # transient=True → активный кадр стёрт.
        if self._active_text:
            self._print_block(self._active_text, is_first=(self._printed_blocks == 0))
        self._active_text = ""
        self._printed_blocks = 0
        self._emitted_blocks = []
        self._done = True
        self._emitted_any = False

    def reset(self) -> None:
        self._stop_live()
        self._active_text = ""
        self._printed_blocks = 0
        self._emitted_blocks = []
        self._done = False
        self._emitted_any = False

    @property
    def has_active(self) -> bool:
        return self._live is not None or bool(self._active_text)
