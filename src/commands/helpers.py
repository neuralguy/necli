import asyncio
import logging
import os
import signal
import sys
import time

from rich.align import Align
from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

import models as app_models
import session.storage as storage
from config.i18n import t as _
from config.themes import t
from session import Session
from ui import format_cost, format_tokens

logger = logging.getLogger(__name__)
console = Console()


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


def _make_interrupt_handler(task_ref, stderr_ref):
    state: dict[str, float] = {"level": 0}

    def handler(sig, frame):
        from agent import get_current_ctx
        ctx = get_current_ctx()
        state["level"] += 1
        level = state["level"]

        if level == 1:
            # Мягкое прерывание: цикл доделает текущую итерацию и остановится.
            if ctx:
                ctx.interrupted = True
            stderr_ref.write(
                "\r\033[K  \033[33m■\033[0m"
                " \033[2mStopping after current step… (Ctrl+C again = hard stop)\033[0m\n"
            )
            stderr_ref.flush()
            return

        # level >= 3: cancel завис (неотменяемый синхронный код / C-расширение) —
        # аварийный выход всего процесса. Лучше так, чем висеть бесконечно.
        if level >= 3:
            stderr_ref.write(
                "\r\033[K  \033[31m■■■\033[0m"
                " \033[2mForce exit.\033[0m\n"
            )
            stderr_ref.flush()
            os._exit(130)

        # level == 2: жёсткая отмена задачи прямо сейчас.
        if ctx:
            ctx.hard_interrupted = True
        stderr_ref.write(
            "\r\033[K  \033[31m■■\033[0m"
            " \033[2mEmergency stop…\033[0m\n"
        )
        stderr_ref.flush()
        try:
            import sys as _sys
            _sys.stderr = open(os.devnull, "w")  # noqa: SIM115
        except Exception:
            logger.debug("stderr redirect to devnull failed", exc_info=True)
        t_ = task_ref.get("task")
        if t_ and not t_.done():
            t_.cancel()

    def stop_animation():
        pass

    return handler, state, stop_animation


async def _run_with_interrupt(coro, session, on_cancelled=None):
    t0 = time.monotonic()
    cancelled = False
    task = asyncio.ensure_future(coro)

    _task_ref = {"task": task}
    _saved_stderr = sys.stderr
    _int_handler, _int_state, _stop_anim = _make_interrupt_handler(_task_ref, _saved_stderr)

    original_handler = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, _int_handler)

    def _restore_stderr():
        if sys.stderr is not _saved_stderr:
            try:
                sys.stderr.close()
            except Exception:
                logger.debug("stderr devnull close failed", exc_info=True)
            sys.stderr = _saved_stderr

    async def _cancel_watchdog():
        """После жёсткого Ctrl+C (level>=2) ждём отмену задачи; если она зависла
        в неотменяемом коде дольше таймаута — форсим выход всего процесса."""
        timeout = 3.0
        while True:
            await asyncio.sleep(0.2)
            if task.done():
                return
            if _int_state["level"] >= 2:
                # Засекаем дедлайн с момента первого жёсткого прерывания.
                deadline = _int_state.get("_hard_at")
                now = time.monotonic()
                if deadline is None:
                    _int_state["_hard_at"] = now
                elif now - deadline > timeout:
                    try:
                        _restore_stderr()
                        sys.stderr.write(
                            "\r\033[K  \033[31m■■\033[0m"
                            " \033[2mTask did not cancel in time — force exit.\033[0m\n"
                        )
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os._exit(130)

    watchdog = asyncio.ensure_future(_cancel_watchdog())
    try:
        try:
            await task
        except (asyncio.CancelledError, Exception):
            if _int_state["level"] == 0:
                raise
        finally:
            watchdog.cancel()
            signal.signal(signal.SIGINT, original_handler)
            _stop_anim()
            _restore_stderr()

        cancelled = _int_state["level"] > 0
        duration = time.monotonic() - t0

        await asyncio.to_thread(storage.save, session)
        return duration, cancelled

    except (BrokenPipeError, ConnectionError, OSError):
        _restore_stderr()
        if not cancelled and on_cancelled is None:
            console.print("\n  [red]✗ API connection error[/red]")
        await asyncio.to_thread(storage.save, session)
        raise
    except Exception as e:
        _restore_stderr()
        logger.exception("agent run failed: %s: %s", type(e).__name__, e)
        if not cancelled:
            console.print(f"\n  [red]✗ {escape(str(e))}[/red]")
        await asyncio.to_thread(storage.save, session)
        raise


def _resolve_or_exit(name: str) -> str:
    resolved = app_models.resolve_model(name)
    if resolved is None:
        console.print(f"[red]Model not found: {escape(name)}[/red]")
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


def _print_session_switch(session: Session, context_count: int = 0):
    try:
        from ui.terminal_title import set_session_terminal_title
        set_session_terminal_title(session)
    except Exception:
        logger.debug("session switch terminal title update failed", exc_info=True)
    console.print()
    console.print(Rule(style="dim"))
    title = f" — {session.title}" if session.title else ""
    console.print(
        f"  [yellow]↻[/yellow] Session [bold]{session.id[:20]}[/bold]{escape(title)}"
    )
    if session.message_count > 0:
        models = session.models_used
        console.print(
            f"  [dim]{session.message_count}msg · "
            f"↑{format_tokens(session.raw_input_tokens)} ↓{format_tokens(session.output_tokens)} · "
            f"≈{format_cost(session.total_cost)} · "
            f"{escape(', '.join(models[:3]))}[/dim]"
        )
    if context_count > 0:
        console.print(f"  [dim]{context_count} messages in context[/dim]")
    console.print(Rule(style="dim"))
    console.print()


def _print_user_message(text: str, model: str):
    pass


def _print_response_separator():
    console.print()
