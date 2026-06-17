"""Реестр связей «скилл → инструменты» и определение активных скиллов.

Идея: некоторые инструменты ГЕЙТЯТСЯ скиллами — они не видны модели (ни в
native-схемах, ни в fenced-списке), пока соответствующий скилл не загружен
через инструмент `skill`. «Активность» скилла НЕ хранится в глобальном
состоянии — она ВЫЧИСЛЯЕТСЯ из истории сообщений: скилл активен, если его
последняя загрузка попала в окно последних `ACTIVE_WINDOW_ROUNDS` раундов
(раунд = одно сообщение пользователя).

Это даёт единый источник правды для трёх механизмов:
  1. гейтинг инструментов (tool_schemas + system_prompt);
  2. pruning текста SKILL.md (_context_pruner) — тем же порогом;
  3. авто-деактивация: за окном скилл «забывается», инструменты снова скрыты.

ВАЖНО: расчёт работает над apis.messages (HumanMessage/AIMessage/ToolMessage),
а не над session.Message — потому что и pruner, и сборка промпта/схем работают
именно с этим LLM-форматом истории.
"""

from __future__ import annotations

import re

# Скилл → набор инструментов, которые он «открывает».
# Единственное место правды. Добавляешь скилл с тулами — правишь здесь.
SKILL_TOOLS: dict[str, set[str]] = {
    "web": {"web_search", "image_search"},
    "ssh": {"ssh"},
    "subagents": {"subagent"},
}

# Все инструменты, которые вообще гейтятся скиллами.
GATED_TOOLS: set[str] = {tool for tools in SKILL_TOOLS.values() for tool in tools}

# Окно активности скилла в раундах (раунд = сообщение пользователя). Должно
# совпадать с порогом pruning в _context_pruner._SKILL_EVICT_ROUNDS, чтобы
# «забывание» текста скилла и скрытие его инструментов происходили синхронно.
ACTIVE_WINDOW_ROUNDS = 5

# Первая строка text-mode skill-результата: "$ skill <name>".
_SKILL_CMD_RE = re.compile(r"^\$ skill\s+(\S+)")


def is_real_user_message(msg) -> bool:
    """True для НАСТОЯЩЕЙ реплики юзера (с клавиатуры).

    Служебные HumanMessage, которые агент сам добавляет между репликами юзера
    в native-режиме (extras-план/статистика, multimodal-картинки инструментов,
    гибрид tool-результаты), помечены additional_kwargs={"synthetic": True} и
    НЕ должны считаться за раунд — иначе окно скилла схлопывается за несколько
    tool-вызовов, хотя юзер ещё ничего нового не написал.
    """
    from apis.messages import HumanMessage

    if not isinstance(msg, HumanMessage):
        return False
    return not (getattr(msg, "additional_kwargs", None) or {}).get("synthetic")


def tools_for_skill(name: str) -> set[str]:
    """Инструменты, открываемые скиллом (пустое множество, если скилл негейтящий)."""
    return set(SKILL_TOOLS.get(name, ()))


def skill_for_tool(tool: str) -> str | None:
    """Скилл, гейтящий данный инструмент, либо None если инструмент не гейтится."""
    for skill, tools in SKILL_TOOLS.items():
        if tool in tools:
            return skill
    return None


def visible_gated_tools(active_skills: set[str] | None) -> set[str]:
    """Какие из гейтящихся инструментов доступны при данном наборе активных скиллов."""
    active = active_skills or set()
    visible: set[str] = set()
    for skill in active:
        visible |= SKILL_TOOLS.get(skill, set())
    return visible


def is_tool_gated_out(tool: str, active_skills: set[str] | None) -> bool:
    """True, если инструмент гейтится скиллом и сейчас НЕ должен быть виден."""
    if tool not in GATED_TOOLS:
        return False
    return tool not in visible_gated_tools(active_skills)


def _skill_loads_by_round(messages: list) -> dict[str, int]:
    """Карта «имя скилла → НОМЕР последнего раунда (1-based), где он был загружен».

    Детектит загрузку скилла в обоих режимах:
      - native: ToolMessage(name="skill"); имя скилла берём из соседнего
        AIMessage.tool_calls (args.name) по совпадению tool_call_id, либо из
        первой строки контента "$ skill <name>";
      - text: HumanMessage, содержащий блок с первой строкой "$ skill <name>"
        (несколько tool-результатов склеены через "\\n---\\n").
    """
    # Импортируем лениво, чтобы registry не тянул apis на этапе импорта пакета.
    from apis.messages import AIMessage, HumanMessage, ToolMessage

    # tool_call_id → имя скилла (из native-вызовов skill).
    skill_call_names: dict[str, str] = {}
    for msg in messages:
        if isinstance(msg, AIMessage):
            for tc in getattr(msg, "tool_calls", []) or []:
                if (tc.get("name") or "") != "skill":
                    continue
                tc_id = tc.get("id") or ""
                args = tc.get("args") or {}
                name = ""
                if isinstance(args, dict):
                    name = str(args.get("name") or "").strip()
                if tc_id and name:
                    skill_call_names[tc_id] = name

    loads: dict[str, int] = {}
    round_idx = 0

    def _note(name: str) -> None:
        if name:
            loads[name] = round_idx  # последний раунд загрузки

    for msg in messages:
        if isinstance(msg, HumanMessage):
            if not is_real_user_message(msg):
                # Служебное HumanMessage (extras/картинки/гибрид) — не раунд.
                continue
            round_idx += 1
            text = msg.content if isinstance(msg.content, str) else ""
            if "$ skill " in text:
                for block in text.split("\n---\n"):
                    first_line = block.partition("\n")[0]
                    m = _SKILL_CMD_RE.match(first_line)
                    if m:
                        _note(m.group(1).strip())
        elif isinstance(msg, ToolMessage):
            if (getattr(msg, "name", "") or "") != "skill":
                continue
            tc_id = getattr(msg, "tool_call_id", "") or ""
            name = skill_call_names.get(tc_id, "")
            if not name:
                content = msg.content if isinstance(msg.content, str) else ""
                m = _SKILL_CMD_RE.match(content.partition("\n")[0])
                if m:
                    name = m.group(1).strip()
            _note(name)

    return loads


def active_skills_from_messages(
    messages: list, window: int = ACTIVE_WINDOW_ROUNDS,
) -> set[str]:
    """Множество скиллов, активных СЕЙЧАС (загружены в пределах окна раундов).

    current_round = число HumanMessage. Скилл активен, если последний раунд
    его загрузки строго новее, чем (current_round - window). Так последняя
    загрузка остаётся активной ровно `window` раундов, считая раунд загрузки.
    """
    if not messages:
        return set()

    current_round = sum(1 for m in messages if is_real_user_message(m))
    if current_round <= 0:
        return set()

    loads = _skill_loads_by_round(messages)
    threshold = current_round - window
    return {name for name, r in loads.items() if r > threshold}
