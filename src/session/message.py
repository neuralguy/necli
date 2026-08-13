"""Модель одного сообщения в истории сессии."""

import time
import uuid

from session._time import format_msk
from session.tokens import count_tokens


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
        "attachments",
        "content",
        "duration",
        "id",
        "model",
        "parent_id",
        "reasoning",
        "role",
        "thoughts",
        "timestamp",
        "tokens",
        "usage",
    )

    def __init__(
        self,
        role: str,
        content: str,
        model: str = "",
        timestamp: float | None = None,
        tokens: int | None = None,
        duration: float | None = None,
        usage: dict | None = None,
        id: str | None = None,
        parent_id: str | None = None,
        attachments: list | None = None,
        thoughts: list | None = None,
        reasoning: str = "",
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
        self.reasoning = str(reasoning) if reasoning else ""

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
        if self.reasoning:
            d["reasoning"] = self.reasoning
        if self.duration is not None:
            d["duration"] = round(self.duration, 2)
        if self.usage:
            d["usage"] = self.usage
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        if not isinstance(d, dict):
            raise TypeError("message must be an object")
        role = d.get("role")
        content = d.get("content")
        if not isinstance(role, str) or not isinstance(content, str):
            raise ValueError("message role/content must be strings")

        timestamp = d.get("timestamp", time.time())
        tokens = d.get("tokens")
        duration = d.get("duration")
        try:
            timestamp = float(timestamp)
            tokens = int(tokens) if tokens is not None else None
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError) as e:
            raise ValueError(f"invalid message numeric field: {e}") from e

        usage = d.get("usage")
        if usage is not None and not isinstance(usage, dict):
            raise ValueError("message usage must be an object")
        attachments = d.get("attachments")
        if attachments is not None and not isinstance(attachments, list):
            raise ValueError("message attachments must be a list")
        thoughts = d.get("thoughts")
        if thoughts is not None and not isinstance(thoughts, list):
            raise ValueError("message thoughts must be a list")

        return cls(
            role=role,
            content=content,
            model=str(d.get("model") or ""),
            timestamp=timestamp,
            tokens=tokens,
            duration=duration,
            usage=usage,
            id=str(d["id"]) if d.get("id") else None,
            parent_id=str(d["parent_id"]) if d.get("parent_id") else None,
            attachments=attachments,
            thoughts=thoughts,
            reasoning=str(d.get("reasoning") or ""),
        )
