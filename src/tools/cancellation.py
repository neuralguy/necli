"""Контекст отмены синхронного инструмента субагента."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from contextvars import ContextVar


class CancellationScope:
    def __init__(self) -> None:
        self.event = threading.Event()
        self._lock = threading.Lock()
        self._job_id: str | None = None

    def bind_job(self, job_id: str) -> None:
        with self._lock:
            self._job_id = job_id
            cancelled = self.event.is_set()
        if cancelled:
            from tools.background import cancel_background
            cancel_background(job_id)

    def clear_job(self, job_id: str) -> None:
        with self._lock:
            if self._job_id == job_id:
                self._job_id = None

    def cancel(self) -> None:
        self.event.set()
        with self._lock:
            job_id = self._job_id
        if job_id:
            from tools.background import cancel_background
            cancel_background(job_id)


_scope: ContextVar[CancellationScope | None] = ContextVar(
    "tool_cancellation_scope",
    default=None,
)


@contextmanager
def use_cancellation_scope(scope: CancellationScope | None):
    token = _scope.set(scope)
    try:
        yield
    finally:
        _scope.reset(token)


def current_cancellation_scope() -> CancellationScope | None:
    return _scope.get()
