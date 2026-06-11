"""Режим /duo — две модели параллельно работают над одной задачей.

Каждая модель — полноценный изолированный агент (_ApiSubagentRunner) в своём
git-worktree: реально читает/пишет файлы и выполняет команды, не мешая другой.
Прогресс обеих рисуется бок о бок (DuoTracker, split-screen Live). После
завершения обе модели видят решение друг друга и вырабатывают совместную
итоговую рекомендацию (фаза обсуждения).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from typing import Optional

from rich.columns import Columns
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from config.themes import t
from agent.duo_render import DuoTracker
from agent.subagent_render import SubagentBuffer
from agent.subagent_api import _ApiSubagentRunner
from agent.subagent_git import (
    GitError,
    cleanup_worktree,
    commit_worktree,
    create_worktree,
    ensure_git_repo,
    gen_run_id,
    summarize_changes,
)
from apis.registry import get_provider
from apis.messages import HumanMessage, SystemMessage
from apis._retry import with_throttle_retry
from apis.agent_adapter import _content_to_text

logger = logging.getLogger(__name__)
console = Console()


def _proof(working_dir: str) -> str:
    return (
        f"Working directory: {os.path.abspath(working_dir)}\n"
        f"Today's date: {datetime.now().strftime('%a %b %d %H:%M:%S %Y')}"
    )


async def run_duo(working_dir: str, task: str, models: list[tuple[str, str, str]]) -> None:
    """models: ровно 2 кортежа (provider_id, model_id, display_name)."""
    if len(models) != 2:
        console.print("  [red]/duo requires exactly 2 models[/red]")
        return

    try:
        base_sha = ensure_git_repo(working_dir)
    except GitError as e:
        console.print(f"  [red]/duo needs git for isolation: {escape(str(e))}[/red]")
        return

    run_id = gen_run_id()
    try:
        handles = [create_worktree(working_dir, i, run_id, base_sha) for i in range(2)]
    except GitError as e:
        console.print(f"  [red]Worktree setup failed: {escape(str(e))}[/red]")
        return

    proof = _proof(working_dir)
    buffers = [
        SubagentBuffer(index=i, mode="agent", prompt=task, model_label=models[i][2])
        for i in range(2)
    ]
    runners = [
        _ApiSubagentRunner(
            index=i,
            prompt=task,
            mode="agent",
            provider_id=models[i][0],
            model_id=models[i][1],
            proof=proof,
            buffer=buffers[i],
            status_cb=lambda idx, msg: None,
            handle=handles[i],
        )
        for i in range(2)
    ]

    logger.info(
        "duo start: run_id=%s task=%r models=%s",
        run_id, task[:80], [m[1] for m in models],
    )

    tracker = DuoTracker(buffers)
    tracker.start()
    try:
        results = await asyncio.gather(
            runners[0].run(), runners[1].run(), return_exceptions=True,
        )
    finally:
        tracker.stop()

    finals: list[tuple[str, int, Optional[str]]] = []
    for i in range(2):
        r = results[i]
        if isinstance(r, Exception):
            logger.error("duo runner %d crashed: %s", i, r, exc_info=r)
            finals.append(("", 0, f"{type(r).__name__}: {r}"))
        else:
            finals.append(r)
        try:
            commit_worktree(handles[i], f"duo[{models[i][2]}]: {task[:60]}")
            summarize_changes(handles[i], working_dir)
        except Exception:
            logger.error("duo finalize git sub=%d failed", i, exc_info=True)
        try:
            cleanup_worktree(working_dir, handles[i])
        except Exception:
            logger.warning("duo cleanup_worktree sub=%d failed", i, exc_info=True)

    _print_results(models, finals, handles)
    await _discussion(working_dir, task, models, finals)


def _print_results(models, finals, handles) -> None:
    panels = []
    for i in range(2):
        text, iters, err = finals[i]
        h = handles[i]
        if err:
            body = Text(err, style="red")
            border = "red"
        else:
            body = Text(text.strip() or "(no answer)")
            border = t("success")
        title = Text()
        title.append("\U0001f916 ", style=f"bold {t('magenta')}")
        title.append(models[i][2], style=f"bold {t('magenta')}")
        sub_parts = [f"{iters} iter"]
        if h.has_changes:
            sub_parts.append(f"{h.commits_count} commit(s) \u00b7 {len(h.files_changed)} files")
            sub_parts.append(f"branch {h.branch}")
        else:
            sub_parts.append("no changes")
        panels.append(Panel(
            body, title=title, subtitle=" \u00b7 ".join(sub_parts),
            title_align="left", subtitle_align="right",
            border_style=border, padding=(0, 1),
        ))
    console.print()
    console.print(Columns(panels, equal=True, expand=True))

    # Подсказки по merge для веток с изменениями.
    hints: list[str] = []
    for i in range(2):
        h = handles[i]
        if h.has_changes and h.commit_sha:
            hints.append(
                f"  [dim]{models[i][2]}: git merge --no-ff {h.branch}"
                f"   ·   git show {h.commit_sha[:12]}[/dim]"
            )
    if hints:
        console.print()
        for ln in hints:
            console.print(ln)


async def _ask(provider_id: str, model_id: str, system: str, user: str) -> str:
    llm = get_provider(provider_id, model_id)
    try:
        if hasattr(llm, "streaming"):
            llm.streaming = False
    except Exception:
        logger.debug("duo _ask set streaming failed", exc_info=True)
    result = await with_throttle_retry(
        lambda: llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    )
    return _content_to_text(getattr(result, "content", result)).strip()


async def _discussion(working_dir, task, models, finals) -> None:
    ans = [finals[0][0].strip(), finals[1][0].strip()]
    if not ans[0] and not ans[1]:
        return

    console.print()
    console.print(f"  [bold {t('magenta')}]\U0001f91d Discussion[/bold {t('magenta')}]")

    review_system = (
        "You are an AI engineer collaborating with another AI on the SAME task. "
        "You are shown both solutions. Be honest and specific: where is the other "
        "solution better than yours, where is yours better, and what is the single "
        "best combined approach. 4-6 sentences, no preamble."
    )

    async def _review(i: int) -> str:
        mine, theirs = ans[i], ans[1 - i]
        user = (
            f"TASK:\n{task}\n\n"
            f"YOUR SOLUTION ({models[i][2]}):\n{mine or '(empty)'}\n\n"
            f"OTHER MODEL'S SOLUTION ({models[1 - i][2]}):\n{theirs or '(empty)'}\n\n"
            "Compare them and state the best combined approach."
        )
        try:
            return await _ask(models[i][0], models[i][1], review_system, user)
        except Exception as e:
            logger.error("duo review %d failed: %s", i, e, exc_info=True)
            return f"(review failed: {type(e).__name__})"

    reviews = await asyncio.gather(_review(0), _review(1))

    for i in range(2):
        console.print()
        console.print(f"  [bold]{escape(models[i][2])}[/bold]")
        console.print(f"  [dim]{escape(reviews[i])}[/dim]")

    # Финальный синтез — нейтральное итоговое решение от первой модели.
    synth_system = (
        "You are a neutral technical lead. Given a task, two independent AI "
        "solutions and each author's review of both, decide the FINAL approach: "
        "which solution to take as the base, what to merge from the other, and "
        "why. Be decisive and concrete. End with a one-line verdict."
    )
    synth_user = (
        f"TASK:\n{task}\n\n"
        f"SOLUTION A ({models[0][2]}):\n{ans[0] or '(empty)'}\n\n"
        f"SOLUTION B ({models[1][2]}):\n{ans[1] or '(empty)'}\n\n"
        f"REVIEW BY A:\n{reviews[0]}\n\n"
        f"REVIEW BY B:\n{reviews[1]}\n\n"
        "Now give the final decision."
    )
    try:
        verdict = await _ask(models[0][0], models[0][1], synth_system, synth_user)
    except Exception as e:
        logger.error("duo synthesis failed: %s", e, exc_info=True)
        verdict = ""

    if verdict:
        console.print()
        console.print(Panel(
            Text(verdict),
            title=Text("\u2696 Final decision", style=f"bold {t('accent')}"),
            title_align="left",
            border_style=t("accent"),
            padding=(0, 1),
        ))