"""Фоновый запуск отчёта /insights."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.markup import escape

from config.i18n import t as tr
from config.themes import t
from logger import logger
from tools._paths import get_working_dir
from ui.shell import ensure_static_blank, print_static

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _print_result(message: str, *, error: bool = False) -> None:
    role = "error" if error else "success"
    ensure_static_blank()
    print_static(f"[{t(role)}]{escape(message)}[/{t(role)}]")
    ensure_static_blank()


async def _generate(working_dir: str) -> None:
    from memory.insights import generate_insights

    try:
        result = await generate_insights(working_dir, persist_memory=False)
    except asyncio.CancelledError:
        raise
    except RuntimeError as exc:
        if "no sessions" in str(exc):
            _print_result(tr("insights.no_sessions"), error=True)
            return
        logger.error("insights failed: {}", exc)
        _print_result(tr("insights.failed", err=str(exc)), error=True)
        return
    except Exception as exc:
        logger.opt(exception=True).error("insights failed: {}", exc)
        _print_result(tr("insights.failed", err=str(exc)), error=True)
        return

    report_path = Path(result["report_path"])
    _print_result(tr("insights.done", path=str(report_path)))


async def insights_interactive() -> None:
    """Запустить анализ на текущем loop и сразу вернуть управление терминалу."""
    working_dir = get_working_dir()
    task = asyncio.create_task(_generate(working_dir), name="insights")
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    print_static(f"[dim]{escape(tr('insights.working'))}[/dim]")


async def stop_background_insights_tasks() -> None:
    """Отменить незавершённые отчёты при закрытии интерактивной сессии."""
    tasks = set(_BACKGROUND_TASKS)
    _BACKGROUND_TASKS.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
