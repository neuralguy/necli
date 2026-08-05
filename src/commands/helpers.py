import asyncio
import logging
import os
import sys
import time

# termios для сохранения/восстановления состояния терминала после Ctrl+C.
# Если импорт не удался (Windows) — функции-заглушки отработают без ошибки.
try:
    import termios as _termios
    _HAVE_TERMIOS = True
except ImportError:
    _HAVE_TERMIOS = False

from rich.align import Align
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

import models as app_models
import session.storage as storage
from config.i18n import t as _
from config.themes import ansi_24bit, t
from session import Session
from ui import format_tokens

logger = logging.getLogger(__name__)
console = Console()

# ── Terminal state save/restore ───────────────────────────────────

_SAVED_TERMIOS: list | None = None


def _save_termios() -> None:
    """Сохраняет termios-атрибуты stdin для восстановления после Ctrl+C."""
    global _SAVED_TERMIOS
    if not _HAVE_TERMIOS:
        return
    try:
        fd = sys.stdin.fileno()
        _SAVED_TERMIOS = _termios.tcgetattr(fd)
    except Exception:
        _SAVED_TERMIOS = None


def _restore_termios() -> None:
    """Восстанавливает сохранённые termios-атрибуты stdin.

    Fallback: stty sane если _SAVED_TERMIOS невалиден.
    """
    global _SAVED_TERMIOS
    if not _HAVE_TERMIOS:
        return
    fd = sys.stdin.fileno()
    term = _SAVED_TERMIOS
    # Отцепляем глобальную ссылку — при повторах хендлера сохранится свежий снимок.
    _SAVED_TERMIOS = None

    if term is not None:
        try:
            _termios.tcsetattr(fd, _termios.TCSADRAIN, term)
            return
        except Exception:
            logger.debug("restore_termios: tcsetattr failed", exc_info=True)

    # Fallback: stty sane
    try:
        import subprocess as _sp
        _sp.run(["stty", "sane"], stderr=_sp.DEVNULL, timeout=2)
    except Exception:
        pass


def _read_version() -> str:
    try:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as _pkg_version
        try:
            return _pkg_version("necli-api")
        except PackageNotFoundError:
            pass
    except Exception:
        logger.debug("importlib.metadata version lookup failed", exc_info=True)

    try:
        from pathlib import Path
        # helpers.py → commands → src → <корень репо>/pyproject.toml.
        # Проверяем оба варианта раскладки (src-layout и flat) на случай
        # перемещения файла.
        here = Path(__file__).resolve()
        for root in (here.parent.parent.parent, here.parent.parent):
            pyproject = root / "pyproject.toml"
            if not pyproject.exists():
                continue
            in_project_table = False
            for raw in pyproject.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if line.startswith("["):
                    # version читаем только из таблицы [project], а не из
                    # [tool.*] и прочих секций, где может быть своё version.
                    in_project_table = line == "[project]"
                    continue
                if in_project_table and line.startswith("version"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        ver = parts[1].split("#", 1)[0].strip().strip('"').strip("'")
                        if ver:
                            return ver
    except Exception:
        logger.debug("pyproject version read failed", exc_info=True)

    return "0.0.0"


_APP_VERSION = _read_version()


def _notice(markup: str) -> None:
    """Служебное сообщение в scrollback над рамкой.

    Раньше такие строки писались прямо в stderr с `\\r\\033[K`: тогда курсором
    владел код агента. Теперь экраном владеет Application, и любая прямая
    запись рвала бы рамку — печатаем через единственную легальную дверь.
    """
    from ui.shell import print_static
    print_static(markup)


class InterruptController:
    """Три уровня Ctrl+C, управляемые событием из Shell, а не сигналом SIGINT.

    prompt_toolkit держит терминал в raw-режиме, поэтому Ctrl+C приходит в
    Application **клавишей**, а ядро SIGINT не посылает — прежний
    `signal.signal(SIGINT, …)` не сработал бы ни разу. Эскалацию двигает
    главный цикл, получив из `shell.submissions` событие SUBMIT_INTERRUPT.
    Смысл уровней сохранён один в один:

      1 — `ctx.interrupted`: цикл агента доделывает текущий шаг и встаёт;
      2 — `ctx.hard_interrupted` + `cancel()` задачи хода;
      3 — `os._exit(130)`: отмена зависла в неотменяемом коде.

    Счётчик обнуляется на каждом новом ходе (`begin`) — иначе второй Ctrl+C
    в следующем ходе сразу давал бы жёсткую отмену.
    """

    def __init__(self) -> None:
        self.level: int = 0
        self.task: asyncio.Task | None = None
        self.hard_at: float | None = None
        self._saved_stderr = None

    # ── жизненный цикл хода ──
    def begin(self, task: asyncio.Task) -> None:
        self.task = task
        self.level = 0
        self.hard_at = None

    def end(self, task: asyncio.Task) -> None:
        if self.task is task:
            self.task = None
        self.restore_stderr()

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    # ── подавление трейсбеков на жёсткой отмене ──
    def silence_stderr(self) -> None:
        """На level 2 отмена рвёт стек в самых разных местах; их трейсбеки
        пользователю не нужны и порвали бы рамку."""
        if self._saved_stderr is not None:
            return
        try:
            self._saved_stderr = sys.stderr
            sys.stderr = open(os.devnull, "w")  # noqa: SIM115
        except Exception:
            self._saved_stderr = None
            logger.debug("stderr redirect to devnull failed", exc_info=True)

    def restore_stderr(self) -> None:
        saved = self._saved_stderr
        if saved is None:
            return
        self._saved_stderr = None
        if sys.stderr is not saved:
            try:
                sys.stderr.close()
            except Exception:
                logger.debug("stderr devnull close failed", exc_info=True)
        sys.stderr = saved

    @staticmethod
    def _invalidate_ui() -> None:
        """Перерисовать Working сразу, не дожидаясь следующего SSE-чанка."""
        try:
            from ui.shell import get_shell
            shell = get_shell()
            if shell is not None:
                shell.invalidate()
        except Exception:
            logger.debug("interrupt UI invalidate failed", exc_info=True)

    # ── собственно эскалация ──
    def escalate(self) -> int:
        """Обработать очередной Ctrl+C. Возвращает достигнутый уровень.

        Вне хода (0) ничего не делает: на простое Ctrl+C только чистит ввод —
        это делает сам Shell, и убивать процесс тут нельзя.
        """
        if not self.active:
            return 0
        from agent import get_current_ctx
        ctx = get_current_ctx()
        self.level += 1

        if self.level == 1:
            # Мягкое прерывание: цикл доделает текущую итерацию и остановится.
            if ctx:
                ctx.interrupted = True
                ctx.interrupt_level = 1
            self._invalidate_ui()
            return 1

        # level >= 3: cancel завис (неотменяемый синхронный код / C-расширение) —
        # аварийный выход всего процесса. Лучше так, чем висеть бесконечно.
        if self.level >= 3:
            _restore_termios()
            # print_static здесь недоступен: он печатает через run_in_terminal,
            # то есть на следующем шаге loop'а, которого уже не будет.
            _write_now(f"\r\033[K  \033[{ansi_24bit(t('error'))}m■■■\033[0m \033[2mForce exit.\033[0m\n")
            os._exit(130)

        # level == 2: жёсткая отмена задачи прямо сейчас.
        #
        # ВАЖНО: termios здесь НЕ трогаем. Раньше тут стоял _restore_termios(),
        # оставшийся от старой архитектуры, где постоянного Application не было
        # и терминал надо было спасать руками. Теперь он живой и владеет
        # терминалом: возврат в cooked-режим включает ECHO прямо под ним, и
        # терминал начинает эхать служебные ответы (`^[[34;1R` — ответ на запрос
        # позиции курсора). Отсюда мусор на экране, разъехавшаяся рамка и
        # переставший работать Ctrl+C, потому что ptk теряет raw-режим.
        # Терминал восстанавливаем только на уровне 3, перед самим выходом.
        if ctx:
            ctx.hard_interrupted = True
            ctx.interrupt_level = 2
        self._invalidate_ui()
        self.hard_at = time.monotonic()
        self.silence_stderr()
        task = self.task
        if task is not None and not task.done():
            task.cancel()
        return 2


#: Ход всегда один (очередь строго серийная), поэтому контроллер один на процесс.
_INTERRUPT = InterruptController()


def interrupt_controller() -> InterruptController:
    return _INTERRUPT


def _write_now(text: str) -> None:
    """Синхронная запись в реальный терминал — только для аварийного выхода."""
    try:
        sys.__stdout__.write(text)
        sys.__stdout__.flush()
    except Exception:
        pass


async def _run_with_interrupt(coro, session):
    # termios здесь больше не трогаем: терминалом всю сессию владеет
    # Application, он же держит raw-режим и сам его восстанавливает на выходе.
    # Снимок делает `interactive._run` ДО старта Application (пока режим
    # cooked) — только такой годится для аварийного восстановления на level 3.
    t0 = time.monotonic()
    cancelled = False
    task = asyncio.ensure_future(coro)

    ctl = interrupt_controller()
    ctl.begin(task)

    async def _cancel_watchdog():
        """После жёсткого Ctrl+C (level>=2) ждём отмену задачи; если она зависла
        в неотменяемом коде дольше таймаута — форсим выход всего процесса."""
        timeout = 3.0
        while True:
            await asyncio.sleep(0.2)
            if task.done():
                return
            hard_at = ctl.hard_at
            if ctl.level >= 2 and hard_at is not None and time.monotonic() - hard_at > timeout:
                _restore_termios()
                ctl.restore_stderr()
                _write_now(
                    f"\r\033[K  \033[{ansi_24bit(t('error'))}m■■\033[0m"
                    " \033[2mTask did not cancel in time — force exit.\033[0m\n"
                )
                os._exit(130)

    watchdog = asyncio.ensure_future(_cancel_watchdog())
    try:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            if ctl.level == 0:
                raise
        finally:
            watchdog.cancel()
            ctl.end(task)

        cancelled = ctl.level > 0
        duration = time.monotonic() - t0

        await asyncio.to_thread(storage.save, session)
        return duration, cancelled

    except (BrokenPipeError, ConnectionError, OSError):
        ctl.end(task)
        if not cancelled:
            _notice(f"\n  [{t('error')}]✗ API connection error[/{t('error')}]")
        await asyncio.to_thread(storage.save, session)
        raise
    except Exception as e:
        ctl.end(task)
        logger.exception("agent run failed: %s: %s", type(e).__name__, e)
        if not cancelled:
            _notice(f"\n  [{t('error')}]✗ {escape(str(e))}[/{t('error')}]")
        await asyncio.to_thread(storage.save, session)
        raise


def _resolve_or_exit(name: str) -> str:
    resolved = app_models.resolve_model(name)
    if resolved is None:
        console.print(f"[{t('error')}]Model not found: {escape(name)}[/{t('error')}]")
        for m in app_models.list_models():
            console.print(f"  • {m}")
        sys.exit(1)
    return resolved


_LOGO_LINES = (
    "  ███╗   ██╗███████╗ ██████╗██╗     ██╗",
    "  ████╗  ██║██╔════╝██╔════╝██║     ██║",
    "  ██╔██╗ ██║█████╗  ██║     ██║     ██║",
    "  ██║╚██╗██║██╔══╝  ██║     ██║     ██║",
    "  ██║ ╚████║███████╗╚██████╗███████╗██║",
    "  ╚═╝  ╚═══╝╚══════╝ ╚═════╝╚══════╝╚═╝",
)

_LOGO_GRADIENT = ("#5eead4", "#34d399", "#22c55e", "#16a34a", "#15803d", "#166534")


def _format_relative_time(ts: float) -> str:
    if not ts:
        return ""
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 86400 * 14:
        return f"{int(delta // 86400)}d ago"
    if delta < 86400 * 60:
        return f"{int(delta // (86400 * 7))}w ago"
    return f"{int(delta // (86400 * 30))}mo ago"


def _build_left_content(model: str, session: Session, display_wd: str, n_lsp: int = 0,
                        n_mcp: int = 0, mcp_tools: int = 0, tg_info: str = ""):
    logo = Text()
    for i, line in enumerate(_LOGO_LINES):
        if i:
            logo.append("\n")
        logo.append(line, style=f"bold {_LOGO_GRADIENT[i]}")

    api_id = ""
    try:
        import config as _config
        api_id = _config.get_active_api() or ""
    except Exception:
        logger.debug("welcome get_active_api failed", exc_info=True)

    info = Text()
    info.append(_("welcome.tagline") + "\n", style=f"bold {t('success')}")
    info.append(f"{model}", style=f"bold {t('accent')}")
    if api_id:
        info.append(f"  ·  {api_id}", style="dim")

    meta = Text()
    meta.append("\n\n")
    meta.append("cwd  ", style="dim")
    meta.append(display_wd, style=f"bold {t('success')}")

    if n_lsp > 0:
        meta.append("\n")
        meta.append("lsp  ", style="dim")
        meta.append(_("welcome.lsp_ready", n=n_lsp), style=f"bold {t('success')}")
        meta.append(" (lazy)", style="dim")

    if n_mcp > 0:
        meta.append("\n")
        meta.append("mcp  ", style="dim")
        meta.append(_("welcome.mcp_ready", n=n_mcp, tools=mcp_tools), style=f"bold {t('success')}")

    if tg_info:
        meta.append("\n")
        meta.append("tg   ", style="dim")
        meta.append(tg_info, style=f"bold {t('success')}")

    if session.message_count > 0:
        meta.append("\n")
        meta.append("sess ", style="dim")
        meta.append(session.id[:16], style="bold")
        meta.append(
            f"  ·  {session.message_count} msg  ·  "
            f"↑{format_tokens(session.raw_input_tokens)} "
            f"↓{format_tokens(session.output_tokens)}",
            style="dim",
        )

    return Group(Align.center(logo), Text(""), Align.center(info), meta)


def _build_right_content():
    tips = Text()
    tips.append(_("welcome.tips_title") + "\n", style=f"bold {t('accent')}")
    tips.append(_("welcome.tip_type") + " ")
    tips.append("/help", style=f"bold {t('accent')}")
    tips.append(" " + _("welcome.tip_help") + "\n", style="dim")
    tips.append(_("welcome.tip_use") + " ")
    tips.append("@file", style=f"bold {t('accent')}")
    tips.append(" " + _("welcome.tip_at") + "\n", style="dim")
    tips.append(_("welcome.tip_press") + " ")
    tips.append("Tab", style=f"bold {t('accent')}")
    tips.append(" " + _("welcome.tip_tab") + "\n", style="dim")
    tips.append(_("welcome.tip_press") + " ")
    tips.append("Ctrl+C", style=f"bold {t('accent')}")
    tips.append(" " + _("welcome.tip_ctrl_c"), style="dim")

    recent = Text()
    recent.append("\n\n" + _("welcome.recent") + "\n", style=f"bold {t('accent')}")
    try:
        sessions = storage.list_sessions(limit=4) or []
    except Exception:
        logger.debug("welcome list_sessions failed", exc_info=True)
        sessions = []

    if sessions:
        for s in sessions:
            ts = s.get("updated_at") or s.get("created_at") or 0
            rel = _format_relative_time(ts)
            title = (s.get("title") or s.get("id", ""))[:38]
            recent.append(f"{rel:>7}  ", style="dim")
            recent.append(f"{title}\n")
        recent.append("/sessions", style=f"bold {t('accent')}")
        recent.append(" " + _("welcome.for_more"), style="dim")
    else:
        recent.append(_("welcome.no_sessions_yet"), style="dim")

    return Group(tips, recent)


def _print_welcome(model: str, session: Session, workdir: str = ".", n_lsp: int = 0,
                   n_mcp: int = 0, mcp_tools: int = 0, tg_info: str = ""):
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(session)
    except Exception:
        logger.debug("welcome terminal title update failed", exc_info=True)
    # Сохраним параметры для replay (Ctrl+O в compact) — в модульный кэш
    # потому что ctx ещё может быть не создан в момент первого вызова.
    import agent.render_replay as _rr
    _rr._LAST_WELCOME_ARGS = {
        "model": model, "workdir": workdir, "n_lsp": n_lsp,
        "n_mcp": n_mcp, "mcp_tools": mcp_tools, "tg_info": tg_info,
        "session_id": getattr(session, "id", ""),
    }
    home = os.path.expanduser("~")
    display_wd = workdir
    try:
        abs_wd = os.path.abspath(workdir)
        if abs_wd == home:
            display_wd = "~"
        elif abs_wd.startswith(home + os.sep):
            display_wd = "~" + abs_wd[len(home):]
        else:
            display_wd = abs_wd
    except Exception:
        logger.debug("welcome workdir normalize failed", exc_info=True)

    console.print()
    width = console.size.width
    if width >= 118:
        left_w, right_w = 50, 50
        divider_lines = max(
            len(_LOGO_LINES) + 8,
            10,
        )
        divider = Text(
            "\n".join(["│"] * divider_lines),
            style=f"dim {t('accent')}",
        )
        table = Table.grid(padding=(0, 2), expand=False, pad_edge=False)
        table.add_column(width=left_w, no_wrap=False)
        table.add_column(width=1, no_wrap=True)
        table.add_column(width=right_w, no_wrap=False)
        table.add_row(
            _build_left_content(model, session, display_wd, n_lsp=n_lsp,
                                n_mcp=n_mcp, mcp_tools=mcp_tools, tg_info=tg_info),
            divider,
            _build_right_content(),
        )
        console.print(Panel(
            table,
            title=f"[bold {t('accent')}]necli[/bold {t('accent')}]  [dim]v{_APP_VERSION}[/dim]",
            title_align="left",
            border_style=t("accent"),
            padding=(1, 2),
            width=118,
            expand=False,
        ))
    else:
        console.print(Panel(
            Group(
                _build_left_content(model, session, display_wd, n_lsp=n_lsp,
                                    n_mcp=n_mcp, mcp_tools=mcp_tools, tg_info=tg_info),
                Text(""),
                _build_right_content(),
            ),
            title=f"[bold {t('accent')}]necli[/bold {t('accent')}]  [dim]v{_APP_VERSION}[/dim]",
            title_align="left",
            border_style=t("accent"),
            padding=(1, 2),
        ))
    console.print()


def _print_response_separator():
    """Отбивка после ответа.

    Сама статус-линия теперь живёт в верхней линии рамки и обновляется
    Shell'ом, поэтому здесь остаётся только пустая строка. Если footer уже
    закончил вывод такой отбивкой, вторую не добавляем.
    """
    from ui.shell import ensure_static_blank
    ensure_static_blank()
