"""Keyboard-event responsibilities of the interactive terminal shell."""

from __future__ import annotations

import logging
import time

from prompt_toolkit.application import run_in_terminal
from prompt_toolkit.key_binding import KeyBindings

from .buffer_editing import _default_buffer_key
from .submissions import SUBMIT_EOF, SUBMIT_INTERRUPT

logger = logging.getLogger(__name__)


class ShellKeyBindingMixin:
    def _build_keys(self) -> None:
        kb = KeyBindings()
        self.kb = kb

        def overlay_takes(key: str, event) -> bool:
            if self.overlay is None:
                return False
            # В поле свободного ввода пробел — это символ, а не команда. Без
            # этой нормализации он приходит именем "space", не проходит проверку
            # `len(key) == 1` и молча теряется: "user name" → "username".
            # Оверлеям, где пробел действие (чекбоксы в poll), имя оставляем.
            if key == "space" and self.overlay.wants_text:
                key = " "
            try:
                return bool(self.overlay.handle_key(key, event))
            except Exception:
                logger.warning("overlay.handle_key failed", exc_info=True)
                return True

        @kb.add("escape")
        def _esc(event):
            if overlay_takes("escape", event):
                self.invalidate()
                return
            if self.overlay is not None:
                self.overlay.finish(None)
            self.invalidate()

        @kb.add("up", eager=True)
        def _up(event):
            if overlay_takes("up", event):
                self.invalidate()
                return
            if self._row_focus >= 0:
                # со строк субагентов возвращаемся к вводу
                self._row_focus -= 1
                self.invalidate()
                return
            buf = self.input_buffer
            if buf.complete_state:
                buf.complete_previous()
            elif not buf.text and self._queued_messages and self.on_edit_queued:
                text = self.on_edit_queued()
                if text is not None:
                    buf.text = text
                    buf.cursor_position = len(text)
            else:
                buf.auto_up()
            self.invalidate()

        @kb.add("down", eager=True)
        def _down(event):
            if overlay_takes("down", event):
                self.invalidate()
                return
            groups = self._all_rows()
            buf = self.input_buffer
            if self._row_focus >= 0:
                self._row_focus = min(len(groups) - 1, self._row_focus + 1)
                self.invalidate()
                return
            if buf.complete_state:
                buf.complete_next()
                self.invalidate()
                return
            # Вниз из пустого ввода — переход на строки субагентов. Если в
            # буфере текст, вниз остаётся навигацией по истории: иначе
            # пользователь терял бы каретку посреди набора.
            if groups and not buf.text.strip():
                self._row_focus = 0
            else:
                buf.auto_down()
            self.invalidate()

        @kb.add("enter", eager=True)
        def _enter(event):
            if overlay_takes("enter", event):
                self.invalidate()
                return
            if self._row_focus >= 0:
                groups = self._all_rows()
                if 0 <= self._row_focus < len(groups):
                    groups[self._row_focus].open()
                self.invalidate()
                return
            buf = self.input_buffer
            # принятие подсказки автодополнения не должно отправлять строку
            if buf.complete_state and buf.complete_state.current_completion:
                buf.apply_completion(buf.complete_state.current_completion)
                self.invalidate()
                return
            line = buf.document.current_line_before_cursor
            if line.endswith("\\"):
                buf.delete_before_cursor(count=1)
                buf.insert_text("\n")
                self.invalidate()
                return
            text = buf.text
            buf.text = ""
            self._submit_text(text)
            self.invalidate()

        @kb.add("escape", "enter", eager=True)
        def _newline(event):
            if self.overlay is None:
                self.input_buffer.insert_text("\n")
                self.invalidate()

        @kb.add("escape", "backspace", eager=True)
        def _alt_backspace(event):
            if overlay_takes("a-backspace", event):
                self.invalidate()
                return
            _default_buffer_key(self, "a-backspace", event)
            self._restart_completion()
            self.invalidate()

        @kb.add("c-c", eager=True)
        def _ctrl_c(event):
            if overlay_takes("c-c", event):
                self.invalidate()
                return
            if self.overlay is not None:
                self.overlay.finish(None)
                self.invalidate()
                return
            if self.input_buffer.text:
                self.input_buffer.reset()
            else:
                self.submissions.put_nowait((SUBMIT_INTERRUPT, None))
            self.invalidate()

        @kb.add("c-d", eager=True)
        def _ctrl_d(event):
            if overlay_takes("c-d", event):
                return
            if self.overlay is None and not self.input_buffer.text:
                if self._confirm_exit_active():
                    # Второе нажатие в течение 3 с — выход сразу.
                    self._confirm_exit_until = None
                    self.submissions.put_nowait((SUBMIT_EOF, None))
                else:
                    # Первое нажатие — показать подсказку и ждать 3 с.
                    self._confirm_exit_until = time.monotonic() + 3.0
                    self.invalidate()

        @kb.add("tab", eager=True)
        def _tab(event):
            if overlay_takes("tab", event):
                self.invalidate()
                return
            # Tab переключает режим ВСЕГДА, даже когда в поле уже что-то
            # набрано: так было до реворка и на это опираются пальцы. Варианты
            # автодополнения выбираются стрелками ↑↓ и принимаются Enter, а
            # появляются они сами (complete_while_typing); принудительный
            # вызов повешен на ctrl+space.
            order = ("agent", "planning", "swarm")
            idx = order.index(self.mode) if self.mode in order else 0
            self.mode = order[(idx + 1) % len(order)]
            if self.on_mode_toggle:
                try:
                    self.on_mode_toggle(self.mode)
                except Exception:
                    logger.debug("on_mode_toggle failed", exc_info=True)
            self.invalidate()

        @kb.add("c-space", eager=True)
        def _force_complete(event):
            """Явный вызов автодополнения — вместо отобранного Tab'а."""
            if overlay_takes("c-space", event):
                self.invalidate()
                return
            self._restart_completion()
            self.invalidate()

        @kb.add("c-o", eager=True)
        def _ctrl_o(event):
            if self.on_ctrl_o:
                run_in_terminal(self.on_ctrl_o)
            self.invalidate()

        # Клавиши, которые должны доезжать до оверлея как есть.
        for key in (
            "left",
            "right",
            "home",
            "end",
            "c-left",
            "c-right",
            "c-a",
            "c-e",
            "c-w",
            "c-u",
            "c-k",
            "pageup",
            "pagedown",
            "backspace",
            "delete",
            "c-delete",
            "c-p",
            "c-n",
            "c-x",
            "c-s",
            "space",
            "f5",
        ):

            def _make(k):
                def _h(event):
                    if overlay_takes(k, event):
                        self.invalidate()
                        return
                    # вне оверлея — обычное поведение буфера
                    _default_buffer_key(self, k, event)
                    if k in ("backspace", "delete", "c-delete"):
                        self._restart_completion()
                    self.invalidate()

                return _h

            kb.add(key, eager=True)(_make(key))

        @kb.add("<any>")
        def _any(event):
            if self.overlay is not None:
                data = event.data
                if self.overlay.wants_text:
                    self.overlay_buffer.insert_text(data)
                else:
                    overlay_takes(data, event)
                self.invalidate()
                return
            # Начал печатать, стоя на строке субагентов — возвращаем каретку в
            # поле ввода, иначе символы уходили бы в поле, а маркер «❯» висел
            # бы на строке: пользователь не понимает, куда он печатает.
            if self._row_focus >= 0:
                self._row_focus = -1
            self.input_buffer.insert_text(event.data)
            self.invalidate()
