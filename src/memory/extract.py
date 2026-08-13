"""Фоновое извлечение долговременной памяти из диалога.

Закрывает то, что раньше было только обещано в докстринге пакета: после
завершения раунда (или каждые N сообщений) лёгкий one-shot вызов модели читает
транскрипт + список уже сохранённых memory-файлов и решает, какие НОВЫЕ
устойчивые факты стоит сохранить (или какие существующие обновить). Сами факты
пишутся через memory.write_memory.

Дизайн как у api_recap: изолированный provider-инстанс активной модели, без
tools, история сессии не трогается, всё в фоне — extraction не блокирует UI и
никогда не роняет основной поток (любая ошибка логируется и проглатывается).

Память НЕ должна дублировать выводимое из кода/git/AGENTS.md — это явно в
промпте. Если модель не нашла ничего нового — возвращается 0 без записи.
"""

from __future__ import annotations

import json
import re

from logger import logger
from memory._time import current_timestamp

from .memdir import (
    MEMORY_TYPES,
    _is_pinned,
    delete_memory,
    format_manifest,
    format_similar_memories,
    memory_path,
    read_memory,
    write_memory,
)

_MAX_TRANSCRIPT_CHARS = 24_000
_MAX_ITEMS = 6
_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _build_prompt(transcript: str, manifest: str, similar: str = "") -> str:
    types = ", ".join(MEMORY_TYPES)
    existing = manifest.strip() or "(no memories saved yet)"
    return (
        "You maintain the long-term memory of an AI coding agent. "
        "Below is a transcript of the latest session and the list of memory files "
        "that already exist. For every candidate fact, choose the right memory "
        "operation so future sessions behave better.\n\n"
        "STRICT RULES:\n"
        "- Save ONLY facts NOT derivable from code, git history, or AGENTS.md.\n"
        "- Good memories: the user's role/preferences/skill level (type=user); how to "
        "approach the work — do/avoid (type=feedback); current goals/incidents/context "
        "not in the code (type=project); external facts/links/values useful later "
        "(type=reference).\n"
        f"- type must be one of: {types}.\n"
        "- scope must be 'global' or 'project'. Use 'global' for facts that are NOT "
        "tied to this one project — who the user is, their general preferences and "
        "working style, universal references — so they apply in EVERY project. Use "
        "'project' for context specific to the current project. When unsure, use "
        "'project'.\n"
        "- Before creating anything, compare it with SIMILAR MEMORIES below.\n"
        "- Choose exactly one action: create, update, merge, delete, or ignore. "
        "create is only for a genuinely new fact; update replaces one existing "
        "record; merge consolidates two or more records; delete removes a fact "
        "made obsolete by the transcript; ignore makes no filesystem change.\n"
        "- Prefer few, high-signal items. If nothing is worth saving, return [].\n"
        f"- At most {_MAX_ITEMS} items.\n\n"
        "OUTPUT: a JSON array and nothing else. Schemas:\n"
        '  create: {"action":"create","name":"new-name","type":"...",'
        '"scope":"global|project","body":"..."}\n'
        '  update: {"action":"update","target":"existing.md","scope":"...",'
        '"type":"...","body":"replacement"}\n'
        '  merge: {"action":"merge","target":"kept-or-new-name",'
        '"sources":["a.md","b.md"],"scope":"...","type":"...","body":"merged"}\n'
        '  delete: {"action":"delete","target":"existing.md","scope":"..."}\n'
        '  ignore: {"action":"ignore","reason":"already known or not durable"}\n\n'
        "EXISTING MEMORY MANIFEST:\n" + existing + "\n\n"
        "SIMILAR MEMORIES (full text):\n" + (similar or "(none)") + "\n\n"
        "--- TRANSCRIPT ---\n" + transcript[-_MAX_TRANSCRIPT_CHARS:] + "\n--- END ---"
    )


def _parse_items(raw: str) -> list[dict]:
    """Достаёт JSON-массив из ответа модели максимально терпимо."""
    text = (raw or "").strip()
    if not text:
        return []
    # Срезаем возможные ```json ... ``` ограждения.
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("\n") + 1 :] if "\n" in text else text
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        m = _JSON_BLOCK_RE.search(text)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if isinstance(item, dict) and (
            item.get("action") == "ignore" or item.get("name") or item.get("target")
        ):
            out.append(item)
    return out


async def extract_memories(transcript: str, working_dir: str | None = None) -> int:
    """Извлекает и сохраняет долговременные факты. Возвращает число записанных.

    Никогда не бросает наружу — при любой ошибке возвращает 0.
    """
    transcript = (transcript or "").strip()
    if not transcript:
        return 0
    try:
        from apis.agent_adapter import api_extract_memory
    except Exception as e:  # pragma: no cover
        logger.debug("memory.extract: import failed: {}", e, exc_info=True)
        return 0

    manifest = format_manifest(working_dir)
    similar = format_similar_memories(transcript[-_MAX_TRANSCRIPT_CHARS:], working_dir)
    prompt = _build_prompt(transcript, manifest, similar)

    try:
        raw = await api_extract_memory(prompt)
    except Exception as e:
        logger.debug("memory.extract: model call failed: {}", e, exc_info=True)
        return 0

    items = _parse_items(raw)
    if not items:
        logger.info("memory.extract: nothing to save")
        return 0

    return apply_memory_decisions(items, working_dir=working_dir)


def apply_memory_decisions(items: list[dict], working_dir: str | None = None) -> int:
    """Validate and apply model-selected memory operations."""
    timestamp = current_timestamp()
    changed = 0
    for item in items[:_MAX_ITEMS]:
        action = str(item.get("action", "create")).strip().lower()
        if action == "ignore":
            continue
        name = str(item.get("name") or item.get("target") or "").strip()
        body = str(item.get("body", "")).strip()
        mtype = str(item.get("type", "project")).strip() or "project"
        scope = str(item.get("scope", "project")).strip() or "project"
        if mtype not in MEMORY_TYPES:
            mtype = "project"
        if scope not in ("project", "global"):
            scope = "project"
        if action not in {"create", "update", "merge", "delete"} or not name:
            continue
        try:
            if action == "delete":
                target = read_memory(memory_path(name, working_dir=working_dir, scope=scope))
                if (
                    target is not None
                    and not _is_pinned(target)
                    and delete_memory(name, working_dir=working_dir, scope=scope) is not None
                ):
                    changed += 1
                continue
            if not body:
                continue
            if action in {"update", "merge"}:
                target = read_memory(memory_path(name, working_dir=working_dir, scope=scope))
                if action == "update" and target is None:
                    continue
            write_memory(
                name,
                body,
                mtype=mtype,
                timestamp=timestamp,
                working_dir=working_dir,
                scope=scope,
            )
            changed += 1
            if action == "merge":
                for source in item.get("sources", []):
                    source_name = str(source).strip()
                    source_path = memory_path(source_name, working_dir=working_dir, scope=scope)
                    source_memory = read_memory(source_path) if source_path.exists() else None
                    if (
                        source_name
                        and source_path != memory_path(name, working_dir=working_dir, scope=scope)
                        and source_memory is not None
                        and not _is_pinned(source_memory)
                    ):
                        delete_memory(source_name, working_dir=working_dir, scope=scope)
        except Exception as e:
            logger.debug("memory.extract: action '{}' failed: {}", name, e, exc_info=True)
    logger.info("memory.extract: applied %d/%d decision(s)", changed, len(items))
    return changed
