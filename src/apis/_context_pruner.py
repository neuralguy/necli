"""Pruning старых read-результатов из истории перед отправкой в API.

Цель — экономия токенов без потери актуального контекста. Pruner работает
в ДВУХ архитектурах сразу:

  1. text-mode: tool_results вшиты в user-сообщения блоками
     '$ read_files <path>\\n<content>', разделёнными '\\n---\\n'.
  2. native: каждый tool_result — отдельный ToolMessage(name=..., content=...)
     после AIMessage(tool_calls=...). Команда/путь восстанавливаются из
     соответствующего tool_call в предыдущем AIMessage.

Три триггера вытеснения read-контента (от свежего к старому контексту):
  A. modified-later: файл перезаписан (write/patch/create) в более позднем
     раунде — старое чтение устарело.
  B. superseded: тот же путь прочитан ПОЗЖЕ — ранние копии лишние (дедуп).
  C. age+size: крупное чтение (> _MIN_EVICT_CHARS) старше _KEEP_RECENT_ROUNDS
     раундов — вытесняем, даже если файл не менялся.
  D. hard-cap: ЛЮБОЕ чтение старше _HARD_EVICT_ROUNDS раундов — вытесняем
     независимо от размера (древний контекст обнуляется целиком).

Последний раунд (свежие tool_results) НЕ трогается никогда.
Оригинал session.messages НЕ мутируется — pruner возвращает копию.
"""

from __future__ import annotations

import re
from typing import Any

from apis.messages import HumanMessage, AIMessage, ToolMessage
from logger import logger


def _is_real_user(msg: Any) -> bool:
    """True только для НАСТОЯЩЕЙ реплики юзера (не synthetic).

    Служебные HumanMessage (extras/картинки/гибрид tool-результаты), которые
    агент сам вставляет между репликами юзера в native-режиме, помечены
    additional_kwargs={"synthetic": True} и не должны считаться за раунд —
    иначе окна вытеснения схлопываются раньше, чем юзер напишет 5 сообщений.
    """
    if not isinstance(msg, HumanMessage):
        return False
    return not (getattr(msg, "additional_kwargs", None) or {}).get("synthetic")

_BLOCK_SEP = "\n---\n"
_READ_CMD_RE = re.compile(r"^\$ (read_files?|read_file)\s+(.+)$")
_WRITE_CMD_RE = re.compile(r"^\$ (write_file|patch_file|create_file)\s+(.+)$")

_READ_NAMES = {"read_files", "read_file"}
_WRITE_NAMES = {"write_file", "patch_file", "create_file"}
# Инструменты, чей ВЫВОД (не файл) вытесняется по возрасту: снимок состояния,
# перечитать «тем же» нельзя — только повторный вызов. Применяем C (крупный
# рано) и D (любой через _HARD_EVICT_ROUNDS), без A/B (нет пути/дедупа).
_TOOL_EVICT_NAMES = {
    "shell", "grep_files", "find_files", "tree", "ls", "web_search",
    "lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics",
}
_TOOL_CMD_RE = re.compile(
    r"^\$ (shell|grep_files|find_files|tree|ls|web_search|"
    r"lsp_definition|lsp_references|lsp_hover|lsp_diagnostics)\b(.*)$"
)

# Скилл-результаты (вывод инструмента `skill` — тело SKILL.md). Вытесняются по
# собственному порогу: текст инструкции нужен только пока скилл «активен».
# _SKILL_EVICT_ROUNDS должен совпадать с skills.registry.ACTIVE_WINDOW_ROUNDS —
# тогда исчезновение текста и скрытие инструментов скилла происходят синхронно.
_SKILL_NAMES = {"skill"}
_SKILL_CMD_RE = re.compile(r"^\$ skill\b(.*)$")
_SKILL_EVICT_ROUNDS = 5

# Read старше этого числа раундов и крупнее _MIN_EVICT_CHARS — кандидат на
# age-eviction (триггер C). Свежие раунды остаются дословно.
_KEEP_RECENT_ROUNDS = 4
_MIN_EVICT_CHARS = 2000
# Жёсткий потолок (триггер D): чтение старше этого числа раундов вытесняется
# независимо от размера — даже мелкие.
_HARD_EVICT_ROUNDS = 10

_EVICT_MARKER = "[content evicted to save tokens"


def _extract_paths_from_cmd_tail(tail: str) -> list[str]:
    """Из строки команды read_files выдёргивает пути.

    Форматы:
      read_files agent/stream.py
      read_files ['agent/stream.py', 'tools/x.py']
      read_files agent/stream.py:120-200      (с lines)
      patch_file agent/x.py
    Берём то что до первого пробела/двоеточия, либо распарсиваем repr-list.
    """
    tail = tail.strip()
    if not tail:
        return []
    if tail.startswith("[") and tail.endswith("]"):
        return re.findall(r"['\"]([^'\"]+)['\"]", tail)
    first = tail.split()[0]
    if ":" in first:
        first = first.split(":", 1)[0]
    return [first] if first else []


def _paths_from_args(args: Any) -> list[str]:
    """Достаёт пути из native tool_call args (read_files/write_file/...)."""
    if not isinstance(args, dict):
        return []
    p = args.get("path")
    if isinstance(p, str) and p:
        return [p]
    if isinstance(p, list):
        return [str(x) for x in p if x]
    return []


def _get_text_content(msg: Any) -> str | None:
    """Возвращает str-контент сообщения если он строковый. Multimodal list пропускаем."""
    c = getattr(msg, "content", None)
    if isinstance(c, str):
        return c
    return None


def _set_text_content(msg: Any, new_text: str) -> Any:
    """Создаёт копию сообщения с заменённым text-контентом."""
    cls = type(msg)
    kwargs: dict = {"content": new_text}
    add_kw = getattr(msg, "additional_kwargs", None)
    if add_kw:
        kwargs["additional_kwargs"] = dict(add_kw)
    if isinstance(msg, ToolMessage):
        kwargs["tool_call_id"] = getattr(msg, "tool_call_id", "")
        kwargs["name"] = getattr(msg, "name", "tool")
    elif isinstance(msg, AIMessage):
        tc = getattr(msg, "tool_calls", None)
        if tc:
            kwargs["tool_calls"] = list(tc)
    return cls(**kwargs)


def _placeholder(cmd: str, cmd_tail: str, paths: list[str], reason: str) -> str:
    path_disp = ", ".join(paths) if paths else cmd_tail
    return (
        f"$ {cmd} {cmd_tail}\n"
        f"{_EVICT_MARKER} — {reason}. "
        f"Re-read with read_files if needed: {path_disp}]"
    )


def _scan_round_writes(messages: list) -> dict[str, int]:
    """Для каждого file path → максимальный round его модификации (write/patch/create).

    Round = индекс user-сообщения (1-based). Сканирует ОБА формата:
      - text-mode: '$ write_file ...' блоки внутри HumanMessage;
      - native: AIMessage.tool_calls с именами write/patch/create.
    """
    writes: dict[str, int] = {}
    round_idx = 0
    for msg in messages:
        if _is_real_user(msg):
            round_idx += 1
            text = _get_text_content(msg)
            if text:
                for block in text.split(_BLOCK_SEP):
                    first_line = block.split("\n", 1)[0]
                    m = _WRITE_CMD_RE.match(first_line)
                    if not m:
                        continue
                    for p in _extract_paths_from_cmd_tail(m.group(2)):
                        if writes.get(p, 0) < round_idx:
                            writes[p] = round_idx
        elif isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                if (tc.get("name") or "") in _WRITE_NAMES:
                    for p in _paths_from_args(tc.get("args")):
                        if writes.get(p, 0) < round_idx:
                            writes[p] = round_idx
    return writes


def _scan_read_paths(messages: list) -> dict[str, int]:
    """Для каждого file path → максимальный round, в котором его ЧИТАЛИ.

    Нужно для дедупа (триггер B): чтение пути в раунде < последнего чтения
    того же пути — кандидат на вытеснение.
    """
    reads: dict[str, int] = {}
    round_idx = 0

    def _bump(paths: list[str]) -> None:
        for p in paths:
            if reads.get(p, 0) < round_idx:
                reads[p] = round_idx

    for msg in messages:
        if _is_real_user(msg):
            round_idx += 1
            text = _get_text_content(msg)
            if text:
                for block in text.split(_BLOCK_SEP):
                    first_line = block.split("\n", 1)[0]
                    m = _READ_CMD_RE.match(first_line)
                    if m:
                        _bump(_extract_paths_from_cmd_tail(m.group(2)))
        elif isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                if (tc.get("name") or "") in _READ_NAMES:
                    _bump(_paths_from_args(tc.get("args")))
    return reads


def _should_evict(
    paths: list[str],
    block_round: int,
    current_round: int,
    block_chars: int,
    write_rounds: dict[str, int],
    read_rounds: dict[str, int],
) -> str | None:
    """Возвращает причину eviction или None. Применяет триггеры A/B/C/D."""
    if not paths:
        return None
    # A. файл изменён в более позднем раунде
    if any(write_rounds.get(p, 0) > block_round for p in paths):
        return "file modified in later round"
    # B. тот же путь прочитан позже — ранняя копия лишняя (дедуп)
    if any(read_rounds.get(p, 0) > block_round for p in paths):
        return "superseded by a later read of the same file"
    age = current_round - block_round
    # C. старое крупное чтение
    if age >= _KEEP_RECENT_ROUNDS and block_chars >= _MIN_EVICT_CHARS:
        return f"stale read ({age} rounds old)"
    # D. жёсткий потолок — любое чтение старше _HARD_EVICT_ROUNDS
    if age >= _HARD_EVICT_ROUNDS:
        return f"very old read ({age} rounds old)"
    return None


def _should_evict_tool(block_round: int, current_round: int, block_chars: int) -> str | None:
    """Возраст-вытеснение вывода инструмента: C (крупный рано) + D (любой через потолок)."""
    age = current_round - block_round
    if age >= _KEEP_RECENT_ROUNDS and block_chars >= _MIN_EVICT_CHARS:
        return f"stale tool output ({age} rounds old)"
    if age >= _HARD_EVICT_ROUNDS:
        return f"very old tool output ({age} rounds old)"
    return None


def _should_evict_skill(block_round: int, current_round: int) -> str | None:
    """Вытеснение skill-инструкции: после _SKILL_EVICT_ROUNDS раундов (любой размер).

    Совпадает с окном активности скилла — за порогом скилл «забывается», его
    инструменты снова скрыты, держать текст SKILL.md в контексте незачем.
    """
    age = current_round - block_round
    if age >= _SKILL_EVICT_ROUNDS:
        return f"skill instructions expired ({age} rounds old)"
    return None


def _skill_placeholder(cmd_line: str, reason: str) -> str:
    return (
        f"{cmd_line}\n"
        f"{_EVICT_MARKER} — {reason}. Reload the skill (skill tool) if you still need it.]"
    )

def _tool_placeholder(cmd_line: str, reason: str) -> str:
    return (
        f"{cmd_line}\n"
        f"{_EVICT_MARKER} — {reason}. Re-run the tool if you need this output.]"
    )

def _prune_user_text(
    text: str,
    user_round: int,
    current_round: int,
    write_rounds: dict[str, int],
    read_rounds: dict[str, int],
    evicted_paths: set[str],
) -> str:
    """Заменяет старые read-блоки на плейсхолдер. Возвращает новый text.

    Пути вытесненных блоков добавляются в evicted_paths (для сброса read-cache).
    """
    blocks = text.split(_BLOCK_SEP)
    changed = False
    new_blocks: list[str] = []
    for block in blocks:
        first_line, _, _ = block.partition("\n")
        if _EVICT_MARKER in block:
            new_blocks.append(block)
            continue
        m = _READ_CMD_RE.match(first_line)
        if m:
            cmd_tail = m.group(2)
            paths = _extract_paths_from_cmd_tail(cmd_tail)
            reason = _should_evict(
                paths, user_round, current_round, len(block),
                write_rounds, read_rounds,
            )
            if reason is None:
                new_blocks.append(block)
                continue
            new_blocks.append(_placeholder(m.group(1), cmd_tail, paths, reason))
            evicted_paths.update(paths)
            changed = True
            continue
        mt = _TOOL_CMD_RE.match(first_line)
        if mt:
            reason = _should_evict_tool(user_round, current_round, len(block))
            if reason is None:
                new_blocks.append(block)
                continue
            new_blocks.append(_tool_placeholder(first_line, reason))
            changed = True
            continue
        msk = _SKILL_CMD_RE.match(first_line)
        if msk:
            reason = _should_evict_skill(user_round, current_round)
            if reason is None:
                new_blocks.append(block)
                continue
            new_blocks.append(_skill_placeholder(first_line, reason))
            changed = True
            continue
        new_blocks.append(block)

    if not changed:
        return text
    return _BLOCK_SEP.join(new_blocks)


def _prune_native(
    messages: list,
    current_round: int,
    write_rounds: dict[str, int],
    read_rounds: dict[str, int],
    evicted_paths: set[str],
) -> tuple[list, int, int]:
    """Native-проход: вытесняет старые read-ToolMessage.

    Путь read-результата восстанавливается из tool_call_id в предыдущем
    AIMessage (по имени read_files/read_file и совпадению id). Возвращает
    (новый список, pruned_blocks, saved_chars).
    """
    # Карта tool_call_id → (round, paths) для read-вызовов + round для tool-вызовов.
    call_meta: dict[str, tuple[int, list[str]]] = {}
    tool_meta: dict[str, int] = {}
    skill_meta: dict[str, int] = {}
    round_idx = 0
    for msg in messages:
        if _is_real_user(msg):
            round_idx += 1
        elif isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                name = tc.get("name") or ""
                tc_id = tc.get("id") or ""
                if name in _READ_NAMES and tc_id:
                    call_meta[tc_id] = (round_idx, _paths_from_args(tc.get("args")))
                elif name in _TOOL_EVICT_NAMES and tc_id:
                    tool_meta[tc_id] = round_idx
                elif name in _SKILL_NAMES and tc_id:
                    skill_meta[tc_id] = round_idx

    result: list = []
    pruned = 0
    saved = 0
    for msg in messages:
        if not isinstance(msg, ToolMessage):
            result.append(msg)
            continue
        msg_name = getattr(msg, "name", "") or ""
        tc_id = getattr(msg, "tool_call_id", "") or ""
        content = _get_text_content(msg)
        if content is None or _EVICT_MARKER in content:
            result.append(msg)
            continue
        # Вывод тяжёлых инструментов — вытеснение по возрасту (C/D).
        if msg_name in _TOOL_EVICT_NAMES:
            t_round = tool_meta.get(tc_id)
            if t_round is None or t_round >= current_round:
                result.append(msg)
                continue
            reason = _should_evict_tool(t_round, current_round, len(content))
            if reason is None:
                result.append(msg)
                continue
            new_content = (
                f"{_EVICT_MARKER} — {reason}. Re-run the tool if you need this output.]"
            )
            saved += len(content) - len(new_content)
            pruned += 1
            result.append(_set_text_content(msg, new_content))
            continue
        # Skill-инструкция — вытеснение по собственному порогу (окно активности).
        if msg_name in _SKILL_NAMES:
            s_round = skill_meta.get(tc_id)
            if s_round is None or s_round >= current_round:
                result.append(msg)
                continue
            reason = _should_evict_skill(s_round, current_round)
            if reason is None:
                result.append(msg)
                continue
            new_content = (
                f"{_EVICT_MARKER} — {reason}. "
                f"Reload the skill (skill tool) if you still need it.]"
            )
            saved += len(content) - len(new_content)
            pruned += 1
            result.append(_set_text_content(msg, new_content))
            continue
        if msg_name not in _READ_NAMES:
            result.append(msg)
            continue
        meta = call_meta.get(tc_id)
        if meta is None:
            result.append(msg)
            continue
        block_round, paths = meta
        # Свежий раунд не трогаем.
        if block_round >= current_round:
            result.append(msg)
            continue
        reason = _should_evict(
            paths, block_round, current_round, len(content),
            write_rounds, read_rounds,
        )
        if reason is None:
            result.append(msg)
            continue
        path_disp = ", ".join(paths) if paths else "(unknown)"
        new_content = (
            f"{_EVICT_MARKER} — {reason}. "
            f"Re-read with read_files if needed: {path_disp}]"
        )
        saved += len(content) - len(new_content)
        pruned += 1
        evicted_paths.update(paths)
        result.append(_set_text_content(msg, new_content))
    return result, pruned, saved


def prune_messages(messages: list) -> tuple[list, dict]:
    """Возвращает (новый список, stats). Оригинал не модифицируется.

    stats: {"pruned_blocks": N, "saved_chars": M}
    """
    if not messages:
        return list(messages), {"pruned_blocks": 0, "saved_chars": 0}

    current_round = sum(1 for m in messages if _is_real_user(m))
    if current_round <= 1:
        return list(messages), {"pruned_blocks": 0, "saved_chars": 0}

    write_rounds = _scan_round_writes(messages)
    read_rounds = _scan_read_paths(messages)
    evicted_paths: set[str] = set()

    # ── Pass 1: text-mode read-блоки внутри HumanMessage ──
    result: list = []
    round_idx = 0
    pruned_blocks = 0
    saved = 0
    for msg in messages:
        if _is_real_user(msg):
            round_idx += 1
            if round_idx == current_round:
                result.append(msg)
                continue
            text = _get_text_content(msg)
            if text is None:
                result.append(msg)
                continue
            new_text = _prune_user_text(
                text, round_idx, current_round, write_rounds, read_rounds,
                evicted_paths,
            )
            if new_text != text:
                pruned_blocks += new_text.count(_EVICT_MARKER) - text.count(_EVICT_MARKER)
                saved += len(text) - len(new_text)
                result.append(_set_text_content(msg, new_text))
            else:
                result.append(msg)
        else:
            result.append(msg)

    # ── Pass 2: native read-ToolMessage ──
    result, native_pruned, native_saved = _prune_native(
        result, current_round, write_rounds, read_rounds, evicted_paths,
    )
    pruned_blocks += native_pruned
    saved += native_saved

    # Сброс read-cache для вытесненных путей: тело удалено из истории, поэтому
    # повторный read_files НЕ должен отвечать NOT CHANGED (иначе модель
    # останется без контента — и в истории плейсхолдер, и кэш молчит).
    if evicted_paths:
        try:
            from tools.file_ops.read import invalidate_read_cache

            for p in evicted_paths:
                invalidate_read_cache(p)
        except Exception:
            logger.debug("pruner: read-cache invalidation failed", exc_info=True)

    return result, {"pruned_blocks": pruned_blocks, "saved_chars": saved}