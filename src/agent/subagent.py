"""Оркестратор субагентов (API-only) с git-worktree изоляцией."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from agent.subagent_render import SubagentBuffer

logger = logging.getLogger(__name__)


@dataclass
class SubagentTask:
    """Одна задача для субагента."""
    prompt: str
    mode: str = "agent"
    model: str | None = None
    role: str | None = None  # coder | researcher | reviewer | planner | coordinator
    preset: str | None = None  # имя заготовки из .data/agents/<name>/AGENT.md
    depends_on: list[int] = field(default_factory=list)  # 1-based индексы задач
    phase: str | None = None
    label: str | None = None


@dataclass
class SubagentResult:
    task_index: int
    mode: str
    response: str
    iterations: int = 0
    elapsed: float = 0.0
    error: str | None = None
    model_label: str = ""
    phase: str = ""
    label: str = ""
    # git-метаданные (заполняются только для mode=agent с worktree)
    branch: str | None = None
    worktree_path: str | None = None
    commit_sha: str | None = None
    commits_count: int = 0
    files_changed: list[str] = field(default_factory=list)
    diff_stat: str = ""
    has_changes: bool = False


class SubagentOrchestrator:
    """Запускает API-субагентов с изолированными ApiSession + git worktree."""

    def __init__(
        self,
        model: str,
        working_dir: str,
        on_status: Callable[[int, str], None] | None = None,
        buffers: list[SubagentBuffer] | None = None,
        isolate: bool = False,
    ):
        self._model = model
        self._working_dir = working_dir
        self._on_status = on_status or (lambda idx, msg: None)
        self._buffers = buffers or []
        # isolate=False (default): субагенты пишут в общую рабочую директорию.
        # isolate=True: git-worktree изоляция per task.
        self._isolate = isolate
        self.run_dir: str | None = None  # .data/subagents/<run-id>, заполняется в run()

    async def run(self, tasks: list[SubagentTask]) -> list[SubagentResult]:
        if not tasks:
            return []
        if len(tasks) > 100:
            tasks = tasks[:100]

        from agent.subagent_api import run_api_subagents
        from agent.subagent_git import (
            GitError,
            cleanup_stale_branches,
            create_worktree,
            ensure_git_repo,
            gen_run_id,
        )
        from apis.agent_adapter import get_api_session

        api_sess = get_api_session()
        if api_sess is None:
            return [
                SubagentResult(
                    task_index=i,
                    mode=t.mode,
                    response="",
                    error="API session not initialized for subagents.",
                )
                for i, t in enumerate(tasks)
            ]

        # По умолчанию субагенты пишут в ОБЩУЮ рабочую директорию (isolate=False).
        # При isolate=True каждый получает изолированный git-worktree на своей ветке.
        run_id = gen_run_id()
        base_sha = ""
        if self._isolate:
            try:
                base_sha = ensure_git_repo(self._working_dir)
            except GitError as e:
                err_msg = str(e)
                logger.error("subagent_git: ensure_git_repo failed: %s", err_msg)
                return [
                    SubagentResult(
                        task_index=i, mode=t.mode, response="",
                        error=f"Git setup failed: {err_msg}",
                    )
                    for i, t in enumerate(tasks)
                ]
        # Чистим устаревшие ветки прошлых прогонов (старше порога), не трогая
        # ветки текущего run_id. Не критично если упадёт.
        if self._isolate:
            try:
                cleanup_stale_branches(self._working_dir, current_run_id=run_id)
            except Exception as e:
                logger.warning("cleanup_stale_branches failed (non-fatal): %s", e)
        # Shared scratchpad: общий файл для всех субагентов прогона.
        # Лежит в .data/subagents/<run-id>/shared.md, на уровень выше worktree'ов.
        import os
        scratch_dir = os.path.join(
            self._working_dir, ".data", "subagents", run_id,
        )
        self.run_dir = scratch_dir
        try:
            os.makedirs(scratch_dir, exist_ok=True)
            scratch_path = os.path.join(scratch_dir, "shared.md")
            if not os.path.exists(scratch_path):  # noqa: ASYNC240
                with open(scratch_path, "w", encoding="utf-8") as fh:  # noqa: ASYNC230
                    fh.write(
                        "# Shared scratchpad\n\n"
                        "Subagents of this run append contracts/interfaces/decisions here.\n\n"
                    )
        except Exception as e:
            logger.warning("subagent: shared scratchpad init failed: %s", e)
        if not self._isolate:
            handles = [None] * len(tasks)
        else:
            handles = []
            try:
                for i, _ in enumerate(tasks):
                    self._on_status(i, "Preparing worktree...")
                    handles.append(create_worktree(
                        self._working_dir, i, run_id, base_sha,
                    ))
            except GitError as e:
                err_msg = str(e)
                logger.error("subagent_git: create_worktree failed: %s", err_msg)
                # сносим то, что успели создать
                from agent.subagent_git import cleanup_worktree
                for h in handles:
                    cleanup_worktree(self._working_dir, h)
                return [
                    SubagentResult(
                        task_index=i, mode=t.mode, response="",
                        error=f"Worktree setup failed: {err_msg}",
                    )
                    for i, t in enumerate(tasks)
                ]

        proof = await self._gather_proof()
        for i, _ in enumerate(tasks):
            self._on_status(i, "Initializing API...")

        return await run_api_subagents(
            tasks=tasks,
            proof=proof,
            default_provider_id=api_sess.provider_id,
            default_model_id=api_sess.model_id,
            buffers=self._buffers,
            status_cb=self._on_status,
            handles=handles,
            project_root=self._working_dir,
            run_dir=scratch_dir,
            isolate=self._isolate,
        )

    async def _gather_proof(self) -> str:
        import os
        from datetime import datetime
        pwd_val = os.path.abspath(self._working_dir)  # noqa: ASYNC240
        date_val = datetime.now().strftime("%a %b %d %H:%M:%S %Y")
        return (
            f"Working directory: {pwd_val}\n"
            f"Today's date: {date_val}"
        )


def format_subagent_results(
    results: list[SubagentResult], run_dir: str | None = None,
) -> str:
    """Форматирует результаты для главного агента. Включает git-метаданные."""
    parts = []
    if run_dir:
        import os
        parts.append(
            "(incremental progress log of this run: "
            f"{os.path.join(run_dir, 'progress.md')} — each subagent was "
            "appended the moment it finished)"
        )
    for r in results:
        meta = f" [{r.phase}]" if r.phase else ""
        label = f" {r.label}" if r.label else ""
        header = f"=== Subagent {r.task_index + 1}{meta}{label} [{r.mode}] ==="
        if r.error:
            body = [f"{header}\nERROR: {r.error}"]
            if r.branch:
                body.extend(["", "--- Git ---", f"branch: {r.branch}"])
                if r.has_changes:
                    body.append("uncommitted changes kept for inspection")
                    if r.worktree_path:
                        body.append(f"worktree: {r.worktree_path}")
                    if r.files_changed:
                        body.append(f"files ({len(r.files_changed)}):")
                        for f in r.files_changed[:30]:
                            body.append(f"  {f}")  # noqa: PERF401
                    if r.diff_stat:
                        body.append("")
                        body.append(r.diff_stat)
                else:
                    body.append("no changes")
            parts.append("\n".join(body))
            continue

        stats = f"({r.iterations} iterations, {r.elapsed:.1f}s)"
        body = [f"{header} {stats}", r.response]

        if r.branch:
            git_lines = ["", "--- Git ---", f"branch: {r.branch}"]
            if r.has_changes:
                git_lines.append(f"commits: {r.commits_count}")
                if r.files_changed:
                    git_lines.append(f"files ({len(r.files_changed)}):")
                    for f in r.files_changed[:30]:
                        git_lines.append(f"  {f}")  # noqa: PERF401
                    if len(r.files_changed) > 30:
                        git_lines.append(f"  ... +{len(r.files_changed) - 30} more")
                if r.diff_stat:
                    git_lines.append("")
                    git_lines.append(r.diff_stat)
                git_lines.append("")
                base_short = r.commit_sha[:12] if r.commit_sha else r.branch
                git_lines.append(
                    f"To merge: git merge --no-ff {r.branch}    "
                    f"(or: git cherry-pick {base_short})"
                )
                # diff именно от base_sha, а не от ветки 'main' — у юзера
                # может быть другая активная ветка (rework/feature/...).
                if r.commit_sha:
                    git_lines.append(
                        f"To inspect: git show {base_short}    "
                        f"git log -p {r.branch} -1"
                    )
                else:
                    git_lines.append(f"To inspect: git log {r.branch}")
                git_lines.append(
                    f"To discard: git branch -D {r.branch}"
                )
            else:
                git_lines.append("no changes (nothing to merge)")
            body.extend(git_lines)
        parts.append("\n".join(body))
    return "\n\n".join(parts)
