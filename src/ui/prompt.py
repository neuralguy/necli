"""Адаптер ввода поверх `ui.shell.Shell`.

Раньше здесь жил свой `PromptSession`: он сам читал stdin, сам рисовал
separator со статусом и сам печатал эхо, отматывая курсор вверх. Теперь
экраном владеет единственный Application (`ui/shell.py`), а поле ввода
доступно всегда — в том числе пока агент отвечает. Поэтому от прежнего класса
осталась только та часть, которую Shell не знает и знать не должен:

- вставка из буфера обмена (Ctrl+V текст/картинка, Ctrl+P картинка,
  bracketed paste с маркером `[Pasted N chars]`);
- учёт вставленных картинок и маппинг `[imageN]` → путь;
- полноширинное эхо отправленной реплики (его печатает воркер очереди
  перед началом ответа — см. `commands/agent_queue`);
- статус активности в заголовке терминала.

Имена атрибутов и методов сохранены: на `state.prompt_input` смотрят
`agent/executor.py`, `agent/tg_menu.py`, `agent/render_replay.py`,
`commands/slash_handler.py`.

Клавиши: Enter отправляет, Esc+Enter и `\\`+Enter — перенос строки,
Ctrl+C чистит ввод (а во время хода — прерывает его), Ctrl+D — выход,
Ctrl+V — вставка, Ctrl+P — картинка из буфера обмена.
"""

import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from prompt_toolkit import print_formatted_text as ptk_print
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.processors import Processor, Transformation
from prompt_toolkit.styles import Style
from wcwidth import wcswidth

from config.i18n import t as _i18n
from config.themes import ansi_24bit, t
from ui.formatting import BAR_EMPTY_END, BAR_EMPTY_START, BAR_FILLED_END, BAR_FILLED_START
from ui.shell import get_shell

logger = logging.getLogger(__name__)


def _build_style():
    return Style.from_dict(
        {
            "prompt": f"bold {t('accent')}",
            "prompt-arrow": f"bold {t('success')}",
            "separator": t("muted"),
            "status-text": f"bold {t('fg_primary')}",
            "bar-filled": t("bar_filled"),
            "bar-empty": t("muted"),
        }
    )


# Возвращался из read() при Ctrl+D. Сам read() уехал в Shell (SUBMIT_EOF), но
# сентинел реэкспортируется из ui/__init__.py — оставляем.
_EOF = object()


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
        except FileNotFoundError:  # noqa: PERF203
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


def _vw(s: str) -> int:
    """Видимая ширина строки в ячейках терминала."""
    n = wcswidth(s)
    return n if n >= 0 else len(s)


class _ImageHighlighter(Processor):
    _PATTERN = re.compile(r'(\[image\d+\])')

    def apply_transformation(self, ti):
        fragments = []
        for style, text, *_rest in ti.fragments:
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
    """Тонкая обёртка над Shell: вставки, картинки, эхо, статус активности."""

    def __init__(self, working_dir: str = ".", on_mode_toggle=None, shell=None):
        self.pending_images: list[Path] = []
        self._image_counter = 0
        # Режим живёт в Shell (его переключает Tab). Локальное поле — только
        # для случаев без Application: тесты и headless-режим.
        self._mode: str = "agent"
        self.session = None
        # Callback пересчёта status-строки для Ctrl+O reprint.
        self.status_provider = None
        self._last_status_text: str | None = None
        self._pasted_texts: dict[str, list[str]] = {}
        self._submitted_text: str | None = None
        self._working_dir = working_dir
        self._shell = None
        if shell is not None:
            self.attach(shell)

    # ────────────────────────────── связь с Shell ──────────────────────────
    @property
    def shell(self):
        return self._shell or get_shell()

    def attach(self, shell) -> None:
        """Досоединяет к Shell то, чего в ядре нет: вставки и подсветку картинок.

        Ядро Shell общее для всех подсистем, поэтому клавиши буфера обмена
        доклеиваются сюда, в его KeyBindings, а не форком ядра.
        """
        self._shell = shell
        self._bind_paste_keys(shell)
        try:
            control = shell.input_window.content
            control.input_processors = [
                *(control.input_processors or []),
                _ImageHighlighter(),
            ]
        except Exception:
            logger.debug("image highlighter attach failed", exc_info=True)

    @property
    def mode(self) -> str:
        sh = self._shell
        return sh.mode if sh is not None else self._mode

    @mode.setter
    def mode(self, value: str) -> None:
        self._mode = value
        sh = self._shell
        if sh is not None:
            sh.mode = value
            sh.invalidate()

    def set_working_dir(self, path: str):
        """Update the working directory for file autocomplete."""
        self._working_dir = path
        sh = self._shell
        if sh is not None:
            sh.set_working_dir(path)

    # ───────────────────────────── клавиши вставки ─────────────────────────
    def _overlay_owns_keys(self, key: str, event) -> bool:
        """Пока нижней зоной владеет оверлей, вставлять в поле ввода нельзя:
        отдаём клавишу оверлею и уходим."""
        sh = self._shell
        if sh is None or sh.overlay is None:
            return False
        try:
            sh.overlay.handle_key(key, event)
        except Exception:
            logger.debug("overlay handle_key failed", exc_info=True)
        sh.invalidate()
        return True

    def _bind_paste_keys(self, shell) -> None:
        kb = shell.kb

        def paste_into_overlay(text: str) -> bool:
            """Вставить текст в активное ask_text-поле, если оно открыто."""
            sh = self._shell
            if sh is None or sh.overlay is None or not sh.overlay.wants_text:
                return False
            # Текущие текстовые оверлеи однострочные. Терминалы часто кладут
            # в clipboard завершающий LF — превращаем переводы строк в пробелы,
            # а не отклоняем всю вставку.
            normalized = text.replace("\r\n", "\n").replace("\r", "\n")
            sh.overlay_buffer.insert_text(normalized.replace("\n", " "))
            sh.invalidate()
            return True

        @kb.add("c-v", eager=True)
        def _paste(event):
            # Ctrl+V универсален: сначала пробуем картинку, потом текст.
            sh = self._shell
            if sh is not None and sh.overlay is not None:
                if sh.overlay.wants_text:
                    text = _get_clipboard_text()
                    if text:
                        paste_into_overlay(text)
                    else:
                        sh.invalidate()
                else:
                    self._overlay_owns_keys("c-v", event)
                return
            if not self._insert_image(shell.input_buffer):
                text = _get_clipboard_text()
                if text:
                    text = text.replace("\r\n", "\n").replace("\r", "\n")
                    self._insert_pasted_text(shell.input_buffer, text)
            shell.invalidate()

        @kb.add("c-p", eager=True)
        def _paste_image(event):
            if self._overlay_owns_keys("c-p", event):
                return
            self._insert_image(shell.input_buffer)
            shell.invalidate()

        @kb.add(Keys.BracketedPaste)
        def _bracketed_paste(event):
            data = (event.data or "").replace("\r\n", "\n").replace("\r", "\n")
            if not data:
                return
            sh = self._shell
            if sh is not None and sh.overlay is not None:
                if sh.overlay.wants_text:
                    paste_into_overlay(data)
                else:
                    sh.invalidate()
                return
            self._insert_pasted_text(shell.input_buffer, data)
            shell.invalidate()

    # ────────────────────────────── картинки ───────────────────────────────
    def _session_images_dir(self) -> Path | None:
        """Папка для картинок текущей сессии: <session.dir>/clipboard_images."""
        sess = self.session
        sess_dir = getattr(sess, "dir", None) if sess is not None else None
        if sess_dir is None:
            return None
        return Path(sess_dir) / "clipboard_images"

    def _try_grab_image(self) -> Path | None:
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

    def clear_images(self):
        self.pending_images = []
        self._image_counter = 0

    def get_and_clear_images(self) -> list[Path]:
        images = self.pending_images[:]
        self.pending_images = []
        self._image_counter = 0
        return images

    # ───────────────────────── многострочные вставки ───────────────────────
    def _insert_pasted_text(self, buf, text: str) -> None:
        """Вставляет многострочный текст как маркер, сохраняя оригинал до отправки."""
        if "\n" not in text:
            buf.insert_text(text)
            return
        marker = _i18n("prompt.pasted_chars", n=len(text))
        pending = self._pasted_texts.setdefault(marker, [])
        # Если такого маркера в буфере уже нет — предыдущую вставку отменили
        # (Ctrl+C чистит ввод внутри Shell, и хука отмены у нас больше нет).
        # Иначе устаревший текст подставился бы вместо новой вставки.
        if marker not in (getattr(buf, "text", "") or ""):
            pending.clear()
        pending.append(text)
        buf.insert_text(marker)

    def _expand_pasted_text(self, text: str) -> str:
        """Раскрывает маркеры вставок перед сохранением и отправкой."""
        for marker, pasted_texts in self._pasted_texts.items():
            while pasted_texts and marker in text:
                text = text.replace(marker, pasted_texts.pop(0), 1)
        self._pasted_texts.clear()
        return text

    def _expand_for_history(self, text: str) -> str:
        """Раскрывает вставки для истории, не меняя видимый буфер."""
        self._submitted_text = self._expand_pasted_text(text)
        return self._submitted_text

    def expand_submitted(self, text: str) -> str:
        """Раскрывает `[Pasted N chars]` в реальный текст отправленной реплики.

        Раньше это делала обёртка истории prompt_toolkit в момент записи; теперь
        буфером владеет Shell, поэтому раскрываем в главном цикле — до эха и до
        постановки в очередь, чтобы агент получил текст, а не маркер.
        """
        return self._expand_for_history(text)

    # ───────────────────────────── статус активности ───────────────────────
    def set_activity_status(self, status: str, session=None) -> None:
        if status not in ("idle", "working", "poll", "done"):
            status = "idle"
        if session is not None:
            self.session = session
        try:
            from ui.terminal_title import set_activity_status, set_session_terminal_title
            set_activity_status(status)
            if self.session is not None:
                set_session_terminal_title(self.session, status)
        except Exception:
            logger.debug("prompt activity status update failed", exc_info=True)

    # ─────────────────────── separator (нужен replay'ю) ────────────────────
    def _make_separator_fragments(self, status_text: str | None = None):
        w = _get_term_width()

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
            _empty_part, rest = rest.split(BAR_EMPTY_START, 1)
            empty, after = rest.split(BAR_EMPTY_END, 1)

            prefix = "─── "
            suffix = " "
            visible_len = _vw(prefix) + _vw(before) + _vw(filled) + _vw(empty) + _vw(after) + _vw(suffix)
            remaining = max(0, w - visible_len)
            tail = "─" * remaining

            parts.append(("class:separator", prefix))
            parts.append(("class:status-text", before))
            parts.append(("class:bar-filled", filled))
            parts.append(("class:bar-empty", empty))
            parts.append(("class:status-text", after))
            parts.append(("class:separator", suffix + tail))
            return parts
        elif status_text:
            prefix = "─── "
            suffix = " "
            inner_len = _vw(prefix) + _vw(status_text) + _vw(suffix)
            remaining = max(0, w - inner_len)
            tail = "─" * remaining
            return [
                ("class:separator", prefix),
                ("class:status-text", status_text),
                ("class:separator", suffix + tail),
            ]
        sep = "─" * w
        return [("class:separator", sep)]

    def reprint_separator(self) -> None:
        """Печатает статус-линию в scrollback.

        Внутри Application линия рамки рисуется сама, поэтому здесь это нужно
        только там, где Application временно не владеет экраном (replay в
        headless-режиме).
        """
        status = getattr(self, "_last_status_text", None)
        if not status and callable(getattr(self, "status_provider", None)):
            try:
                status = self.status_provider()
                self._last_status_text = status or ""
            except Exception:
                logger.debug("reprint_separator status_provider failed", exc_info=True)
        fragments = self._make_separator_fragments(status)
        try:
            from prompt_toolkit.output.defaults import create_output
            out = create_output(stdout=sys.__stdout__)
            ptk_print(FormattedText(fragments), style=_build_style(), output=out)
        except Exception:
            sys.__stdout__.write("─" * _get_term_width() + "\n")
            sys.__stdout__.flush()

    # ──────────────────────────────── эхо ввода ────────────────────────────
    def _mode_prefix(self) -> str:
        if self.mode == "planning":
            return "🧠 plan > "
        if self.mode == "swarm":
            return "🔮 swarm > "
        return "🚀 agent > "

    def render_echo(self, text: str) -> str:
        """Собирает полосу эха: bold bright-white на фоне bg_code во всю ширину,
        префикс режима на первой строке, `[imageN]` как OSC-8 file://-ссылки,
        и справа снизу серое время отправки."""
        w = _get_term_width()
        mode_prefix = self._mode_prefix()

        bg = t("bg_code")
        bg_seq = ""
        if isinstance(bg, str) and bg.startswith("#") and len(bg) == 7:
            r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
            bg_seq = f"48;2;{r};{g};{b}"
        fg = ansi_24bit(t("fg_primary"))

        # Маппинг [imageN] → путь для OSC 8 file://-гиперссылок (Ctrl+клик).
        image_paths = {
            f"[image{idx}]": p
            for idx, p in enumerate(self.pending_images, start=1)
        }

        def _linkify(seg: str) -> str:
            # Оборачивает [imageN] в OSC 8 file://-ссылку + underline.
            # Ширину не меняет (escape-коды невидимы).
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

        out: list[str] = []
        for i, ln in enumerate(text.split("\n")):
            prefix = mode_prefix if i == 0 else " "
            pad = max(0, w - _vw(prefix + ln))
            body = _linkify(prefix + ln) + " " * pad
            if bg_seq:
                out.append(f"\033[1;{fg};{bg_seq}m{body}\033[0m\n")
            else:
                out.append(f"\033[1;{fg}m{body}\033[0m\n")
        now = datetime.now().strftime("%H:%M:%S")
        out.append(f"\033[38;5;250m{' ' * max(0, w - _vw(now))}{now}\033[0m\n")
        return "".join(out)

    def echo_submitted(self, text: str) -> None:
        """Печатает эхо реплики в scrollback.

        Курсор больше не отматывается вверх: поле ввода живёт внутри
        Application и стирается им самим — стирать нечего, а прежний
        `\\033[NA\\033[J` съел бы чужой вывод.
        """
        if not (text or "").strip():
            return
        try:
            block = self.render_echo(text)
        except Exception:
            logger.debug("render_echo failed", exc_info=True)
            return
        sh = self.shell
        if sh is not None:
            sh.print_static_raw(block)
            return
        try:
            sys.__stdout__.write(block)
            sys.__stdout__.flush()
        except Exception:
            logger.debug("echo_submitted failed", exc_info=True)
