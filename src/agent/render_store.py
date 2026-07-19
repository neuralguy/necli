"""Хранилище рендер-событий сессии для Ctrl+O toggle (compact ↔ verbose).

Каждое событие, которое агент рисует в терминал (user-message, assistant-block,
tool call+result), складывается в RenderStore. При Ctrl+O CLI очищает экран
и заново печатает store в противоположном режиме.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import tools

logger = logging.getLogger(__name__)


_VERSION = 1


@dataclass
class RenderItem:
    kind: str
    payload: dict
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload, "ts": self.ts}

    @classmethod
    def from_dict(cls, d: dict) -> RenderItem:
        return cls(
            kind=d.get("kind", "unknown"),
            payload=d.get("payload", {}) or {},
            ts=float(d.get("ts", time.time())),
        )


def _serialize_tool_call(call: tools.ToolCall) -> dict:
    return {
        "command": call.command,
        "tool_name": call.tool_name,
        "args": dict(call.args or {}),
        "raw": call.raw or "",
    }


def _deserialize_tool_call(d: dict) -> tools.ToolCall:
    return tools.ToolCall(
        command=d.get("command", ""),
        tool_name=d.get("tool_name", ""),
        args=dict(d.get("args") or {}),
        raw=d.get("raw", ""),
    )


def _serialize_tool_result(result: tools.ToolResult) -> dict:
    out = {
        "name": result.name,
        "status": result.status,
        "output": result.output or "",
        "exit_code": result.exit_code,
        "command": result.command or "",
        "elapsed": float(result.elapsed or 0.0),
        "full_content": bool(getattr(result, "full_content", False)),
        "fatal": bool(getattr(result, "fatal", False)),
    }
    img = getattr(result, "image_path", None)
    if img is not None:
        out["image_path"] = str(img)
    imgs = getattr(result, "image_paths", None)
    if imgs:
        out["image_paths"] = [str(p) for p in imgs]
    ls = getattr(result, "line_starts", None)
    if ls:
        out["line_starts"] = list(ls)
    return out


def _deserialize_tool_result(d: dict) -> tools.ToolResult:
    r = tools.ToolResult(
        name=d.get("name", "unknown"),
        status=d.get("status", "ok"),
        output=d.get("output", ""),
        exit_code=int(d.get("exit_code", 0) or 0),
        command=d.get("command", ""),
    )
    r.elapsed = float(d.get("elapsed", 0.0) or 0.0)
    try:
        r.full_content = bool(d.get("full_content", False))
    except Exception:
        logger.debug("render_store: full_content deserialize failed", exc_info=True)
    try:
        r.fatal = bool(d.get("fatal", False))
    except Exception:
        logger.debug("render_store: fatal deserialize failed", exc_info=True)
    img = d.get("image_path")
    if img:
        try:
            from pathlib import Path as _P  # noqa: N814
            r.image_path = _P(img)
        except Exception:
            logger.debug("render_store: image_path deserialize failed", exc_info=True)
    imgs = d.get("image_paths")
    if imgs:
        try:
            from pathlib import Path as _P  # noqa: N814
            r.image_paths = [_P(p) for p in imgs]
        except Exception:
            logger.debug("render_store: image_paths deserialize failed", exc_info=True)
    ls = d.get("line_starts")
    if ls:
        try:
            r.line_starts = [int(x) for x in ls]
        except Exception:
            logger.debug("render_store: line_starts deserialize failed", exc_info=True)
    return r


class RenderStore:
    def __init__(self):
        self.items: list[RenderItem] = []

    def add(self, kind: str, payload: dict) -> RenderItem:
        item = RenderItem(kind=kind, payload=payload)
        self.items.append(item)
        return item

    def add_user(self, text: str, status: str = "") -> RenderItem:
        return self.add("user", {"text": text or "", "status": status or ""})

    def add_assistant_block(self, text: str, subtitle: str = "", message_num: int = 0) -> RenderItem:
        return self.add("assistant", {
            "text": text or "",
            "subtitle": subtitle or "",
            "message_num": int(message_num or 0),
        })

    def add_tool(self, call: tools.ToolCall, result: tools.ToolResult | None,
                 subtitle: str = "") -> RenderItem:
        return self.add("tool", {
            "call": _serialize_tool_call(call) if call else None,
            "result": _serialize_tool_result(result) if result else None,
            "subtitle": subtitle or "",
        })

    def add_command_only(self, call: tools.ToolCall, subtitle: str = "") -> RenderItem:
        return self.add("command_only", {
            "call": _serialize_tool_call(call) if call else None,
            "subtitle": subtitle or "",
        })

    def add_plan(
        self,
        plan_snapshot: dict,
        action: str = "",
        focus_index: int | None = None,
    ) -> RenderItem:
        payload = {"plan": plan_snapshot or {}}
        if action:
            payload["action"] = action
        if focus_index is not None:
            payload["focus_index"] = int(focus_index)
        return self.add("plan", payload)

    def add_think(self, steps: list[str]) -> RenderItem:
        return self.add("think", {"steps": [s for s in (steps or []) if s]})

    def to_dict(self) -> dict:
        return {"version": _VERSION, "items": [it.to_dict() for it in self.items]}

    @classmethod
    def from_dict(cls, d: dict) -> RenderStore:
        store = cls()
        if not isinstance(d, dict):
            return store
        for raw in (d.get("items") or []):
            if isinstance(raw, dict):
                store.items.append(RenderItem.from_dict(raw))
        return store

    def clear(self) -> None:
        self.items.clear()

    def __len__(self) -> int:
        return len(self.items)


def deserialize_tool_call(d: dict) -> tools.ToolCall:
    return _deserialize_tool_call(d)


def deserialize_tool_result(d: dict) -> tools.ToolResult:
    return _deserialize_tool_result(d)
