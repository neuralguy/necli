"""
Ввод пользователя через prompt_toolkit.

- Enter отправляет, Esc+Enter — новая строка
- `\\` + Enter в конце строки — новая строка (multiline продолжение)
- Ctrl+C при вводе — пустая строка
- Ctrl+D — выход
- Ctrl+V — вставить текст из буфера обмена
- Ctrl+P — вставить изображение из буфера обмена
"""

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.history import FileHistory, ThreadedHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.styles import Style
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit import print_formatted_text as ptk_print

import re

from wcwidth import wcswidth

from ui.completer import make_combined_completer
from ui.formatting import BAR_FILLED_START, BAR_FILLED_END, BAR_EMPTY_START, BAR_EMPTY_END

import config
from config.themes import t

logger = logging.getLogger(__name__)

_HISTORY_FILE = config.BASE_DIR / "history"

def _build_style():
    return Style.from_dict(
        {
            "prompt": f"bold {t('accent')}",
            "prompt-arrow": f"bold {t('success')}",
            "separator": t("muted"),
            "status-text": "bold #ffffff",
            "bottom-toolbar": f"{t('dim_text')} bg:{t('bg_code')}",
            "bottom-toolbar.text": t("dim_text"),
            "bar-filled": t("bar_filled"),
            "bar-empty": t("muted"),
            "hint-left": f"{t('dim_text')} bg:{t('bg_code')}",
            "hint-right": f"#555555 bg:{t('bg_code')}",
            "auto-suggest": "#555555",
            "completion-menu": "bg:default noinherit",
            "completion-menu.completion": "bg:default #888888 noinherit",
            "completion-menu.completion.current": f"bg:default {t('accent')} noinherit",
            "completion-menu.meta.completion": "bg:default #555555 noinherit",
            "completion-menu.meta.completion.current": f"bg:default {t('accent')} noinherit",
            "scrollbar.background": "bg:default noinherit",
            "scrollbar.button": "bg:default noinherit",
            "scrollbar.arrow": "bg:default noinherit",
        }
    )

_EOF = object()
# Возвращается из read(), когда ожидание ввода прервано завершением фоновой
# задачи (buffer пуст) — REPL синтезирует continuation-ход для агента.
_BG_RESUME = object()



def _get_clipboard_text() -> str:
    """Читает текст из системного буфера обмена."""
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["pbpaste"],
                capture_output=True,
                text=True,
                timeout=2,
            )
            return r.stdout if r.returncode == 0 else ""
        except Exception:
            return ""

    for cmd in [
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "--clipboard", "--output"],
        ["wl-paste", "--no-newline"],
    ]:
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=2,
            )
            if r.returncode == 0:
                return r.stdout
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return ""


def _get_term_width() -> int:
    """Get terminal width, default 80."""
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


class _ImageHighlighter(Processor):
    _PATTERN = re.compile(r'(\[image\d+\])')

    def apply_transformation(self, ti):
        fragments = []
        for style, text, *rest in ti.fragments:
            parts = self._PATTERN.split(text)
            for i, part in enumerate(parts):
                if not part:
                    continue
                if i % 2 == 1:
                    fragments.append((f"{t('accent')} underline", part))
                else:
                    fragments.append((style, part))
        return Transformation(fragments)


class InputPrompt:
    """Обёртка над prompt_toolkit для удобного ввода."""

    def __init__(self, working_dir: str = ".", on_mode_toggle=None):
        self.pending_images: list[Path] = []
        self._image_counter = 0
        self._on_mode_toggle = on_mode_toggle
        self.mode: str = "agent"
        self.activity_status: str = "idle"
        self.session = None
        # Callback пересчёта status-строки. Используется в reprint_separator
        # (Ctrl+O) как fallback, когда _last_status_text пуст/устарел после
        # compress/decompress — иначе separator выродится в голую линию.
        self.status_provider = None
        self._last_status_text: Optional[str] = None
        self._combined_completer, self._file_completer = make_combined_completer(working_dir)
        self._session = PromptSession(
            history=ThreadedHistory(FileHistory(str(_HISTORY_FILE))),
            key_bindings=self._make_bindings(),
            completer=self._combined_completer,
            complete_while_typing=True,
            auto_suggest=AutoSuggestFromHistory(),
            style=_build_style(),
            multiline=False,
            wrap_lines=True,
            enable_history_search=False,
            mouse_support=False,
            reserve_space_for_menu=12,
            input_processors=[_ImageHighlighter()],
        )

    def set_working_dir(self, path: str):
        """Update the working directory for file autocomplete."""
        self._file_completer.set_working_dir(path)

    def _session_images_dir(self) -> Optional[Path]:
        """Папка для картинок текущей сессии: <session.dir>/clipboard_images."""
        sess = self.session
        sess_dir = getattr(sess, "dir", None) if sess is not None else None
        if sess_dir is None:
            return None
        return Path(sess_dir) / "clipboard_images"

    def _try_grab_image(self) -> Optional[Path]:
        """Пытается извлечь изображение из системного буфера."""
        try:
            from ui.clipboard import grab_image_from_clipboard

            return grab_image_from_clipboard(dest_dir=self._session_images_dir())
        except Exception:
            return None

    def _insert_image(self, buf) -> bool:
        """Пробует вставить изображение. Возвращает True если удалось."""
        image_path = self._try_grab_image()
        if image_path is not None:
            self._image_counter += 1
            self.pending_images.append(image_path)
            placeholder = f"[image{self._image_counter}]"
            buf.insert_text(placeholder)
            return True
        return False

    def _make_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add(Keys.Enter)
        def _submit_or_continue(event):
            buf = event.current_buffer

            # Если в меню автодополнения выбран пункт — принять его, не отправлять
            if buf.complete_state and buf.complete_state.complete_index is not None:
                buf.complete_state = None
                return

            line = buf.document.current_line_before_cursor

            # Если строка заканчивается на \\ — удаляем \\ и переносим
            if line.endswith("\\"):
                buf.delete_before_cursor(count=1)
                buf.insert_text("\n")
                return

            # Иначе обычная отправка
            text = buf.text.strip()
            if text:
                buf.validate_and_handle()

        @kb.add(Keys.Escape, Keys.Enter)
        def _newline(event):
            event.current_buffer.insert_text("\n")

        @kb.add(Keys.Tab)
        def _tab_toggle_mode(event):
            order = ("agent", "planning", "autonomous")
            try:
                idx = order.index(self.mode)
            except ValueError:
                idx = 0
            self.mode = order[(idx + 1) % len(order)]
            if self._on_mode_toggle:
                self._on_mode_toggle(self.mode)
            event.app.invalidate()

        # ── Стрелки для истории ──
        @kb.add("up")
        def _history_prev(event):
            event.current_buffer.auto_up()

        @kb.add("down")
        def _history_next(event):
            event.current_buffer.auto_down()

        # ── Ctrl+V: универсальная вставка — сначала картинка, потом текст ──
        @kb.add("c-v", eager=True)
        def _paste(event):
            # 1) Если в буфере картинка — вставляем её
            if self._insert_image(event.current_buffer):
                return
            # 2) Иначе — обычный текст
            text = _get_clipboard_text()
            if text:
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                event.current_buffer.insert_text(text)

        # ── Ctrl+O: toggle expanded/compact view (только в compact-режиме) ──
        @kb.add("c-o", eager=True)
        def _toggle_expand_render(event):
            def _do_toggle():
                try:
                    from agent.loop import get_current_ctx
                    from agent.render_replay import replay, clear_terminal
                    from agent.display import is_expanded_preview
                    ctx = get_current_ctx()
                    if ctx is None:
                        return
                    store = getattr(ctx, "render_store", None)
                    if store is None or len(store) == 0:
                        return
                    next_expanded = not is_expanded_preview()
                    logger.debug("ctrl+o toggle: expand=%s items=%d", next_expanded, len(store))
                    clear_terminal()
                    replay(store, expand=next_expanded)
                    # Перерисовать status-separator поверх результата replay.
                    pi = getattr(ctx, "prompt_input", None)
                    if pi is not None and hasattr(pi, "reprint_separator"):
                        fresh = getattr(ctx, "last_status_text", None)
                        # После compress/decompress last_status_text может
                        # устареть/опустеть → пересчитываем через callback,
                        # иначе separator выродится в голую линию.
                        if not fresh:
                            rebuild = getattr(ctx, "rebuild_status", None)
                            if callable(rebuild):
                                try:
                                    fresh = rebuild()
                                    ctx.last_status_text = fresh or ""
                                except Exception:
                                    logger.debug("ctrl+o rebuild_status failed", exc_info=True)
                        if fresh:
                            try:
                                pi._last_status_text = fresh
                            except Exception:
                                logger.debug("ctrl+o set last_status_text failed", exc_info=True)
                        try:
                            pi.reprint_separator()
                        except Exception:
                            logger.warning("reprint_separator failed", exc_info=True)
                except Exception:
                    logger.warning("ctrl+o toggle failed", exc_info=True)

            from prompt_toolkit.application import run_in_terminal as _rit
            _rit(_do_toggle)
            # Принудительно перерисовать prompt — иначе prompt_toolkit может
            # стереть строки над собой при следующем нажатии.
            try:
                event.app.invalidate()
            except Exception:
                pass

        # ── BracketedPaste ──
        @kb.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            data = event.data or ""
            if data:
                data = data.replace("\r\n", "\n").replace("\r", "\n")
                event.current_buffer.insert_text(data)

        return kb

    def clear_images(self):
        self.pending_images = []
        self._image_counter = 0

    def get_and_clear_images(self) -> list[Path]:
        images = self.pending_images[:]
        self.pending_images = []
        self._image_counter = 0
        return images

    def set_activity_status(self, status: str, session=None) -> None:
        if status not in ("idle", "working", "poll", "done"):
            status = "idle"
        self.activity_status = status
        if session is not None:
            self.session = session
        try:
            from ui.terminal_title import set_session_terminal_title, set_activity_status
            set_activity_status(status)
            if self.session is not None:
                set_session_terminal_title(self.session, status)
        except Exception:
            logger.debug("prompt activity status update failed", exc_info=True)

    def _activity_emoji(self) -> str:
        try:
            from ui.terminal_title import activity_emoji
            return activity_emoji(self.activity_status)
        except Exception:
            return "💤"

    def _mode_fragments(self):
        if self.mode == "planning":
            return [
                ("", "🧠 "),
                ("#e6a817 bold", "plan"),
                ("class:prompt-arrow", " > "),
            ]
        if self.mode == "autonomous":
            return [
                ("", "🔮 "),
                (f"{t('purple')} bold", "auto"),
                ("class:prompt-arrow", " > "),
            ]
        return [
            ("", "🚀 "),
            (f"{t('success')} bold", "agent"),
            ("class:prompt-arrow", " > "),
        ]

    def _make_separator_fragments(self, status_text: Optional[str] = None):
        w = _get_term_width()

        def _vw(s: str) -> int:
            n = wcswidth(s)
            return n if n >= 0 else len(s)

        has_complete_bar = (
            status_text
            and BAR_FILLED_START in status_text
            and BAR_FILLED_END in status_text
            and BAR_EMPTY_START in status_text
            and BAR_EMPTY_END in status_text
        )
        if has_complete_bar:
            parts = []
            rest = status_text

            before, rest = rest.split(BAR_FILLED_START, 1)
            filled, rest = rest.split(BAR_FILLED_END, 1)
            empty_part, rest = rest.split(BAR_EMPTY_START, 1)
            empty, after = rest.split(BAR_EMPTY_END, 1)

            prefix = "\u2500\u2500\u2500 "
            suffix = " "
            visible_len = _vw(prefix) + _vw(before) + _vw(filled) + _vw(empty) + _vw(after) + _vw(suffix)
            remaining = max(0, w - visible_len)
            tail = "\u2500" * remaining

            parts.append(("class:separator", prefix))
            parts.append(("class:status-text", before))
            parts.append(("class:bar-filled", filled))
            parts.append(("class:bar-empty", empty))
            parts.append(("class:status-text", after))
            parts.append(("class:separator", suffix + tail))
            return parts
        elif status_text:
            prefix = "\u2500\u2500\u2500 "
            suffix = " "
            inner_len = _vw(prefix) + _vw(status_text) + _vw(suffix)
            remaining = max(0, w - inner_len)
            tail = "\u2500" * remaining
            return [
                ("class:separator", prefix),
                ("class:status-text", status_text),
                ("class:separator", suffix + tail),
            ]
        sep = "\u2500" * w
        return [("class:separator", sep)]

    def _make_prompt_fragments(self, status_text: Optional[str] = None):
        return self._mode_fragments()

    def _print_separator(self, status_text: Optional[str] = None):
        ptk_print(FormattedText(self._make_separator_fragments(status_text)), style=_build_style())

    async def read(
        self,
        status_text: Optional[str] = None,
        bg_resume: bool = False,
    ):
        """Читает ввод пользователя.

        bg_resume=True: пока ждём ввод, параллельно следим за завершением
        фоновых задач. Если задача завершилась И поле ввода ПУСТО (пользователь
        не печатает — не мешаем ему) — прерываем ожидание и возвращаем
        _BG_RESUME, чтобы REPL разбудил агента. Если в буфере есть текст —
        не трогаем, ждём отправки.
        """
        self._last_status_text = status_text
        try:
            self._print_separator(status_text)
            if bg_resume:
                result = await self._read_with_bg_resume(status_text)
                if result is _BG_RESUME:
                    return _BG_RESUME
            else:
                result = await self._session.prompt_async(
                    lambda: self._make_prompt_fragments(),
                    bottom_toolbar=None,
                )
            cleaned = result.strip() if result else ""
            if cleaned:
                self._echo_submitted(result)
            return cleaned
        except EOFError:
            return _EOF
        except KeyboardInterrupt:
            return None

    async def _read_with_bg_resume(self, status_text: Optional[str]):
        """prompt_async, прерываемый завершением фоновой задачи (если буфер пуст).

        Возвращает строку ввода либо _BG_RESUME. EOFError/KeyboardInterrupt
        пробрасываются наружу (обрабатываются в read()).
        """
        import asyncio

        from tools.background import (
            clear_finish_event,
            get_finish_event,
            has_pending_finished,
        )

        prompt_task = asyncio.ensure_future(
            self._session.prompt_async(
                lambda: self._make_prompt_fragments(),
                bottom_toolbar=None,
            )
        )

        while True:
            finish_ev = get_finish_event()
            # Нет моста (Event не привязан) — обычное ожидание ввода.
            if finish_ev is None:
                return await prompt_task

            bg_task = asyncio.ensure_future(finish_ev.wait())
            try:
                done, _pending = await asyncio.wait(
                    {prompt_task, bg_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                for t in (prompt_task, bg_task):
                    if not t.done():
                        t.cancel()
                raise

            if prompt_task in done:
                # Пользователь отправил ввод — он приоритетнее.
                if not bg_task.done():
                    bg_task.cancel()
                return prompt_task.result()

            # Сработал bg_task: фоновая задача завершилась.
            clear_finish_event()
            buffer_has_text = bool(
                (self._session.app.current_buffer.text or "").strip()
            )
            if buffer_has_text or not has_pending_finished():
                # Пользователь печатает (не мешаем) ИЛИ ложное срабатывание
                # (всё уже доставлено) — продолжаем ждать ввод, перевзводим Event.
                continue
            # Поле пусто и есть что доставить → прерываем ввод, будим агента.
            if not prompt_task.done():
                self._session.app.exit(result="")
                try:
                    await prompt_task
                except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
                    pass
                except Exception:
                    logger.debug("prompt_task raised on bg-exit", exc_info=True)
            return _BG_RESUME

    def _echo_submitted(self, text: str) -> None:
        """Перепечатывает отправленный ввод белым текстом на сером фоне на всю
        ширину (многострочно). Сначала стирает строки, которые prompt_toolkit
        оставил в скроллбэке (prompt + перенесённые строки ввода)."""
        w = _get_term_width()

        def _vw(s: str) -> int:
            n = wcswidth(s)
            return n if n >= 0 else len(s)

        # prompt-строка: "🚀 agent > " (видимая ширина) — её prompt_toolkit
        # печатает перед текстом. Считаем сколько визуальных строк заняла
        # вся реплика (prompt+text с учётом wrap), чтобы поднять курсор.
        if self.mode == "planning":
            mode_prefix = "🧠 plan > "
        elif self.mode == "autonomous":
            mode_prefix = "🔮 auto > "
        else:
            mode_prefix = "🚀 agent > "
        prefix_w = _vw(mode_prefix)
        rows = 0
        for i, ln in enumerate(text.split("\n")):
            line_w = (prefix_w if i == 0 else 0) + _vw(ln)
            rows += max(1, (line_w + w - 1) // w) if line_w else 1

        # Пишем напрямую в реальный терминал (sys.__stdout__), а не через
        # обёртку prompt_toolkit — иначе escape-коды печатаются как текст
        # ("?[JA") и prompt_toolkit ломает курсор.
        out = sys.__stdout__
        try:
            # Курсор после Enter — на строке под вводом. Поднимаемся к началу
            # prompt-строки (rows вверх), в начало строки, стираем до конца экрана.
            out.write(f"\033[{rows}A\r\033[J")
            bg = t("bg_code")
            fg = "97"  # bright white
            # bg_code вида "#1a1a2e" → 24-bit ANSI
            bg_seq = ""
            if bg.startswith("#") and len(bg) == 7:
                r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
                bg_seq = f"48;2;{r};{g};{b}"
            # Маппинг [imageN] → путь для OSC 8 file://-гиперссылок (Ctrl+клик).
            image_paths = {
                f"[image{idx}]": p
                for idx, p in enumerate(self.pending_images, start=1)
            }

            def _linkify(seg: str) -> str:
                # Оборачивает [imageN] в OSC 8 file://-ссылку + underline.
                # ширину не меняет (escape-коды невидимы).
                if not image_paths:
                    return seg
                def _repl(m):
                    ph = m.group(0)
                    p = image_paths.get(ph)
                    if p is None:
                        return ph
                    uri = Path(p).resolve().as_uri()
                    return f"\033]8;;{uri}\033\\\033[4m{ph}\033[24m\033]8;;\033\\"
                return _ImageHighlighter._PATTERN.sub(_repl, seg)

            lines = text.split("\n")
            for i, ln in enumerate(lines):
                prefix = mode_prefix if i == 0 else " "
                filled = prefix + ln
                pad = max(0, w - _vw(filled))
                body = _linkify(prefix + ln) + " " * pad
                if bg_seq:
                    out.write(f"\033[1;{fg};{bg_seq}m{body}\033[0m\n")
                else:
                    out.write(f"\033[1;{fg}m{body}\033[0m\n")
            from datetime import datetime
            now = datetime.now().strftime("%H:%M:%S")
            pad = max(0, w - _vw(now))
            out.write(f"\033[38;5;250m{' ' * pad}{now}\033[0m\n")
            out.flush()
        except Exception:
            logger.debug("echo_submitted failed", exc_info=True)

    def reprint_separator(self) -> None:
        """Перерисовать separator со статусом (для Ctrl+O после clear).

        Печатаем в реальный stdout с явным output, чтобы prompt-toolkit
        видел строку как внешний вывод и не стирал её при rerender.
        """
        import sys
        from prompt_toolkit.output.defaults import create_output
        status = getattr(self, "_last_status_text", None)
        if not status and callable(getattr(self, "status_provider", None)):
            try:
                status = self.status_provider()
                self._last_status_text = status or ""
            except Exception:
                logger.debug("reprint_separator status_provider failed", exc_info=True)
        logger.debug("reprint_separator: status_len=%d", len(status or ""))
        fragments = self._make_separator_fragments(status)
        try:
            out = create_output(stdout=sys.__stdout__)
            ptk_print(
                FormattedText(fragments),
                style=_build_style(),
                output=out,
            )
        except Exception:
            sys.__stdout__.write("\u2500" * 80 + "\n")
            sys.__stdout__.flush()
