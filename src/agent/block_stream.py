"""Поблочный markdown-streamer для compact-режима.

Архитектура (по образцу Claude Code CLI):

1. На каждом обновлении буфера парсим текст в список блоков.
   Блок = paragraph / heading / list / code-fence / blockquote / hr / table.
2. Все блоки КРОМЕ последнего считаются «закрытыми» — за ними есть
   следующий, значит они уже не вырастут. Печатаем их ОДИН РАЗ через
   print_static — они уходят в scrollback навсегда.
3. Последний блок («активный») держим в динамической зоне Shell: она занимает
   ровно высоту этого блока и перерисовывается на месте. При появлении
   следующего блока зона гасится, блок печатается, зона поднимается заново
   с новым активным блоком.
4. Терминал листает естественно: scrollback растёт с каждым закрытым блоком,
   динамическая зона в каждый момент занимает 1-10 строк (один блок).

Не пытаемся писать свой парсер inline-markdown — каждый блок рендерится
нативным rich.markdown.Markdown.
"""

import re

from rich.console import Console, Group
from rich.text import Text

from agent.display import mark_compact_assistant_output, print_static
from agent.markdown import ResponseMarkdown

#: Ключ динамической зоны под активный (ещё растущий) блок ответа. Отдельный от
#: "stream" намеренно: кадром общего стрима и кадром активного блока управляют
#: независимые пары start/stop, и один ключ на двоих означал бы, что чужой
#: clear_dynamic гасит живой кадр соседа.
_BLOCK_ZONE = "block"

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
        s.update(full_buffer_v1)   # печатает закрытые блоки, держит активный в зоне
        s.update(full_buffer_v2)
        ...
        s.finalize()               # печатает оставшийся активный блок, гасит зону
    """

    def __init__(self, console: Console, refresh_per_second: int = 8):
        # Консоль нужна только для capture и ширины: в scrollback пишет
        # print_static, единый канал вывода агента (см. agent/display.py).
        self.console = console
        self._refresh = refresh_per_second
        self._printed_blocks: int = 0       # сколько блоков уже ушло в scrollback
        self._emitted_blocks: list[str] = []  # тексты блоков, уже ушедших в scrollback (по содержимому)
        self._active_text: str = ""          # текст текущего активного блока
        self._live: bool = False             # занята ли зона _BLOCK_ZONE нашим кадром
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
        # и печатаем как Text.from_ansi. Динамическая зона тут не задействована
        # (блок уже закрыт, идёт прямо в scrollback), высота не важна.
        with self.console.capture() as cap:
            self.console.print(renderable)
        body = "\n".join(ln.rstrip() for ln in cap.get().strip("\n").split("\n"))
        if not body:
            return
        # Разделитель и блок — одним print_static: два вызова означали бы два
        # run_in_terminal, между которыми рамка перерисовывается впустую.
        block = Text.from_ansi(body)
        mark_compact_assistant_output()
        print_static(Group(Text(""), block) if self._emitted_any else block)
        self._emitted_any = True

    def _make_renderable(self, block_text: str, is_first: bool = False):
        from rich.text import Text
        if not block_text or not block_text.strip():
            return Text("")

        def _md(txt):
            from ui.formatting import escape_md_underscores, latex_to_unicode
            txt = escape_md_underscores(latex_to_unicode(txt))
            try:
                return ResponseMarkdown(txt, code_theme="monokai", inline_code_theme="monokai")
            except Exception:
                return Text(txt)

        if is_first:
            from rich.console import Group

            from agent.stream_render import _inline_md, _is_markdown_block
            from config.themes import t
            from ui.formatting import latex_to_unicode
            block_text = latex_to_unicode(block_text)
            stripped = block_text.lstrip("\n").rstrip()
            first_nl = stripped.find("\n")
            first_line = stripped if first_nl < 0 else stripped[:first_nl]
            rest = "" if first_nl < 0 else stripped[first_nl + 1:].lstrip("\n")
            is_block = _is_markdown_block(first_line, rest)
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
        """Поднять кадр активного блока в динамической зоне Shell.

        Раньше это был rich Live с get_renderable=self._live_renderable и
        auto_refresh. Shell принимает тот же callable и пересчитывает его на
        каждом кадре своего ticker'а, поэтому текст «дописывается» так же.
        """
        if self._live:
            return
        if not self._active_text:
            return
        try:
            from config.ui import ui
            if not bool(ui.get("live_stream.compact_active_live", False)):
                return
        except Exception:
            return
        from ui.shell import get_shell
        sh = get_shell()
        if sh is None:
            # Headless / не-TTY: кадр всё равно был transient и ничего не
            # оставлял, а весь блок целиком напечатает finalize().
            return
        sh.set_dynamic(_BLOCK_ZONE, self._live_renderable)
        self._live = True

    def _tail_active(self, text: str) -> str:
        """Обрезает активный блок до высоты терминала ДЛЯ ЖИВОГО КАДРА.

        Только для динамической зоны: она живёт ВНУТРИ Application и скроллить
        не умеет — кадр выше экрана выдавил бы рамку ввода за край («Window too
        small»). В scrollback и при finalize() блок печатается целиком.
        """
        if not text:
            return text
        from agent.stream_render import _stream_max_lines
        max_lines = _stream_max_lines()
        # Высота кадра считается по ВИЗУАЛЬНЫМ строкам с учётом word-wrap, а не
        # по числу \n: длинный абзац без переносов в одну логическую строку при
        # переносе по ширине терминала занимает много экранных строк. Если
        # кадр выше видимой области, prompt_toolkit не может перерисовать зону
        # на месте: она выдавливает рамку и оставляет мусор в scrollback.
        # Поэтому обрезаем по реальной экранной высоте.
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
        """Кадр активного блока с ведущей пустой строкой-разделителем."""
        active = self._tail_active(self._active_text)
        inner = self._make_renderable(active, is_first=(self._printed_blocks == 0))
        if not self._emitted_any:
            return inner
        return Group(Text(""), inner)

    def _stop_live(self):
        """Погасить кадр активного блока — прямая замена Live.stop().

        transient=True означало: кадр исчезает, а содержимое печатает
        _print_block. clear_dynamic делает ровно это, поэтому дубля кадра в
        scrollback не остаётся.
        """
        if not self._live:
            return
        self._live = False
        from ui.shell import get_shell
        sh = get_shell()
        if sh is not None:
            sh.clear_dynamic(_BLOCK_ZONE)

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
        # новые закрытые блоки — нужно сначала погасить кадр (он держит то, что
        # было активным), потом напечатать в scrollback ВСЕ ещё не выведенные
        # закрытые блоки, потом поднять кадр заново с новым активным блоком.
        total = len(blocks)
        closed = blocks[:total - 1]  # последний — активный
        # Печатаем закрытые блоки ПО СОДЕРЖИМОМУ, а не по индексу: разбиение
        # _split_into_blocks между тиками может сдвигать границы (склейка
        # списков в merged, дозревающие переносы), из-за чего индекс closed_count
        # «дрожит» и уже напечатанный блок печатался бы заново. Сверяемся с
        # фактически выведенными текстами — дубль исключён.
        new_closed = closed[len(self._emitted_blocks):]
        if new_closed:
            # Сначала гасим кадр (он сейчас держит то что раньше было активным).
            self._stop_live()
            for block in new_closed:
                self._print_block(block, is_first=(len(self._emitted_blocks) == 0))
                self._emitted_blocks.append(block)
            self._printed_blocks = len(self._emitted_blocks)
            # Активный блок поменялся — кадр поднимется заново ниже.
            self._active_text = ""

        # Обновляем активный (последний) блок.
        new_active = blocks[-1] if total > 0 else ""
        if new_active != self._active_text:
            self._active_text = new_active
            if not self._live and self._active_text:
                self._start_live()
            # Если кадр уже поднят — Shell сам пересчитает callable зоны.

    def finalize(self) -> None:
        """Завершает стрим: гасит зону и печатает активный блок в scrollback."""
        if self._done:
            return
        self._stop_live()  # как transient=True: активный кадр стёрт.
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
        return self._live or bool(self._active_text)
