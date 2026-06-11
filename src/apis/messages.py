"""Минималистичные классы сообщений и стрим-чанков для собственного HTTP-клиента.

Заменяют langchain_core.messages. Совместимы по интерфейсу с тем подмножеством,
которое реально используется в проекте:
  - SystemMessage(content=...)
  - HumanMessage(content=...)
  - AIMessage(content=..., tool_calls=..., additional_kwargs=..., response_metadata=..., usage_metadata=...)
  - ToolMessage(content=..., tool_call_id=..., name=...)
  - AIMessageChunk(...) — поддерживает __add__ для аккумуляции стрима

tool_calls имеют форму [{"id": str, "name": str, "args": dict, "type": "tool_call"}].
"""

from __future__ import annotations

from typing import Any, Optional


class BaseMessage:
    role: str = ""

    def __init__(self, content: Any = "", **kwargs: Any) -> None:
        self.content = content
        self.additional_kwargs: dict = kwargs.pop("additional_kwargs", None) or {}
        self.response_metadata: dict = kwargs.pop("response_metadata", None) or {}

    def __repr__(self) -> str:
        c = self.content if isinstance(self.content, str) else "<complex>"
        if isinstance(c, str) and len(c) > 80:
            c = c[:80] + "..."
        return f"{type(self).__name__}({c!r})"


class SystemMessage(BaseMessage):
    role = "system"


class HumanMessage(BaseMessage):
    role = "user"


class ToolMessage(BaseMessage):
    role = "tool"

    def __init__(self, content: Any = "", tool_call_id: str = "", name: str = "", **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self.tool_call_id = tool_call_id
        self.name = name or "tool"


class AIMessage(BaseMessage):
    role = "assistant"

    def __init__(
        self,
        content: Any = "",
        tool_calls: Optional[list] = None,
        usage_metadata: Optional[dict] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(content, **kwargs)
        self.tool_calls: list = list(tool_calls or [])
        self.usage_metadata: dict = usage_metadata or {}


class AIMessageChunk(AIMessage):
    """Аккумулятор стрим-чанков. Поддерживает сложение (chunk + chunk)."""

    def __init__(
        self,
        content: Any = "",
        tool_calls: Optional[list] = None,
        tool_call_chunks: Optional[list] = None,
        usage_metadata: Optional[dict] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(content, tool_calls=tool_calls, usage_metadata=usage_metadata, **kwargs)
        self.tool_call_chunks: list = list(tool_call_chunks or [])

    def __add__(self, other: "AIMessageChunk") -> "AIMessageChunk":
        if not isinstance(other, AIMessageChunk):
            return self

        # content: конкатенация строк
        a = self.content if isinstance(self.content, str) else ""
        b = other.content if isinstance(other.content, str) else ""
        merged_content = a + b

        # additional_kwargs: reasoning_content — конкатенация, остальное — overwrite-merge
        merged_kw = dict(self.additional_kwargs)
        for k, v in (other.additional_kwargs or {}).items():
            if k == "reasoning_content":
                merged_kw[k] = (merged_kw.get(k) or "") + (v or "")
            else:
                merged_kw[k] = v

        # response_metadata: overwrite-merge
        merged_resp = dict(self.response_metadata)
        merged_resp.update(other.response_metadata or {})

        # usage_metadata: take latest non-empty
        merged_usage = other.usage_metadata or self.usage_metadata or {}

        # tool_call_chunks: накапливаем по index, склеивая args как строки
        merged_tc_chunks = _merge_tool_call_chunks(
            self.tool_call_chunks, other.tool_call_chunks,
        )

        # tool_calls: финальные структуры — берём результат свёртки tc_chunks
        merged_tool_calls = _tc_chunks_to_tool_calls(merged_tc_chunks)
        if not merged_tool_calls:
            merged_tool_calls = list(other.tool_calls or self.tool_calls or [])

        return AIMessageChunk(
            content=merged_content,
            tool_calls=merged_tool_calls,
            tool_call_chunks=merged_tc_chunks,
            usage_metadata=merged_usage,
            additional_kwargs=merged_kw,
            response_metadata=merged_resp,
        )


def _merge_tool_call_chunks(a: list, b: list) -> list:
    """Аккумулирует tool_call_chunks по index: id/name берётся первый non-empty, args конкатенируются."""
    by_index: dict[int, dict] = {}
    order: list[int] = []
    for src in (a, b):
        for ch in src or []:
            idx = ch.get("index", 0)
            if idx not in by_index:
                by_index[idx] = {"index": idx, "id": None, "name": None, "args": ""}
                order.append(idx)
            slot = by_index[idx]
            if ch.get("id") and not slot.get("id"):
                slot["id"] = ch["id"]
            if ch.get("name") and not slot.get("name"):
                slot["name"] = ch["name"]
            args_piece = ch.get("args") or ""
            if isinstance(args_piece, str) and args_piece:
                slot["args"] += args_piece
    return [by_index[i] for i in order]


def _tc_chunks_to_tool_calls(chunks: list) -> list:
    """Конвертирует аккумулированные tool_call_chunks в финальные tool_calls с распарсенным args."""
    import json
    result = []
    for ch in chunks or []:
        args_raw = ch.get("args") or ""
        if isinstance(args_raw, str):
            try:
                args = json.loads(args_raw) if args_raw.strip() else {}
            except json.JSONDecodeError as e:
                args = {
                    "_invalid_json": True,
                    "_raw_args": args_raw,
                    "_parse_error": str(e),
                }
        elif isinstance(args_raw, dict):
            args = args_raw
        else:
            args = {}
        result.append({
            "id": ch.get("id") or "",
            "name": ch.get("name") or "",
            "args": args,
            "type": "tool_call",
        })
    return result


__all__ = [
    "BaseMessage",
    "SystemMessage",
    "HumanMessage",
    "AIMessage",
    "ToolMessage",
    "AIMessageChunk",
]