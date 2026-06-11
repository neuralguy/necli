"""Workflow state models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any
import json
import os
import uuid


def utc_now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def new_run_id(name: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in (name or "workflow").lower())
    safe = "-".join(p for p in safe.split("-") if p)[:48] or "workflow"
    return f"{safe}-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"


@dataclass
class WorkflowAgentState:
    id: str
    label: str
    phase: str
    status: str = "pending"
    prompt: str = ""
    model: str = ""
    role: str = ""
    preset: str = ""
    cache_key: str = ""
    cached: bool = False
    artifact_dir: str = ""
    started_at: str = ""
    finished_at: str = ""
    result: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowPhaseState:
    id: str
    title: str
    detail: str = ""
    status: str = "pending"
    started_at: str = ""
    finished_at: str = ""
    logs: list[str] = field(default_factory=list)
    agents: list[WorkflowAgentState] = field(default_factory=list)


@dataclass
class WorkflowRunState:
    id: str
    name: str
    description: str = ""
    status: str = "pending"
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""
    phases: list[WorkflowPhaseState] = field(default_factory=list)
    result: Any = None
    error: str = ""
    run_dir: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_json(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)