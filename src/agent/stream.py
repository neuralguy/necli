"""Streaming responses with inline tool-block execution."""

import logging
import time
from itertools import cycle

from rich.console import Console, Group
from rich.text import Text

import tools
from agent.block_stream import BlockStreamer
from agent.context import AgentContext
from agent.display import print_static
from agent.sanitizer import sanitize_response
from agent.stream_parser import (
    _clean_display_text,
    _find_next_complete_tool,
    _find_next_partial_tool,
    _find_next_tool_start,
)
from agent.stream_render import (
    THINKING_FRAMES,
    render_live_group,
)
from agent.think import _THINK_BLOCK_RE, ThinkLog, parse_partial_thought, parse_think_blocks
from config.ui import ui
from models import get_pricing
from planner import (
    _PLAN_BLOCK_RE,
    apply_plan_commands,
    delete_plan_file,
    parse_plan_commands,
    resolve_plan_command_focus,
    save_plan_file,
)
from session.tokens import count_tokens
from ui.formatting import format_cost, format_tokens
from ui.shell import ensure_static_blank, get_shell

logger = logging.getLogger(__name__)

#: Консоль нужна BlockStreamer'у для capture/ширины; печатает в scrollback
#: только print_static (единый канал вывода, см. agent/display.py).
console = Console()

#: Ключ динамической зоны Shell под кадр стрима: спиннер thinking, индикатор
#: writing, превью частичного вызова, панели reasoning/think, индикатор
#: прерывания. Раньше этот кадр держал rich Live с transient=True.
_STREAM_ZONE = "stream"


def print_worked_footer(ctx, fallback_elapsed: float = 0.0) -> None:
    """Печатает строку '⏱ работал Nс' с суммарным временем за все циклы хода.

    Время считается от ctx.turn_start_time (начало хода пользователя), поэтому
    отражает суммарное время по всем итерациям цикла, а не последнего сообщения.
    """
    from config.i18n import t as _i18n

    turn_start = getattr(ctx, "turn_start_time", None)
    secs = (time.monotonic() - turn_start) if turn_start is not None else fallback_elapsed
    if secs < 60:
        label = _i18n("stream.worked_sec", n=max(1, round(secs)))
    else:
        label = _i18n("stream.worked_min", n=round(secs / 60))
    ensure_static_blank()
    print_static(f"[grey50]⏱ {label}[/grey50]")
    ensure_static_blank()
    try:
        if getattr(ctx, "render_store", None) is not None:
            ctx.render_store.add("worked", {"label": label})
    except Exception:
        logger.debug("store worked footer failed", exc_info=True)


class StreamEarlyAbort(Exception):  # noqa: N818
    """Сигнал из tool-precheck для немедленной остановки стрима модели.

    Используется когда инструмент заведомо невыполним (например create_file
    для существующего файла) — нет смысла ждать остальной ответ модели.
    """
def _tool_subtitle(model: str, write_time: float, raw_input: str, output_text: str = "") -> str:
    """Subtitle для tool-блока: суммарные tokens (вход + выход) и cost (input+output).

    raw_input — тело fenced-блока (аргументы вызова), считаем как input-токены.
    output_text — содержимое результата инструмента (read/shell/ls/...),
                  считаем как output-токены. Может быть пустым в момент превью
                  до выполнения — тогда отображается только input.
    """
    in_tokens = count_tokens(raw_input, model)
    out_tokens = count_tokens(output_text, model) if output_text else 0
    total_tokens = in_tokens + out_tokens
    price_in, price_out = get_pricing(model)
    cost = (in_tokens * price_in + out_tokens * price_out) / 1_000_000
    parts = [
        f"~{format_tokens(total_tokens)} tk",
        format_cost(cost),
        f"@@WRITE_TIME={write_time:.2f}@@",
    ]
    return "[dim]" + " \u00b7 ".join(parts) + "[/dim]"


class LiveStream:
    """Streaming with inline tool-block execution."""

    def __init__(self, model: str, ctx: AgentContext, session=None, message_num: int = 1):
        self.model = model
        self.ctx = ctx
        self.session = session
        self.message_num = message_num
        # Native function-calling: инструменты доставляются как tool_calls, а
        # любой fenced-блок (:::call ... call:::) в тексте ответа НЕ исполняется
        # и печатается дословно как обычный текст.
        try:
            from system_prompt import _resolve_native_tools
            self._native_tools = bool(_resolve_native_tools())
        except Exception:
            logger.debug("native-tools detection failed", exc_info=True)
            self._native_tools = False
        self.buffer = ""
        self.reasoning_buffer = ""
        self._reasoning_printed = False
        self._reasoning_store_item = None
        self._reasoning_started_at: float | None = None
        self.start_time = time.monotonic()
        self._first_chunk_time: float | None = None
        self._plan_processed_count = 0
        self._plan_shown_count = 0
        self.think_log = ThinkLog()
        self._think_processed_count = 0
        self.inline_results: list[tools.ToolResult] = []
        self.inline_call_keys: list[tuple[str, str]] = []
        #: Занята ли динамическая зона _STREAM_ZONE нашим кадром. Раньше здесь
        #: лежал объект Live; наличие кадра проверяется в тех же местах.
        self._dyn = False
        self._current_text_start = 0
        self._printed_text_end = 0
        self._tcycle = cycle(THINKING_FRAMES)
        self._wcycle = cycle(THINKING_FRAMES)
        self._last_block_end_time = time.monotonic()
        self._spin_last_tick = 0.0
        self._spin_tframe = next(self._tcycle)
        self._spin_wframe = next(self._wcycle)
        self._early_abort: bool = False
        self._finalizing: bool = False
        # Compact-режим: поблочный markdown-стример (Claude Code-style).
        # Активен только когда is_compact()==True. Держит последний блок в
        # своей динамической зоне, остальные блоки уходят в scrollback.
        self._block_streamer = BlockStreamer(
            console, refresh_per_second=int(ui.get("live_stream.refresh_per_second", 8)),
        )
        # Конец уже сохранённого в RenderStore compact-текста (для Ctrl+O).
        self._stored_text_end: int = 0
        self._native_tool_chunks: dict[int, dict] = {}

    def _lead_blank(self) -> None:
        """Одна пустая строка-разделитель перед элементом.

        Печатается ВСЕГДА (включая первый элемент turn'а — отделяет от
        введённого prompt'а). Дубли исключены тем, что каждый элемент зовёт
        _lead_blank ровно один раз перед собой и больше никто пустых не печатает.
        """
        if getattr(self.ctx, "silent_console", False):
            return
        print_static(Text(""))

    def _ts(self):
        self._tick_spinners()
        return self._spin_tframe

    def _ws(self):
        self._tick_spinners()
        return self._spin_wframe

    def _tick_spinners(self):
        now = time.monotonic()
        if now - self._spin_last_tick >= 0.1:
            self._spin_tframe = next(self._tcycle)
            self._spin_wframe = next(self._wcycle)
            self._spin_last_tick = now

    def _scan_tool_start(self, text: str, offset: int):
        return None if self._native_tools else _find_next_tool_start(text, offset)

    def _scan_complete_tool(self, text: str, offset: int):
        return None if self._native_tools else _find_next_complete_tool(text, offset)

    def _scan_partial_tool(self, text: str, offset: int):
        return None if self._native_tools else _find_next_partial_tool(text, offset)

    def _clean(self, text: str) -> str:
        return _clean_display_text(text, strip_calls=not self._native_tools)

    def _get_current_text(self) -> str:
        start = max(self._current_text_start, self._printed_text_end)
        frag = self.buffer[start:]
        tool_start = self._scan_tool_start(frag, 0)
        cleaned = self._clean(frag[:tool_start]) if tool_start is not None else self._clean(frag)
        # Некоторые модели (Sonnet через OnlySQ при native function-calling)
        # дублируют содержимое :::call think внутри обычного текста.
        # Если cleaned-фрагмент дословно совпадает (или начинается) с любой
        # уже распарсенной мыслью — выкидываем его, иначе под thinking-panel
        # появляется вторая копия того же текста в Response.
        if cleaned and self.think_log.total:
            stripped = cleaned.strip()
            for step in self.think_log.steps:
                thought = (step.text or "").strip()
                if not thought:
                    continue
                if stripped == thought or stripped.startswith(thought):
                    rest = stripped[len(thought):].lstrip()
                    cleaned = rest
                    break
        return cleaned

    def _get_partial_tool_info(self):
        if self._native_tools and self._native_tool_chunks:
            active = self._native_tool_chunks[min(self._native_tool_chunks)]
            return True, active["args"], active["name"] or "tool", ""
        start = max(self._current_text_start, self._printed_text_end)
        partial = self._scan_partial_tool(self.buffer, start)
        if partial:
            return True, partial.body, partial.tool_name, partial.attrs_header
        return False, "", "shell", ""

    def _render_live(self):
        ct_full = self._get_current_text()
        # Текст ответа уже выведен поблочно в scrollback через BlockStreamer —
        # кадр стрима его НЕ должен дублировать.
        ct = ""
        hp, _pb, pt, _pa = self._get_partial_tool_info()
        live_reasoning = "" if self._reasoning_printed else self.reasoning_buffer
        reasoning_done = bool(ct_full and ct_full.strip())
        live_think = None if getattr(self, "_think_static_printed", False) else self.think_log
        partial_thought = parse_partial_thought(self.buffer) if live_think is not None else None
        partial_elapsed = (
            time.monotonic() - self._last_block_end_time if hp else 0.0
        )
        group = render_live_group(
            ct, hp, pt, self._ts(), self._ws(), self.model,
            message_num=self.message_num,
            reasoning_text=live_reasoning,
            reasoning_done=reasoning_done or self._finalizing,
            think_log=live_think,
            partial_thought=partial_thought,
            response_streaming=not self._finalizing,
            partial_elapsed=partial_elapsed,
        )
        # Ведущая пустая строка: кадр стрима (initial thinking / partial-tool
        # превью) всегда отделяется одной пустой от того, что выше — и от
        # введённого prompt'а (первый кадр), и от напечатанных блоков.
        return Group(Text(""), group)

    def _start_live(self):
        """Показать только реальный raw-reasoning/think, без второго Working."""
        if getattr(self.ctx, "silent_console", False):
            return
        sh = get_shell()
        if sh is None or self._block_streamer.has_active:
            return
        has_reasoning = (
            not self._reasoning_printed
            and bool((self.reasoning_buffer or "").strip())
        )
        has_thought = (
            not self._native_tools
            and not getattr(self, "_think_static_printed", False)
            and bool(parse_partial_thought(self.buffer))
        )
        if not has_reasoning and not has_thought:
            return
        sh.set_dynamic(_STREAM_ZONE, self._render_live)
        self._dyn = True

    def _stop_live(self):
        """Убрать кадр из динамической зоны — прямая замена Live.stop().

        transient=True означало: кадр исчезает и в scrollback не остаётся.
        clear_dynamic делает ровно это; готовое содержимое печатают вызывающие
        через print_static, как и раньше печатал console.print после stop().
        """
        if not self._dyn:
            return
        self._dyn = False
        sh = get_shell()
        if sh is not None:
            sh.clear_dynamic(_STREAM_ZONE)

    def _update_live(self):
        """Немедленно перерисовать кадр.

        Ручного refresh больше нет и не нужно: в зоне лежит callable, Shell
        пересчитывает его сам. Просим лишь не ждать очередного такта ticker'а,
        чтобы новый чанк появлялся без задержки, как при Live.refresh().
        """
        if not self._dyn:
            return
        sh = get_shell()
        if sh is not None:
            sh.invalidate()

    def _flush_reasoning_static(self):
        if self._reasoning_printed:
            return
        rb = (self.reasoning_buffer or "").strip()
        if not rb:
            self._reasoning_printed = True
            return
        # Normal streaming stores every cumulative delta, but the final flush
        # is also a safety net for providers that only expose reasoning at EOF.
        self._store_reasoning_raw()
        if getattr(self.ctx, "silent_console", False):
            self._reasoning_printed = True
            return
        from agent.stream_render import render_reasoning_panel
        try:
            elapsed = (
                (time.monotonic() - self._reasoning_started_at)
                if self._reasoning_started_at is not None else None
            )
            print_static(Group(
                Text(""),
                render_reasoning_panel(rb, streaming=False, elapsed=elapsed),
            ))
        except Exception:
            logger.debug("render_reasoning_panel failed", exc_info=True)
        self._reasoning_printed = True

    def _store_reasoning_raw(self) -> None:
        """Keep native reasoning_content available for Ctrl+O replay."""
        raw = self.reasoning_buffer or ""
        if not raw.strip():
            return
        try:
            store = getattr(self.ctx, "render_store", None)
            if store is None:
                return
            item = self._reasoning_store_item
            if item is None:
                self._reasoning_store_item = store.add_reasoning(raw)
            else:
                store.update_reasoning(item, raw)
        except Exception:
            logger.debug("store reasoning failed", exc_info=True)

    def _advance_past_think_blocks(self):
        """Move scan cursor past ```call think``` blocks WITHOUT dropping the frame.

        Раньше тут печатался текст до think-блока отдельной Response-панелью —
        это приводило к «морганию»: текст «застывал» в панели, а static
        thinking-panel прятала live-строку с мыслями. Теперь просто двигаем
        курсоры за конец
        последнего закрытого блока БЕЗ печати и БЕЗ гашения кадра. Сам
        think-блок выкидывается из отображения через strip_think_blocks в
        _clean_display_text, поэтому кадр плавно перерисует единую Response.
        """
        scan_from = self._current_text_start
        last_end = scan_from
        found = False
        for m in _THINK_BLOCK_RE.finditer(self.buffer, scan_from):
            found = True
            if m.end() > last_end:
                last_end = m.end()
        if not found:
            return
        self._current_text_start = max(self._current_text_start, last_end)
        if self._printed_text_end < last_end:
            self._printed_text_end = last_end

    def _store_think_raw_steps(self) -> None:
        """Сохраняет исходные тексты всех мыслей для Ctrl+O replay."""
        try:
            from agent.loop import get_current_ctx
            ctx = get_current_ctx()
            store = getattr(ctx, "render_store", None) if ctx else None
            if store is None:
                return
            raw_steps = [s.raw_text or s.text for s in self.think_log.steps]
            item = getattr(self, "_think_store_item", None)
            if item is None:
                self._think_store_item = store.add_think(raw_steps)
            else:
                store.update_think(item, raw_steps)
        except Exception:
            logger.debug("store think failed", exc_info=True)

    def _print_think_static_once(self):
        """Печатает статичную панель think один раз, когда нужно перейти к тексту/инструменту."""
        if getattr(self, "_think_static_printed", False):
            return
        if self.think_log.total == 0:
            return
        if getattr(self.ctx, "silent_console", False):
            self._think_static_printed = True
            return
        from agent.think import render_think_static
        self._flush_reasoning_static()
        print_static(Group(Text(""), render_think_static(self.think_log)))
        self._think_static_printed = True
        self._store_think_raw_steps()

    def _advance_past_plan_blocks(self):
        """Move cursor past plan blocks. Plan does NOT render to UI.

        Текст ДО plan-блока уже стримился через BlockStreamer
        (_compact_feed_blocks). Раньше тут печатался тот же текст ВТОРОЙ раз
        отдельной Response-панелью (дубль ответа). Теперь только финализируем
        активный
        блок BlockStreamer'а (он сам уже всё вывел в scrollback) и двигаем
        курсоры за конец plan-блоков. План в UI не печатается вообще.
        """
        scan_from = max(self._current_text_start, self._printed_text_end)
        spans = []
        for m in _PLAN_BLOCK_RE.finditer(self.buffer, scan_from):
            spans.append((m.start(), m.end()))  # noqa: PERF401
        if not spans:
            return
        spans.sort()
        merged = []
        for s, e in spans:
            if merged and s < merged[-1][1]:
                if e > merged[-1][1]:
                    merged[-1] = (merged[-1][0], e)
                continue
            merged.append((s, e))
        first_start = merged[0][0]
        last_end = merged[-1][1]
        if first_start > self._printed_text_end:
            # Догоняем и финализируем текст до plan-блока через BlockStreamer.
            # reset() открывает новую
            # «страницу» для текста после плана.
            self._compact_feed_blocks()
            self._block_streamer.finalize()
            self._store_compact_segment()
            self._block_streamer.reset()
        self._current_text_start = max(self._current_text_start, last_end)
        self._printed_text_end = max(self._printed_text_end, last_end)

    def start(self):
        self.start_time = time.monotonic()
        self._first_chunk_time = None
        self.buffer = ""
        self.reasoning_buffer = ""
        self._reasoning_printed = False
        self._reasoning_store_item = None
        self._reasoning_started_at = None
        self.inline_results = []
        self.inline_call_keys = []
        self.ctx.step_tracker.reset()
        self._plan_processed_count = 0
        self._plan_shown_count = 0
        self._think_processed_count = 0
        self.think_log = ThinkLog()
        self._think_static_printed = False
        self._think_store_item = None
        self._current_text_start = 0
        self._printed_text_end = 0
        self._tcycle = cycle(THINKING_FRAMES)
        self._wcycle = cycle(THINKING_FRAMES)
        self._last_block_end_time = time.monotonic()
        self._tg_typing_started = False
        self._finalizing = False
        self._block_streamer.reset()
        self._stored_text_end = 0
        self._native_tool_chunks = {}
        from agent.working import continue_working_round
        continue_working_round(self.ctx, self.model, self.message_num)
        self._start_tg_thinking()
        self._start_live()

    def on_reasoning_update(self, text: str):
        """Called when reasoning_content chunk arrives. text — full accumulated reasoning."""
        if text == self.reasoning_buffer:
            return
        if self._reasoning_started_at is None and (text or "").strip():
            self._reasoning_started_at = time.monotonic()
        self.reasoning_buffer = text
        self._store_reasoning_raw()
        current = getattr(self.ctx, "working_round", None)
        if current is not None:
            current.update_stream(text)
        eh = getattr(self.ctx, "event_handler", None)
        if eh is not None and hasattr(eh, "emit_stream_chunk"):
            try:
                eh.emit_stream_chunk(text, "reasoning")
            except Exception as _e:
                logger.warning("emit_stream_chunk(reasoning) failed: %s", _e)
        if not self._dyn:
            self._start_live()
        else:
            self._update_live()

    def on_native_tool_update(self, chunks: list[dict]) -> None:
        """Accumulate native tool-call deltas for progressive terminal rendering."""
        for chunk in chunks or []:
            if not isinstance(chunk, dict):
                continue
            index = chunk.get("index", 0)
            if not isinstance(index, int):
                index = 0
            current = self._native_tool_chunks.setdefault(index, {
                "name": "",
                "args": "",
            })
            name = chunk.get("name")
            if name and not current["name"]:
                current["name"] = str(name)
            args = chunk.get("args")
            if isinstance(args, str) and args:
                current["args"] += args
            elif isinstance(args, dict):
                import json
                current["args"] += json.dumps(args, ensure_ascii=False)
        if self._native_tool_chunks:
            current = getattr(self.ctx, "working_round", None)
            if current is not None:
                active = self._native_tool_chunks[min(self._native_tool_chunks)]
                current.update_stream(
                    active.get("args", ""),
                    current_call=str(active.get("name") or "tool"),
                )
            self._block_streamer.finalize()
            if not self._dyn:
                self._start_live()
            else:
                self._update_live()

    def on_text_update(self, text: str):
        """Called on each streaming text update (full accumulated buffer)."""
        if self._early_abort:
            raise StreamEarlyAbort()
        if self._first_chunk_time is None:
            self._first_chunk_time = time.monotonic() - self.start_time
        self.buffer = text
        current = getattr(self.ctx, "working_round", None)
        if current is not None:
            has_partial, _body, partial_name, _attrs = self._get_partial_tool_info()
            current.update_stream(
                text,
                current_call=partial_name if has_partial else "",
            )
        eh = getattr(self.ctx, "event_handler", None)
        if eh is not None and hasattr(eh, "emit_stream_chunk"):
            try:
                eh.emit_stream_chunk(text, "text")
            except Exception as _e:
                logger.warning("emit_stream_chunk(text) failed: %s", _e)

        tb = parse_think_blocks(self.buffer)
        if len(tb) > self._think_processed_count:
            # Текст ДО think-блока шёл через BlockStreamer (его активный
            # блок держится в динамической зоне). Если не финализировать его
            # сейчас, think-static уйдёт в scrollback НАД живым кадром →
            # think всплывёт ВЫШЕ уже написанного ответа. Догоняем и
            # финализируем текст до think, потом печатаем think под ним.
            if self._block_streamer.has_active:
                self._compact_feed_blocks()
                self._block_streamer.finalize()
                self._store_compact_segment()
                self._block_streamer.reset()
            new_thoughts = tb[self._think_processed_count:]
            for thought in new_thoughts:
                self.think_log.add(thought)
            self._store_think_raw_steps()
            self._think_processed_count = len(tb)
            self._advance_past_think_blocks()
            # Печатаем static-панель think СРАЗУ при закрытии блока (до текста
            # ответа). Иначе кадр с мыслью стирается, печатается текст, а
            # static think всплывает в конце снизу — выглядит как мигание и
            # неправильный порядок. Живой кадр (partial-мысль) уже снят
            # _advance_past_think_blocks; здесь фиксируем мысль в scrollback.
            is_cli_eh_now = (
                eh is None
                or type(eh).__name__ == "RichEventHandler"
            )
            if is_cli_eh_now:
                self._stop_live()
                self._print_think_static_once()
            # RichEventHandler (CLI) сам рисует think через render_think_static
            # — отдельные tool_start/tool_result события для think приведут к
            # дублированию рамки. Пропускаем только web/telegram handlers.
            is_cli_eh = (
                eh is not None
                and type(eh).__name__ == "RichEventHandler"
            )
            if eh is not None and not is_cli_eh:
                for thought in new_thoughts:
                    try:
                        call = tools.ToolCall(
                            command="think",
                            tool_name="think",
                            args={"thought": thought},
                            raw="",
                        )
                        eh.on_tool_start(call, subtitle="")
                        eh.on_tool_result(tools.ToolResult(
                            name="think",
                            status="ok",
                            output=thought,
                            exit_code=0,
                            command="think",
                        ))
                    except Exception as _e:  # noqa: PERF203
                        logger.warning("think event emit failed: %s", _e)

        pc = parse_plan_commands(self.buffer)
        npc = pc[self._plan_processed_count:]
        if npc:
            plan_events = []
            plan_before = self.ctx.plan
            for cmd in npc:
                plan_events.append((
                    cmd.action,
                    resolve_plan_command_focus(plan_before, cmd),
                    str(cmd.data.get("status") or ""),
                ))
                plan_before = apply_plan_commands(plan_before, [cmd])
            self.ctx.plan = plan_before
            self._plan_processed_count = len(pc)
            if self.ctx.plan:
                if self.ctx.plan.is_complete:
                    delete_plan_file(self.ctx.effective_plan_dir)
                else:
                    save_plan_file(self.ctx.plan, self.ctx.effective_plan_dir)
            if len(pc) > self._plan_shown_count:
                self._advance_past_plan_blocks()
                self._plan_shown_count = len(pc)
                if self.ctx.plan and not getattr(self.ctx, "silent_console", False):
                    self._stop_live()
                    from agent.display import show_plan_update
                    for action, focus_index, status in plan_events:
                        if action == "update" and status == "in_progress" and not self.ctx.plan.is_complete:
                            continue
                        show_plan_update(self.ctx.plan, action=action, focus_index=focus_index)
                    self._start_live()

        # Поблочный стрим текста до ближайшего tool-блока.
        self._compact_feed_blocks()

        scan_start = max(self._current_text_start, self._printed_text_end)
        partial = self._scan_partial_tool(self.buffer, scan_start)
        # Партиал нас интересует ТОЛЬКО если перед ним нет ещё не выполненного
        # complete-блока: иначе сдвиг _current_text_start на partial.start
        # перепрыгивает готовый complete, и while-loop ниже его пропускает.
        # Пример (Sonnet 4.6): chunk1 содержит partial create_file, chunk2
        # достраивает create_file + начинает partial patch_file. Если на
        # chunk2 мы сдвинем _current_text_start на patch_file.start —
        # create_file complete никогда не исполнится в правильном порядке.
        next_complete = self._scan_complete_tool(self.buffer, scan_start)
        partial_is_relevant = (
            partial is not None
            and partial.start > self._printed_text_end
            and (next_complete is None or partial.start <= next_complete.start)
        )
        if partial_is_relevant:
            self._stop_live()
            self._block_streamer.finalize()
            self._printed_text_end = partial.start
            # Двигаем _current_text_start чтобы _get_current_text() не возвращал
            # тот же text_before на следующих тиках (дублирование Response).
            # Но не ВПЕРЁД complete-блоков — проверили выше.
            self._current_text_start = max(self._current_text_start, partial.start)
            self._start_live()

        from agent.stream_tool_exec import handle_complete_tool
        while True:
            complete = self._scan_complete_tool(
                self.buffer, self._current_text_start,
            )
            if not complete:
                break
            self._stop_live()
            self._print_think_static_once()
            if complete.start > self._printed_text_end:
                text_before = self.buffer[
                    self._printed_text_end:complete.start
                ]
                # Если _printed_text_end уже стоит на начале fence (partial
                # был обнаружен ранее), text_before — это просто открывающая
                # строка `:::call <tool> ...` без полезного контента.
                # _clean_display_text её отфильтрует, но
                # явная проверка дешевле и снимает лишний print_static().
                cleaned_before = self._clean(text_before)
                if cleaned_before:
                    self._compact_feed_blocks()
                    self._block_streamer.finalize()
                    self._store_compact_segment()
                # Web: фиксируем промежуточный текст между tool-блоками как
                # отдельное assistant-сообщение через tool_prefix.
                try:
                    eh = self.ctx.event_handler if self.ctx else None
                    if eh is not None and hasattr(eh, "emit_stream_chunk") and text_before.strip():
                        eh.emit_stream_chunk(text_before, "tool_prefix")
                except Exception:
                    logger.warning("emit tool_prefix failed", exc_info=True)
            handle_complete_tool(self, complete)
            self._current_text_start = complete.end
            self._printed_text_end = complete.end
            # Новая «страница» текста после tool-блока — сбрасываем.
            self._block_streamer.reset()
            self._last_block_end_time = time.monotonic()
            self._dyn = False
            self._start_live()

        # Живой стрим частичной мысли (незакрытый :::call think). think
        # исключён из _scan_partial_tool, поэтому кадр для него надо поднять
        # отдельно: пока мысль пишется, рисуем её через _render_live
        # (render_live_group рисует partial_thought). Без этого think
        # появляется только целиком в конце через static-панель.
        if not self._native_tools and not getattr(self, "_think_static_printed", False):
            partial_thought = parse_partial_thought(self.buffer)
            if partial_thought:
                # Мысль начала писаться ПОСЛЕ текста ответа: BlockStreamer
                # держит кадр активного блока и блокирует кадр стрима → think
                # не стримился. Финализируем текст до think в scrollback,
                # сдвигаем курсоры на начало think-блока (чтобы
                # _compact_feed_blocks не перечитал тот же текст в свежий
                # BlockStreamer = дубль), затем поднимаем кадр стрима.
                if self._block_streamer.has_active:
                    think_start = self._scan_tool_start(
                        self.buffer, self._current_text_start,
                    )
                    self._block_streamer.finalize()
                    self._store_compact_segment()
                    self._block_streamer.reset()
                    if think_start is not None:
                        self._current_text_start = max(
                            self._current_text_start, think_start,
                        )
                        self._printed_text_end = max(
                            self._printed_text_end, think_start,
                        )
                if not self._dyn:
                    self._start_live()
                else:
                    self._update_live()

    def _store_compact_segment(self) -> None:
        """Сохраняет текущий compact-сегмент текста в RenderStore (для Ctrl+O).

        В compact-режиме текст печатается через BlockStreamer, минуя
        render_md_panel/_store_assistant — поэтому без этого вызова
        assistant-блоки терялись бы при Ctrl+O replay. Зеркалит логику
        verbose-режима: один сегмент текста между tool-блоками = один
        assistant-блок в store. Идемпотентность — по диапазону уже
        сохранённого текста (_stored_text_end)."""
        start = max(self._stored_text_end, self._current_text_start)
        end = len(self.buffer)
        tool_at = self._scan_tool_start(self.buffer, max(self._stored_text_end, self._current_text_start))
        if tool_at is not None and tool_at > start:
            end = tool_at
        if end <= start:
            return
        cleaned = self._clean(self.buffer[start:end])
        if not cleaned or not cleaned.strip():
            return
        self._stored_text_end = end
        try:
            from agent.display import _store_assistant
            _store_assistant(cleaned, subtitle="", message_num=self.message_num)
        except Exception:
            logger.debug("store compact segment failed", exc_info=True)

    def _compact_feed_blocks(self) -> None:
        """Compact: отдаёт BlockStreamer'у текущий чистый текст до tool-блока."""
        if getattr(self.ctx, "silent_console", False):
            return
        start = self._current_text_start
        end = len(self.buffer)
        tool_at = self._scan_tool_start(self.buffer, start)
        if tool_at is not None:
            end = tool_at
        if end <= start:
            return
        raw = self.buffer[start:end]
        cleaned = self._clean(raw)
        if not cleaned:
            return
        # Появился текст — кадр стрима (initial thinking) больше не нужен.
        # Гасим его ДО запуска BlockStreamer'а, иначе в динамической зоне
        # оказались бы сразу два кадра: спиннер и активный блок.
        self._stop_live()
        # Reasoning (реальные мысли модели) ДОЛЖЕН быть напечатан ДО первого
        # блока текста ответа. Иначе он флашится только в stop() — уже ПОСЛЕ
        # всего ответа, и в scrollback мысли оказываются ниже текста.
        if not self._block_streamer.has_active and not self._block_streamer._emitted_any:
            self._flush_reasoning_static()
            # Ведущая пустая строка перед первым блоком текста в этом сегменте
            # (если до него уже что-то было напечатано — think/tool/etc).
            self._lead_blank()
        self._block_streamer.update(cleaned)

    def stop(self, show_final: bool = True, cancelled: bool = False):
        # Догоняем последний кусок текста и финализируем активный блок.
        self._compact_feed_blocks()
        self._block_streamer.finalize()
        self._store_compact_segment()

        remaining = self._get_current_text()
        self._stop_live()
        if not self._reasoning_printed and (self.reasoning_buffer or "").strip():
            self._flush_reasoning_static()
        self._print_think_static_once()
        if show_final and remaining and remaining.strip():
            # Текст уже в scrollback (поблочно). Subtitle (TTFB/tokens/$)
            # не печатаем — статистика и так в шапке prompt'а.
            self._printed_text_end = len(self.buffer)

        self._mirror_to_telegram(cancelled=cancelled)
        self.buffer = sanitize_response(self.buffer)

    def _start_tg_thinking(self) -> None:
        """Запускает typing-индикатор в TG (без текстового thinking-плейсхолдера).

        Плейсхолдер «💭 thinking…» больше не отправляется отдельным сообщением:
        о работе агента сигнализирует typing-индикатор. Если включён
        telegram_show_thinking — финальный ответ отрисуется в новом сообщении,
        иначе тоже (placeholder_id остаётся None).
        """
        try:
            import config as _cfg
            if not _cfg.get_telegram_enabled():
                return
            from apis.telegram import get_bridge
            bridge = get_bridge()
            if not bridge.is_running:
                return
            bridge.start_typing()
            bridge.agent_busy = True
            self._tg_typing_started = True
        except Exception:
            logger.debug("tg thinking start failed", exc_info=True)

    def _mirror_to_telegram(self, cancelled: bool = False) -> None:
        """Зеркалит reasoning и финальный текст ответа модели в TG (если активен).

        Если thinking-плейсхолдер был отправлен, редактирует его. Иначе шлёт новое.
        """
        try:
            import config as _cfg
            bridge = None
            if _cfg.get_telegram_enabled():
                from apis.telegram import get_bridge
                bridge = get_bridge()
                if not bridge.is_running:
                    bridge = None

            if bridge is not None and self._tg_typing_started:
                bridge.stop_typing()
                self._tg_typing_started = False
            if bridge is not None:
                bridge.agent_busy = False

            if bridge is None:
                return

            from agent.telegram_handler import TelegramEventHandler
            from planner import strip_plan_commands
            from tools import strip_tool_calls

            reasoning = (self.reasoning_buffer or "").strip()
            final_text = strip_tool_calls(strip_plan_commands(self.buffer or "")).strip()

            handler = self.ctx.event_handler if self.ctx else None
            if not isinstance(handler, TelegramEventHandler):
                handler = TelegramEventHandler(handler) if handler else None

            # Reasoning логически предшествует ответу — шлём его ПЕРВЫМ
            # (отдельным сообщением). Зеркалится только если включено в /tg.
            if reasoning and _cfg.get_telegram_show_thinking() and handler is not None:
                handler.mirror_reasoning(reasoning)

            if final_text:
                if handler is not None:
                    handler.mirror_assistant(final_text, cancelled=cancelled)
            elif cancelled and handler is not None:
                handler.mirror_assistant("", cancelled=True)
            # Нет финального текста (только tool calls) — ничего не шлём:
            # о работе агента уже сообщает typing-индикатор и tool-сообщения.
        except Exception:
            logger.debug("stream tg mirror failed", exc_info=True)
