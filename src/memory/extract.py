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

import datetime as _dt
import json
import re

from logger import logger

from .memdir import MEMORY_TYPES, format_manifest, write_memory

_MAX_TRANSCRIPT_CHARS = 24_000
_MAX_ITEMS = 6
_JSON_BLOCK_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def _today() -> str:
    return _dt.date.today().isoformat()


def _build_prompt(transcript: str, manifest: str) -> str:
    types = ", ".join(MEMORY_TYPES)
    existing = manifest.strip() or "(no memories saved yet)"
    return (
        "You maintain the long-term memory of an AI coding agent. "
        "Below is a transcript of the latest session and the list of memory files "
        "that already exist. Decide which DURABLE facts (if any) are worth saving "
        "so future sessions behave better.\n\n"
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
        "- Do NOT duplicate or restate facts already covered by an existing memory "
        "file with the same meaning. If an existing fact changed, emit it again with "
        "the SAME 'name' to overwrite it.\n"
        "- Prefer few, high-signal items. If nothing is worth saving, return [].\n"
        f"- At most {_MAX_ITEMS} items.\n\n"
        "OUTPUT: a JSON array (and nothing else) of objects:\n"
        '  {"name": "short-kebab-name", "type": "<one of the types>", '
        '"scope": "global|project", '
        '"body": "One concise sentence stating the fact. Optionally add **Why:** and '
        '**How to apply:** lines."}\n\n'
        "EXISTING MEMORIES:\n" + existing + "\n\n"
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
        text = text[text.find("\n") + 1:] if "\n" in text else text
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
        if isinstance(item, dict) and item.get("name") and item.get("body"):
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
        logger.debug("memory.extract: import failed: %s", e, exc_info=True)
        return 0

    manifest = format_manifest(working_dir)
    prompt = _build_prompt(transcript, manifest)

    try:
        raw = await api_extract_memory(prompt)
    except Exception as e:
        logger.debug("memory.extract: model call failed: %s", e, exc_info=True)
        return 0

    items = _parse_items(raw)
    if not items:
        logger.info("memory.extract: nothing to save")
        return 0

    today = _today()
    saved = 0
    for item in items[:_MAX_ITEMS]:
        name = str(item.get("name", "")).strip()
        body = str(item.get("body", "")).strip()
        mtype = str(item.get("type", "project")).strip() or "project"
        scope = str(item.get("scope", "project")).strip() or "project"
        if mtype not in MEMORY_TYPES:
            mtype = "project"
        if scope not in ("project", "global"):
            scope = "project"
        if not name or not body:
            continue
        try:
            write_memory(
                name, body, mtype=mtype, today=today,
                working_dir=working_dir, scope=scope,
            )
            saved += 1
        except Exception as e:
            logger.debug("memory.extract: write '%s' failed: %s", name, e, exc_info=True)
    logger.info("memory.extract: saved %d/%d candidate fact(s)", saved, len(items))
    return saved
