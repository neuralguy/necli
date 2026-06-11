"""Модель одного сообщения в истории сессии."""

import time
import uuid
from typing import Optional

from session.tokens import count_tokens
from session._time import format_msk


def _new_msg_id() -> str:
    return uuid.uuid4().hex[:12]


class Message:
    """Сообщение в истории.

    id / parent_id — для дерева вариантов (branches). Активный линейный путь
    хранится в Session.messages, альтернативы в Session._branch_alternatives.
    Старые сессии без id получают их при загрузке (см. from_dict).

    attachments — список {path, name, mime, is_image} для прикреплённых файлов.

    tokens — итоговое число токенов для подсчёта стоимости.
    usage — сырой dict от провайдера.
    """

    __slots__ = (
        "role", "content", "model", "timestamp", "tokens", "duration", "usage",
        "id", "parent_id", "attachments", "thoughts",
    )

    def __init__(
        self,
        role: str,
        content: str,
        model: str = "",
        timestamp: Optional[float] = None,
        tokens: Optional[int] = None,
        duration: Optional[float] = None,
        usage: Optional[dict] = None,
        id: Optional[str] = None,
        parent_id: Optional[str] = None,
        attachments: Optional[list] = None,
        thoughts: Optional[list] = None,
    ):
        self.role = role
        self.content = content
        self.model = model
        self.timestamp = time.time() if timestamp is None else timestamp
        self.duration = duration
        self.usage = usage if usage else None
        self.id = id or _new_msg_id()
        self.parent_id = parent_id
        self.attachments = list(attachments) if attachments else []
        self.thoughts = [str(t) for t in thoughts] if thoughts else []

        if tokens is not None:
            self.tokens = tokens
        elif self.usage:
            if role == "assistant":
                self.tokens = int(self.usage.get("output") or 0) or count_tokens(content, model)
            else:
                self.tokens = count_tokens(content, model)
        else:
            self.tokens = count_tokens(content, model)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "role": self.role,
            "content": self.content,
            "model": self.model,
            "timestamp": self.timestamp,
            "time": format_msk(self.timestamp),
            "tokens": self.tokens,
        }
        if self.parent_id:
            d["parent_id"] = self.parent_id
        if self.attachments:
            d["attachments"] = list(self.attachments)
        if self.thoughts:
            d["thoughts"] = list(self.thoughts)
        if self.duration is not None:
            d["duration"] = round(self.duration, 2)
        if self.usage:
            d["usage"] = self.usage
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            role=d["role"],
            content=d["content"],
            model=d.get("model", ""),
            timestamp=d.get("timestamp", time.time()),
            tokens=d.get("tokens"),
            duration=d.get("duration"),
            usage=d.get("usage"),
            id=d.get("id"),
            parent_id=d.get("parent_id"),
            attachments=d.get("attachments"),
            thoughts=d.get("thoughts"),
        )