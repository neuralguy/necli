"""Фоновое выполнение shell-команд.

Тяжёлую команду можно запустить в фоне (`background=True` у shell): она
исполняется в потоке-демоне, агент сразу получает job-id и продолжает
работу. Завершённые задачи доставляются модели как уведомления через
`drain_finished_results()` — основной цикл подмешивает их к результатам
ближайшего раунда.
"""

import asyncio
import codecs
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from io import StringIO

from config.constants import Limits
from config.i18n import format_duration
from logger import logger
from tools.models import ToolResult


class _OutputBuffer:
    def __init__(self, limit: int) -> None:
        self._limit = max(2, limit)
        self._head_limit = self._limit // 2
        self._tail_limit = self._limit - self._head_limit
        self._head = StringIO()
        self._tail: deque[str] = deque()
        self._tail_chars = 0
        self.total_chars = 0

    def write(self, text: str) -> None:
        if not text:
            return
        self.total_chars += len(text)
        head_remaining = self._head_limit - self._head.tell()
        if head_remaining > 0:
            self._head.write(text[:head_remaining])
            text = text[head_remaining:]
        if not text:
            return
        self._tail.append(text)
        self._tail_chars += len(text)
        while self._tail_chars > self._tail_limit and self._tail:
            excess = self._tail_chars - self._tail_limit
            first = self._tail[0]
            if len(first) <= excess:
                self._tail.popleft()
                self._tail_chars -= len(first)
            else:
                self._tail[0] = first[excess:]
                self._tail_chars -= excess

    def snapshot(self, max_chars: int | None = None) -> str:
        head = self._head.getvalue()
        tail = "".join(self._tail)
        if self.total_chars <= self._limit:
            text = head + tail
        else:
            skipped = self.total_chars - len(head) - len(tail)
            text = head + f"\n... [{skipped} chars skipped] ...\n" + tail
        if max_chars is None or len(text) <= max_chars:
            return text
        skipped = len(text) - max_chars
        return f"... [{skipped} earlier chars hidden in live view] ...\n" + text[-max_chars:]

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


def clear_finish_event(*, force: bool = False) -> None:
    """Сбрасывает Event после обработки, не теряя гонку с новым результатом.

    Публикация результата и эта проверка используют один lock. Поэтому новый
    результат, опубликованный после проверки, гарантированно взведёт Event
    снова; ``force`` нужен только когда результат намеренно оставлен ждать
    следующего пользовательского хода.
    """
    if _finish_event is None:
        return
    with _lock:
        if force or not (
            _external_results
            or any(j.status != "running" and not j.delivered for j in _jobs.values())
        ):
            _finish_event.clear()


def _signal_finish() -> None:
    """Будит asyncio-loop из фонового потока (thread-safe)."""
    loop = _event_loop
    ev = _finish_event
    if loop is None or ev is None:
        return
    try:
        loop.call_soon_threadsafe(ev.set)
    except Exception:
        logger.debug("background: signal_finish failed", exc_info=True)


@dataclass
class _Job:
    id: str
    command: str
    status: str = "running"  # running | done | error | timeout | cancelled
    output: str = ""
    exit_code: int = 0
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    delivered: bool = False
    revision: int = 0
    process: subprocess.Popen | None = field(default=None, repr=False)
    cancel_requested: bool = False
    deliver_result: bool = True
    visible: bool = True
    timeout: float = Limits.BG_SHELL_TIMEOUT
    process_ready: threading.Event = field(default_factory=threading.Event, repr=False)
    output_buffer: _OutputBuffer = field(
        default_factory=lambda: _OutputBuffer(Limits.BG_SHELL_MAX_OUTPUT_CHARS),
        repr=False,
    )


_jobs: dict[str, _Job] = {}
_external_results: list[ToolResult] = []
_external_running = 0
_lock = threading.Lock()
_counter = 0


def has_pending_finished() -> bool:
    """True, если есть завершённые, но ещё не доставленные модели задачи."""
    with _lock:
        return bool(_external_results) or any(
            j.status != "running" and not j.delivered for j in _jobs.values()
        )


def has_running_work() -> bool:
    """Есть ли работа либо готовый результат, ещё не доставленный агенту."""
    with _lock:
        return (
            _external_running > 0
            or bool(_external_results)
            or any(j.status == "running" or not j.delivered for j in _jobs.values())
        )


def _notify_job_changed(job: _Job) -> None:
    try:
        from agent.background_render import update_background_job

        update_background_job(job)
    except Exception:
        logger.debug("background UI update failed", exc_info=True)


def _append_job_output(job: _Job, text: str, *, stderr: bool = False) -> None:
    if not text:
        return
    chunk = f"[stderr] {text}" if stderr else text
    with _lock:
        job.output_buffer.write(chunk)
        job.revision += 1


def snapshot_job_output(job: _Job, max_chars: int | None = None) -> str:
    with _lock:
        output_buffer = getattr(job, "output_buffer", None)
        buffered = (
            output_buffer.snapshot(max_chars=max_chars)
            if output_buffer is not None
            else ""
        )
        output = getattr(job, "output", "") or ""
        if not output:
            return buffered
        combined = output + buffered
        if max_chars is None or len(combined) <= max_chars:
            return combined
        skipped = len(combined) - max_chars
        return (
            f"... [{skipped} earlier chars hidden in live view] ...\n"
            + combined[-max_chars:]
        )


def _finalize_job_output(job: _Job) -> None:
    job.output += job.output_buffer.snapshot()
    job.output_buffer = _OutputBuffer(Limits.BG_SHELL_MAX_OUTPUT_CHARS)


def _stop_process(process: subprocess.Popen, *, force: bool = False) -> None:
    """Остановить shell вместе со всеми запущенными им дочерними процессами."""
    try:
        if sys.platform != "win32":
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
        elif force:
            process.kill()
        else:
            process.terminate()
    except (ProcessLookupError, OSError):
        # Процесс мог завершиться между poll() и сигналом — это нормальная гонка.
        pass


def _run_job(job: _Job, cwd: str, env: dict) -> None:
    child_env = dict(env)
    # stdout/stderr фоновой команды подключены к pipe, а не к TTY. Python в
    # таком режиме буферизует print блоками, поэтому долгоживущий test.py мог
    # выглядеть как задача без вывода. -u через окружение работает и для
    # `python script.py`, и для модулей/обёрток, не переписывая команду.
    child_env["PYTHONUNBUFFERED"] = "1"
    run_kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": False,
        "bufsize": 0,
        "cwd": cwd,
        "env": child_env,
    }
    if sys.platform != "win32":
        run_kwargs["executable"] = "/bin/bash"
        # Отдельная группа позволяет по таймауту остановить не только shell,
        # но и его дочерние процессы (иначе `sleep` продолжал держать pipe).
        run_kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(job.command, shell=True, **run_kwargs)
        with _lock:
            job.process = process

        readers = []
        for pipe, is_stderr in ((process.stdout, False), (process.stderr, True)):
            if pipe is None:
                continue

            def _read(stream=pipe, err=is_stderr, job_ref=job):
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                try:
                    while chunk := os.read(stream.fileno(), 64 * 1024):
                        text = decoder.decode(chunk)
                        if text:
                            _append_job_output(job_ref, text, stderr=err)
                    tail = decoder.decode(b"", final=True)
                    if tail:
                        _append_job_output(job_ref, tail, stderr=err)
                except (OSError, ValueError):
                    # Pipe закрыт извне (process killed, timeout) — не даём
                    # потоку упасть и не оставляем дескриптор открытым.
                    pass
                finally:
                    try:
                        stream.close()
                    except OSError:
                        pass  # уже закрыт

            reader = threading.Thread(
                target=_read,
                daemon=True,
                name=f"necli-bg-reader-{job.id}",
            )
            reader.start()
            readers.append(reader)

        job.process_ready.set()

        deadline = time.monotonic() + job.timeout
        timed_out = False
        cancel_sent_at: float | None = None
        while process.poll() is None:
            with _lock:
                cancel_requested = job.cancel_requested
            if cancel_requested:
                if cancel_sent_at is None:
                    cancel_sent_at = time.monotonic()
                    _stop_process(process)
                elif time.monotonic() - cancel_sent_at >= 2:
                    _stop_process(process, force=True)
            if time.monotonic() >= deadline:
                timed_out = True
                _stop_process(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _stop_process(process, force=True)
                break
            time.sleep(0.05)
        exit_code = process.wait()
        for reader in readers:
            reader.join(timeout=1)
        with _lock:
            job.process = None
            _finalize_job_output(job)
            if not job.output:
                job.output = "(no output)"
            cancelled = job.cancel_requested
            job.exit_code = 130 if cancelled else -1 if timed_out else exit_code
            job.status = (
                "cancelled"
                if cancelled
                else "timeout"
                if timed_out
                else "done"
                if exit_code == 0
                else "error"
            )
            job.finished_at = time.monotonic()
            job.revision += 1
        logger.info(
            "background job {} done: exit={} out_len={}",
            job.id,
            job.exit_code,
            len(job.output),
        )
    except Exception as e:
        job.process_ready.set()
        with _lock:
            job.process = None
            _finalize_job_output(job)
            error = f"Error: {e}"
            job.output = f"{job.output}\n{error}" if job.output else error
            job.exit_code = 130 if job.cancel_requested else -1
            job.status = "cancelled" if job.cancel_requested else "error"
            job.finished_at = time.monotonic()
            job.revision += 1
        logger.opt(exception=True).error("background job {} crashed: {}", job.id, e)
    finally:
        _notify_job_changed(job)
        # Будим REPL/цикл: задача завершилась (в любом исходе) — есть что
        # доставить модели. Сигнал thread-safe и безопасен при отсутствии loop.
        if job.deliver_result:
            _signal_finish()


def start_background(
    command: str,
    cwd: str,
    env: dict,
    *,
    visible: bool = True,
    deliver_result: bool = True,
    timeout: float = Limits.BG_SHELL_TIMEOUT,
) -> str:
    """Запускает команду в фоновом потоке, возвращает job-id."""
    global _counter
    with _lock:
        _counter += 1
        job_id = f"bg-{_counter}"
        job = _Job(
            id=job_id,
            command=command,
            delivered=not deliver_result,
            deliver_result=deliver_result,
            visible=visible,
            timeout=timeout,
        )
        _jobs[job_id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(job, cwd, dict(env)),
        daemon=True,
        name=f"necli-bg-{job_id}",
    )
    # Процесс стартует ДО ленивого импорта UI: первая фоновая команда не
    # должна ждать загрузки Rich/оверлеев, прежде чем реально запуститься.
    thread.start()
    # Ждём создания процесса в обоих случаях: cancel_background должен
    # находить job.process сразу после возврата job-id.
    job.process_ready.wait(timeout=1)
    if visible:
        try:
            from agent.background_render import attach_background_job

            attach_background_job(job)
        except Exception:
            logger.debug("background UI attach failed", exc_info=True)
    logger.info("background job {} started: {!r} (cwd={})", job_id, command[:300], cwd)
    return job_id


def cancel_background(job_id: str) -> bool:
    """Запросить остановку фоновой задачи. Возвращает False, если она уже завершена."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None or job.status != "running":
            return False
        job.cancel_requested = True
        job.revision += 1
        process = job.process
    if process is not None:
        _stop_process(process)
    _notify_job_changed(job)
    return True


def register_external_work() -> None:
    """Зарегистрировать фоновую работу вне реестра shell-процессов."""
    global _external_running
    with _lock:
        _external_running += 1


def publish_external_result(result: ToolResult) -> None:
    """Доставить агенту результат любой фоновой работы на ближайшем ходу."""
    global _external_running
    with _lock:
        _external_results.append(result)
        _external_running = max(0, _external_running - 1)
    _signal_finish()


def wait_background_result(job_id: str) -> ToolResult:
    """Дождаться скрытой managed-задачи и забрать её результат без авто-доставки."""
    while True:
        with _lock:
            job = _jobs.get(job_id)
            if job is None:
                return ToolResult(
                    name="shell",
                    status="error",
                    output="Background job disappeared.",
                    exit_code=-1,
                )
            if job.status != "running":
                _jobs.pop(job_id, None)
                return ToolResult(
                    name="shell",
                    status="ok" if job.status == "done" else "error",
                    output=job.output,
                    exit_code=job.exit_code,
                    command=job.command,
                )
        time.sleep(0.05)


def drain_finished_results() -> list[ToolResult]:
    """Возвращает ToolResult-уведомления по завершённым, ещё не доставленным задачам."""
    out: list[ToolResult] = []
    delivered_ids: list[str] = []
    with _lock:
        if _external_results:
            out.extend(_external_results)
            _external_results.clear()
        for job in _jobs.values():
            if job.status == "running" or job.delivered:
                continue
            job.delivered = True
            delivered_ids.append(job.id)
            elapsed = max(0.0, job.finished_at - job.started_at)
            header = (
                f"[background {job.id} finished — exit {job.exit_code}, "
                f"{format_duration(elapsed)}]\n$ {job.command}\n"
            )
            out.append(
                ToolResult(
                    name="shell",
                    status="ok" if job.status == "done" else "error",
                    output=header + job.output,
                    exit_code=job.exit_code,
                    command=job.command,
                )
            )
        # Delivered jobs have no remaining lifecycle role. Keeping them forever
        # made long-running CLI processes retain every command/output in memory.
        for job_id in delivered_ids:
            _jobs.pop(job_id, None)
    if delivered_ids:
        try:
            from agent.background_render import detach_background_job

            for job_id in delivered_ids:
                detach_background_job(job_id)
        except Exception:
            logger.debug("background UI detach failed", exc_info=True)
    if out:
        logger.info("background drain: delivering {} finished job(s)", len(out))
    return out
