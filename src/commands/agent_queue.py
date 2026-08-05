"""Очередь ходов агента.

Зачем очередь
-------------
Поле ввода теперь доступно всегда, в том числе пока агент отвечает. Значит
пользователь может отправить второе сообщение в середине хода. Запускать второй
ход параллельно нельзя: в коде полно состояния, рассчитанного ровно на один
активный ход — модульная глобалка `_current_ctx`, общая история `_api_session`,
`session.add_user_message()`, переименовывающий каталог сессии.
Параллельные ходы гарантированно испортили бы историю.

Поэтому очередь **строго последовательная**: ровно один ход за раз.

Батчинг
-------
Сообщения, накопившиеся пока агент занят, забираются одним заходом и
склеиваются в один запрос. Так же ведёт себя Claude Code: три реплики подряд,
отправленные во время ответа, обрабатываются вместе, а не тремя ходами.

Где печатается эхо (важно)
--------------------------
Пока реплика ждёт, Shell рисует её динамической строкой `  ❯ текст` над полем:
её можно забрать обратно клавишей `↑`. Постоянное эхо в scrollback печатает **воркер,
прямо перед началом своего ответа**, а не главный цикл в момент Enter. Иначе эхо новых
сообщений вклинилось бы в середину стрима текущего ответа. Всё эхо одного батча идёт подряд,
затем стрим.

Slash-команды
-------------
Команды, которые только показывают интерактивный виджет и не оставляют вывода
в scrollback, выполняются немедленно и мимо очереди — их можно вызывать во
время работы агента. Команды, печатающие вывод или меняющие сессию, встают в
очередь и выполняются по одной, без склейки: иначе их вывод оказался бы посреди
чужого ответа.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from config.i18n import t as tr

logger = logging.getLogger(__name__)

KIND_USER = "user"
KIND_SLASH = "slash"

#: Команды, открывающие интерактивный виджет и не пишущие в scrollback.
#: Их можно выполнять сразу, параллельно работающему агенту: виджет забирает
#: нижнюю зону, стрим продолжает идти выше.
IMMEDIATE_SLASH: frozenset[str] = frozenset({
    "/agents", "/api", "/autoprune", "/help", "/lang", "/lsp", "/mcp",
    "/models", "/params", "/permissions", "/proxy", "/skills",
    "/stats", "/themes", "/tg",
})


def is_immediate_slash(text: str) -> bool:
    """True — команду можно выполнить сразу, не ставя в очередь."""
    head = (text or "").strip().split(" ", 1)[0].lower()
    return head in IMMEDIATE_SLASH


@dataclass
class QueueItem:
    kind: str
    text: str



class AgentQueue:
    """Последовательный исполнитель ходов агента с батчингом сообщений."""

    def __init__(
        self,
        run_turn: Callable[[list[str]], Awaitable[None]],
        run_slash: Callable[[str], Awaitable[None]],
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._run_turn = run_turn
        self._run_slash = run_slash
        self._on_change = on_change
        self.pending: list[QueueItem] = []
        # Первый элемент резервируется синхронно в submit_* ещё до того, как
        # asyncio успеет запустить worker. Иначе несколько быстрых Enter
        # попадали в pending за один тик, _next_batch забирал их ВСЕ как
        # текущий ход, и визуальная/редактируемая очередь оставалась пустой.
        self._claimed: list[QueueItem] = []
        self.busy: bool = False
        self.current_kind: str | None = None
        self._wake = asyncio.Event()
        self._stopped = False
        self._worker_task: asyncio.Task | None = None

    # ───────────────────────────── постановка ──────────────────────────────
    def submit_user(self, text: str) -> None:
        self._push(QueueItem(KIND_USER, text))

    def submit_slash(self, text: str) -> None:
        self._push(QueueItem(KIND_SLASH, text))

    def _push(self, item: QueueItem) -> None:
        if not self.busy and self.current_kind is None and not self.pending:
            # Claim the first item before yielding to the worker. Every later
            # submission is therefore genuinely pending and remains visible
            # and editable even when several submissions arrive in one loop
            # iteration.
            self._claimed = [item]
            self.current_kind = item.kind
        else:
            self.pending.append(item)
        # busy выставляем синхронно: иначе главный цикл успеет решить, что
        # агент свободен, и напечатает эхо сам — оно уедет в середину стрима.
        self.busy = True
        self._wake.set()
        self._notify()

    def _notify(self) -> None:
        if self._on_change is not None:
            try:
                self._on_change()
            except Exception:
                logger.debug("queue on_change failed", exc_info=True)

    # ───────────────────────────── состояние ───────────────────────────────
    def pending_user_texts(self) -> list[str]:
        """Editable user messages that have not entered a running batch yet."""
        return [item.text for item in self.pending if item.kind == KIND_USER]

    def pop_all_users_for_edit(self) -> str | None:
        """Remove every queued user message and return one editable batch.

        The currently running batch is never touched: only entries still in
        ``pending`` are editable. Messages are joined exactly as the worker
        would join its next batch, so editing and resubmitting preserves their
        original order and meaning.
        """
        texts = [item.text for item in self.pending if item.kind == KIND_USER]
        if not texts:
            return None
        self.pending[:] = [item for item in self.pending if item.kind != KIND_USER]
        if not self.pending and self.current_kind is None:
            self.busy = False
            self._wake.clear()
        self._notify()
        return "\n".join(texts)

    def status_text(self) -> str:
        """Короткая сводка для строки подсказок под рамкой."""
        n = len(self.pending)
        if not n:
            return ""
        return tr("queue.waiting", n=n)

    # ───────────────────────────── исполнение ──────────────────────────────
    async def _next_batch(self) -> list[QueueItem] | None:
        """Забрать следующую порцию работы.

        Подряд идущие сообщения пользователя отдаются пачкой — они склеятся в
        один ход. Slash-команда всегда возвращается одна: её вывод не должен
        смешиваться ни с чем.
        """
        while True:
            if self._stopped:
                return None
            if self._claimed:
                batch = self._claimed
                self._claimed = []
                return batch
            if self.pending:
                first = self.pending[0]
                if first.kind == KIND_SLASH:
                    return [self.pending.pop(0)]
                batch: list[QueueItem] = []
                while self.pending and self.pending[0].kind == KIND_USER:
                    batch.append(self.pending.pop(0))
                return batch
            self.busy = False
            self.current_kind = None
            self._notify()
            self._wake.clear()
            await self._wake.wait()

    async def worker(self) -> None:
        while not self._stopped:
            batch = await self._next_batch()
            if batch is None:
                return
            self.busy = True
            self.current_kind = batch[0].kind
            self._notify()
            try:
                if batch[0].kind == KIND_SLASH:
                    await self._run_slash(batch[0].text)
                else:
                    await self._run_turn([i.text for i in batch])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ход агента завершился ошибкой")
            finally:
                if not self.pending:
                    self.busy = False
                    self.current_kind = None
                self._notify()

    def start(self) -> asyncio.Task:
        self._worker_task = asyncio.create_task(self.worker(), name="agent-queue")
        return self._worker_task

    async def stop(self) -> None:
        self._stopped = True
        self._wake.set()
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except (asyncio.CancelledError, Exception):
                pass


__all__ = [
    "IMMEDIATE_SLASH", "KIND_SLASH", "KIND_USER",
    "AgentQueue", "QueueItem", "is_immediate_slash",
]
