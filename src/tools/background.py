"""Фоновое выполнение shell-команд.

Тяжёлую команду можно запустить в фоне (`background=True` у shell): она
исполняется в потоке-демоне, агент сразу получает job-id и продолжает
работу. Завершённые задачи доставляются модели как уведомления через
`drain_finished_results()` — основной цикл подмешивает их к результатам
ближайшего раунда.
"""

import asyncio
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field

from logger import logger
from tools.models import ToolResult

# Фоновые задачи — для долгих процессов, поэтому таймаут заметно больше
# обычного shell (_EXECUTION_TIMEOUT). Час с запасом.
_BG_TIMEOUT = 3600

# ── Мост поток-демон → asyncio ──
# Фоновые задачи исполняются в daemon-потоках (вне asyncio). Чтобы REPL мог
# мгновенно проснуться при завершении задачи (а не ждать ввода пользователя),
# поток сигналит сюда через loop.call_soon_threadsafe. Ввод/цикл ждут на
# _finish_event и при срабатывании дренируют результаты.
_event_loop: "asyncio.AbstractEventLoop | None" = None
_finish_event: "asyncio.Event | None" = None


def register_event_loop(loop: "asyncio.AbstractEventLoop") -> None:
    """Привязывает asyncio-loop, в котором живёт REPL. Создаёт Event в нём."""
    global _event_loop, _finish_event
    _event_loop = loop
    _finish_event = asyncio.Event()


def get_finish_event() -> "asyncio.Event | None":
    """Event, взводимый при завершении любой фоновой задачи (или None если не привязан)."""
    return _finish_event


def clear_finish_event() -> None:
    """Сбрасывает Event после обработки — чтобы ждать следующего завершения."""
    if _finish_event is not None:
        _finish_event.clear()


def _signal_finish() -> None:
    """Будит asyncio-loop из фонового потока (thread-safe)."""
    loop = _event_loop
    ev = _finish_event
    if loop is None or ev is None:
        return
    try:
        loop.call_soon_threadsafe(ev.set)
    except Exception:  # noqa: BLE001 — loop закрыт/недоступен: не роняем поток
        logger.debug("background: signal_finish failed", exc_info=True)


@dataclass
class _Job:
    id: str
    command: str
    status: str = "running"  # running | done | error | timeout
    output: str = ""
    exit_code: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    delivered: bool = False


_jobs: dict[str, _Job] = {}
_lock = threading.Lock()
_counter = 0


def has_pending_finished() -> bool:
    """True, если есть завершённые, но ещё не доставленные модели задачи."""
    with _lock:
        return any(
            j.status != "running" and not j.delivered for j in _jobs.values()
        )


def _run_job(job: _Job, cwd: str, env: dict) -> None:
    run_kwargs = dict(
        capture_output=True, text=True,
        timeout=_BG_TIMEOUT, cwd=cwd, env=env,
    )
    if sys.platform != "win32":
        run_kwargs["executable"] = "/bin/bash"
    try:
        result = subprocess.run(job.command, shell=True, **run_kwargs)
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        output = "\n".join(parts) if parts else "(no output)"
        with _lock:
            job.output = output
            job.exit_code = result.returncode
            job.status = "done" if result.returncode == 0 else "error"
            job.finished_at = time.monotonic()
        logger.info(
            "background job {} done: exit={} out_len={}",
            job.id, result.returncode, len(output),
        )
    except subprocess.TimeoutExpired:
        with _lock:
            job.output = f"Timeout: {_BG_TIMEOUT}s"
            job.exit_code = -1
            job.status = "timeout"
            job.finished_at = time.monotonic()
        logger.warning("background job {} timeout {}s", job.id, _BG_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — фоновый поток не должен ронять CLI
        with _lock:
            job.output = f"Error: {e}"
            job.exit_code = -1
            job.status = "error"
            job.finished_at = time.monotonic()
        logger.opt(exception=True).error("background job {} crashed: {}", job.id, e)
    finally:
        # Будим REPL/цикл: задача завершилась (в любом исходе) — есть что
        # доставить модели. Сигнал thread-safe и безопасен при отсутствии loop.
        _signal_finish()


def start_background(command: str, cwd: str, env: dict) -> str:
    """Запускает команду в фоновом потоке, возвращает job-id."""
    global _counter
    with _lock:
        _counter += 1
        job_id = f"bg-{_counter}"
        job = _Job(id=job_id, command=command)
        _jobs[job_id] = job
    thread = threading.Thread(
        target=_run_job, args=(job, cwd, dict(env)), daemon=True,
        name=f"necli-bg-{job_id}",
    )
    thread.start()
    logger.info("background job {} started: {!r} (cwd={})", job_id, command[:300], cwd)
    return job_id


def drain_finished_results() -> list[ToolResult]:
    """Возвращает ToolResult-уведомления по завершённым, ещё не доставленным задачам."""
    out: list[ToolResult] = []
    with _lock:
        for job in _jobs.values():
            if job.status == "running" or job.delivered:
                continue
            job.delivered = True
            elapsed = max(0.0, job.finished_at - job.started_at)
            header = (
                f"[background {job.id} finished — exit {job.exit_code}, "
                f"{elapsed:.0f}s]\n$ {job.command}\n"
            )
            out.append(ToolResult(
                name="shell",
                status="ok" if job.status == "done" else "error",
                output=header + job.output,
                exit_code=job.exit_code,
                command=job.command,
            ))
    if out:
        logger.info("background drain: delivering {} finished job(s)", len(out))
    return out