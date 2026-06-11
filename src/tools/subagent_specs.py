"""Normalize subagent orchestration specs into plain subagent tasks."""

from __future__ import annotations

import json
from string import Formatter
from typing import Any


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _clean_str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_depends_on(raw: Any) -> list[int]:
    if raw is None:
        return []
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, str):
        raw = [p for p in raw.replace(",", " ").split() if p]
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[int] = []
    for item in raw:
        try:
            dep = int(item)
        except (TypeError, ValueError):
            continue
        if dep > 0:
            out.append(dep)
    return out


def normalize_task(raw: Any, *, phase: str = "", depends_on: list[int] | None = None) -> dict[str, Any] | None:
    if isinstance(raw, str):
        raw = {"prompt": raw}
    if not isinstance(raw, dict):
        return None
    prompt = _clean_str(raw.get("prompt"))
    if not prompt:
        return None
    task: dict[str, Any] = {"prompt": prompt, "mode": "agent"}
    for key in ("model", "role", "preset", "label"):
        value = _clean_str(raw.get(key))
        if key == "role":
            value = value.lower()
        if value:
            task[key] = value
    task_phase = _clean_str(raw.get("phase")) or phase
    if task_phase:
        task["phase"] = task_phase

    deps = parse_depends_on(raw.get("depends_on"))
    if depends_on:
        deps.extend(depends_on)
    deps = sorted(set(d for d in deps if d > 0))
    if deps:
        task["depends_on"] = deps
    return task


def _item_context(item: Any, item_index: int, stage_index: int, phase: str = "") -> dict[str, Any]:
    if isinstance(item, (dict, list)):
        item_json = json.dumps(item, ensure_ascii=False, sort_keys=True)
    else:
        item_json = str(item)
    ctx: dict[str, Any] = {
        "item": item,
        "item_json": item_json,
        "index": item_index,
        "item_index": item_index,
        "stage": stage_index,
        "stage_index": stage_index,
        "phase": phase,
    }
    if isinstance(item, dict):
        for key, value in item.items():
            if isinstance(key, str):
                ctx[key] = value
    return ctx


def render_template(template: str, item: Any, item_index: int, stage_index: int, phase: str = "") -> str:
    ctx = _SafeFormatDict(_item_context(item, item_index, stage_index, phase))
    try:
        return template.format_map(ctx)
    except Exception:
        parts: list[str] = []
        for literal, field, spec, conv in Formatter().parse(template):
            parts.append(literal)
            if field is None:
                continue
            value = ctx.get(field, "{" + field + "}")
            if conv == "r":
                value = repr(value)
            elif conv == "s":
                value = str(value)
            try:
                parts.append(format(value, spec) if spec else str(value))
            except Exception:
                parts.append(str(value))
        return "".join(parts)


def _append_task(tasks: list[dict[str, Any]], task: dict[str, Any] | None) -> bool:
    if not task:
        return True
    tasks.append(task)
    return len(tasks) < 100


def _pipeline_tasks(
    args: dict[str, Any],
    *,
    phase: str = "",
    depends_on: list[int] | None = None,
    base_index: int = 0,
) -> list[dict[str, Any]]:
    items = args.get("items")
    stages = args.get("stages")
    if not isinstance(items, list) or not isinstance(stages, list):
        return []

    tasks: list[dict[str, Any]] = []
    for item_index, item in enumerate(items, start=1):
        prev_task_index: int | None = None
        for stage_index, stage in enumerate(stages, start=1):
            if isinstance(stage, str):
                stage = {"prompt": stage}
            if not isinstance(stage, dict):
                continue
            stage_name = _clean_str(stage.get("phase") or stage.get("name") or stage.get("title")) or phase
            template = _clean_str(stage.get("prompt") or stage.get("template"))
            if not template:
                continue
            prompt = render_template(template, item, item_index, stage_index, stage_name).strip()
            if not prompt:
                continue

            raw_task = dict(stage)
            raw_task["prompt"] = prompt
            raw_task.setdefault("phase", stage_name)
            raw_task.pop("template", None)
            chain_deps = list(depends_on or [])
            if prev_task_index is not None:
                chain_deps.append(base_index + prev_task_index)
            task = normalize_task(raw_task, phase=stage_name, depends_on=chain_deps)
            if not _append_task(tasks, task):
                return tasks
            prev_task_index = len(tasks)
    return tasks


def build_subagent_task_specs(args: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    tasks: list[dict[str, Any]] = []
    name = _clean_str(args.get("name") or args.get("goal")) or "subagents"

    prompt = _clean_str(args.get("prompt"))
    if prompt:
        task = normalize_task(args)
        if task:
            tasks.append(task)
        return tasks[:100], f"{name} · single · {len(tasks[:100])} task(s)"

    phases = args.get("phases")
    if isinstance(phases, list):
        previous_phase: list[int] = []
        for phase_index, phase_raw in enumerate(phases, start=1):
            if isinstance(phase_raw, str):
                phase_raw = {"name": f"Phase {phase_index}", "tasks": [phase_raw]}
            if not isinstance(phase_raw, dict):
                continue
            phase_name = _clean_str(phase_raw.get("name") or phase_raw.get("title")) or f"Phase {phase_index}"
            if "depends_on" in phase_raw:
                phase_deps = parse_depends_on(phase_raw.get("depends_on"))
            else:
                phase_deps = previous_phase
            start_len = len(tasks)

            phase_tasks = phase_raw.get("tasks")
            if isinstance(phase_tasks, list):
                for raw in phase_tasks:
                    if not _append_task(tasks, normalize_task(raw, phase=phase_name, depends_on=phase_deps)):
                        return tasks[:100], f"{name} · phases · {len(tasks[:100])} task(s)"

            if isinstance(phase_raw.get("items"), list) and isinstance(phase_raw.get("stages"), list):
                for task in _pipeline_tasks(
                    phase_raw,
                    phase=phase_name,
                    depends_on=phase_deps,
                    base_index=len(tasks),
                ):
                    if not _append_task(tasks, task):
                        return tasks[:100], f"{name} · phases · {len(tasks[:100])} task(s)"

            previous_phase = list(range(start_len + 1, len(tasks) + 1))
            if len(tasks) >= 100:
                break
        return tasks[:100], f"{name} · phases · {len(tasks[:100])} task(s)"

    if isinstance(args.get("items"), list) and isinstance(args.get("stages"), list):
        tasks = _pipeline_tasks(args)
        return tasks[:100], f"{name} · pipeline · {len(tasks[:100])} task(s)"

    raw_tasks = args.get("tasks")
    if isinstance(raw_tasks, list):
        for raw in raw_tasks:
            if not _append_task(tasks, normalize_task(raw)):
                break
    return tasks[:100], f"{name} · parallel · {len(tasks[:100])} task(s)"