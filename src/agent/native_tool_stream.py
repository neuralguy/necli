"""Инкрементальный парсер native tool_calls из OpenAI-совместимого стрима.

Протокол (упрощённый, без corrected/rollback):
- Индексы tool calls монотонны: 0 0 0 → 1 1 1 → 2 2 2
- Как только появился index=N+1 и JSON для N валиден → N запечатывается навсегда
- Все последующие чанки для запечатанного индекса игнорируются (late_chunks++)
- На finish_reason запечатывается последний вызов

Состояния: COLLECTING → SEALED → EXECUTED
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum, auto

logger = logging.getLogger(__name__)


def merge_argument_fragment(current: str, piece: str) -> str:
    """Merge OpenAI-style argument deltas and cumulative proxy chunks safely.

    Most providers stream only the new suffix, but some OpenAI-compatible
    proxies repeat the whole accumulated JSON on every chunk. Blind `+=` then
    grows the buffer quadratically and produces invalid JSON. Detect the
    cumulative form and replace instead of appending.
    """
    if not piece:
        return current
    if not current:
        return piece
    if piece == current or piece.startswith(current):
        return piece
    return current + piece


class CallState(Enum):
    COLLECTING = auto()
    SEALED = auto()
    EXECUTED = auto()


@dataclass
class NativeCall:
    """Один native tool call в процессе сбора."""

    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""
    state: CallState = CallState.COLLECTING
    # Инкрементальное состояние JSON-сканера. Раньше is_valid_json делал
    # json.loads() ВСЕГО растущего arguments на каждом SSE-чанке → O(N²).
    _json_started: bool = False
    _json_complete: bool = False
    _json_invalid: bool = False
    _json_in_string: bool = False
    _json_escape: bool = False
    _json_stack: list[str] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if self.arguments:
            # Редкий direct-construction path (в тестах/внешнем коде).
            self._scan_json_fragment(self.arguments)

    def _scan_json_fragment(self, fragment: str) -> None:
        """O(len(fragment)) structural scan without reparsing old prefixes."""
        if not fragment or self._json_invalid:
            return
        for ch in fragment:
            if self._json_complete:
                # После закрытия top-level object допустим только whitespace.
                if not ch.isspace():
                    self._json_invalid = True
                continue

            if not self._json_started:
                if ch.isspace():
                    continue
                if ch != "{":
                    self._json_invalid = True
                    return
                self._json_started = True
                self._json_stack.append("}")
                continue

            if self._json_in_string:
                if self._json_escape:
                    self._json_escape = False
                elif ch == "\\":
                    self._json_escape = True
                elif ch == '"':
                    self._json_in_string = False
                continue

            if ch == '"':
                self._json_in_string = True
            elif ch == "{":
                self._json_stack.append("}")
            elif ch == "[":
                self._json_stack.append("]")
            elif ch in ("}", "]"):
                if not self._json_stack or self._json_stack[-1] != ch:
                    self._json_invalid = True
                    return
                self._json_stack.pop()
                if not self._json_stack:
                    self._json_complete = True

    def append_argument_fragment(self, piece: str) -> None:
        """Append delta/cumulative args and scan only the newly arrived suffix."""
        if not piece:
            return
        current = self.arguments
        if not current:
            self.arguments = piece
            self._scan_json_fragment(piece)
            return
        if piece == current:
            return
        if piece.startswith(current):
            # cumulative proxy chunk: current is an exact prefix, so scan only
            # the suffix instead of rescanning the whole growing JSON.
            suffix = piece[len(current) :]
            self.arguments = piece
            self._scan_json_fragment(suffix)
            return
        self.arguments = current + piece
        self._scan_json_fragment(piece)

    @property
    def is_valid_json(self) -> bool:
        if (
            not self.arguments
            or not self._json_complete
            or self._json_invalid
            or self._json_in_string
            or self._json_stack
        ):
            return False
        try:
            # Structural completeness is O(1); json.loads runs only when the
            # top-level object has actually closed (normally exactly once).
            value = json.loads(self.arguments)
            return isinstance(value, dict)
        except (json.JSONDecodeError, ValueError):
            return False

    def parsed_args(self) -> dict | None:
        try:
            return json.loads(self.arguments)
        except (json.JSONDecodeError, ValueError):
            return None


class NativeToolStreamParser:
    """COLLECTING → SEALED → EXECUTED. Без corrected/rollback."""

    def __init__(self) -> None:
        self._calls: dict[int, NativeCall] = {}
        self._order: list[int] = []
        self._sealed: set[int] = set()
        self._finished = False
        self.late_chunks = 0

    @property
    def calls(self) -> list[NativeCall]:
        return [self._calls[i] for i in self._order]

    def feed(self, chunks: list[dict]) -> list[NativeCall]:
        """Покормить чанками. Возвращает вызовы, готовые к исполнению (sealed)."""
        if self._finished:
            return []

        ready: list[NativeCall] = []

        for chunk in chunks:
            if not isinstance(chunk, dict):
                continue

            index = chunk.get("index", 0)
            if not isinstance(index, int):
                index = 0

            # Запечатанный индекс → игнорируем навсегда
            if index in self._sealed:
                self.late_chunks += 1
                logger.debug(
                    "native_tool_stream: late chunk for sealed index %d (total late: %d)",
                    index,
                    self.late_chunks,
                )
                continue

            # Появился больший индекс → запечатываем предыдущие
            if self._order and index > self._order[-1]:
                for prev_idx in self._order:
                    if prev_idx < index and prev_idx not in self._sealed:
                        prev = self._calls[prev_idx]
                        if prev.is_valid_json:
                            prev.state = CallState.SEALED
                            self._sealed.add(prev_idx)
                            ready.append(prev)

            # Добавляем / обновляем вызов
            if index not in self._calls:
                self._calls[index] = NativeCall(index=index)
                self._order.append(index)

            call = self._calls[index]
            name = chunk.get("name")
            cid = chunk.get("id")
            args = chunk.get("args")

            if name and not call.name:
                call.name = str(name)
            if cid and not call.id:
                call.id = str(cid)
            if isinstance(args, str) and args:
                call.append_argument_fragment(args)
            elif isinstance(args, dict):
                call.append_argument_fragment(json.dumps(args, ensure_ascii=False))

            # В native streaming валидный JSON-объект означает, что arguments
            # этого вызова уже полностью дописаны. Не ждём появления следующего
            # index или finish_reason: иначе последний/единственный tool call
            # неизбежно исполняется только после завершения всего ответа модели.
            if (
                call.state is CallState.COLLECTING
                and call.name
                and call.is_valid_json
                and index not in self._sealed
            ):
                call.state = CallState.SEALED
                self._sealed.add(index)
                ready.append(call)

        return ready

    def finish(self) -> list[NativeCall]:
        """Конец стрима: запечатываем всё оставшееся."""
        self._finished = True
        ready: list[NativeCall] = []
        for idx in self._order:
            if idx not in self._sealed:
                call = self._calls[idx]
                call.state = CallState.SEALED
                self._sealed.add(idx)
                ready.append(call)
        if self.late_chunks:
            logger.info(
                "native_tool_stream: finished with %d late chunk(s) ignored",
                self.late_chunks,
            )
        return ready
