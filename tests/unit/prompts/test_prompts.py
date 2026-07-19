"""prompts/* + system_prompt.py — сборка системного промпта из модульных секций."""

import pytest

from prompts import (
    BASE_HEADER,
    DOCX_BLOCK,
    HARD_CONSTRAINTS_BLOCK,
    SYSTEM_PROMPT,
    TOOL_FORMAT_BLOCK,
    _assemble_default_system_prompt,
    docx_block_for,
    execution_model_block_for,
    hard_constraints_block_for,
    planning_block_for,
    response_structure_block_for,
    think_block_for,
    tool_format_block_for,
    tool_strategy_block_for,
)
from prompts._base import (
    HARD_CONSTRAINTS_BLOCK_NATIVE,
    TOOL_FORMAT_BLOCK_NATIVE,
)
from system_prompt import build_system_prompt, build_tool_results


def _build(**kw):
    # Явно фиксируем native_tools/think_enabled, чтобы не зависеть от
    # глобальных настроек сессии в окружении теста.
    kw.setdefault("native_tools", False)
    kw.setdefault("think_enabled", False)
    # По умолчанию активируем все гейтящие скиллы — тесты ниже проверяют
    # СОДЕРЖИМОЕ блоков (web/orchestration/workflows), которые теперь гейтятся
    # скиллами. Сам гейтинг проверяется отдельно в TestSkillGatingInPrompt.
    kw.setdefault("active_skills", {"web", "ssh", "subagents"})
    return build_system_prompt(**kw)

class TestBuildSystemPrompt:
    def test_returns_non_empty_string(self):
        result = _build()
        assert isinstance(result, str)
        assert len(result) > 1000

    def test_includes_base_header_anchor(self):
        assert "You are a Necli - terminal agent." in _build()

    def test_includes_core_section_anchors(self):
        result = _build()
        for anchor in (
            "# Tool call format",
            "# Execution model",
            "# Response structure",
            "# Efficiency",
            "# Planning",
            "# Hard constraints",
            "# Agent rules",
            "# Orchestration decision",
            "# Subagents",
            "# Language",
        ):
            assert anchor in result, anchor

    def test_skill_gating_hides_blocks_when_inactive(self):
        # Без активных скиллов гейтящиеся инструменты и их блоки скрыты.
        bare = build_system_prompt(
            native_tools=False, think_enabled=False, active_skills=set(),
        )
        # web_search скрыт из списка инструментов и блока Web search
        tools_list = bare.split("## Available tools")[1].split("## LSP tools")[0]
        assert "web_search" not in tools_list
        assert "# Web search" not in bare
        # Детальный блок Subagents гейтится скиллом `subagents` и скрыт.
        # (Решение solo-vs-orchestration S3.0 — базовое и присутствует всегда;
        # его гейтинг проверять нельзя, оно не зависит от скиллов.)
        assert "# Subagents" not in bare
        # ssh/subagent отсутствуют в списке инструментов
        assert "ssh" not in tools_list
        assert "subagent" not in tools_list
        # но базовые инструменты на месте
        assert "shell" in tools_list
        assert "skill" in tools_list

    def test_skill_gating_web_exposes_web_block(self):
        p = build_system_prompt(
            native_tools=False, think_enabled=False, active_skills={"web"},
        )
        tools_list = p.split("## Available tools")[1].split("## LSP tools")[0]
        assert "web_search" in tools_list
        assert "# Web search" in p
        # но subagents-блоки всё ещё скрыты
        assert "# Subagents" not in p

    def test_skill_gating_subagents_exposes_orchestration(self):
        p = build_system_prompt(
            native_tools=False, think_enabled=False, active_skills={"subagents"},
        )
        assert "# Orchestration decision" in p
        assert "# Subagents" in p
        # web остаётся скрытым
        assert "# Web search" not in p

    def test_isolate_warns_about_merge_conflicts(self):
        # Изоляция спасает от затирания, но не от merge-конфликтов при правке
        # одного региона. Промт должен это честно сказать, чтобы агент не считал
        # isolate панацеей и предпочитал распределять distinct-файлы.
        # нормализуем пробелы/переносы — фраза может переноситься по строкам
        result = " ".join(_build().split())
        assert "isolation prevents agents OVERWRITING" in result
        assert "DISTINCT files even under isolation" in result

    def test_explicit_user_instruction_overrides_solo_heuristic(self):
        # If the user explicitly asked for a workflow/subagents, the agent must NOT
        # rationalize doing it solo ("this phase is linear"). The override rule must
        # be present and must precede the checklist.
        for mode in (True, False):
            result = _build(native_tools=mode)
            assert "EXPLICIT USER INSTRUCTION OVERRIDES" in result
            override_pos = result.index("EXPLICIT USER INSTRUCTION OVERRIDES")
            checklist_pos = result.index("The checklist is ONLY for deciding")
            assert override_pos < checklist_pos, "override must come before the checklist"

    def test_orchestration_decision_has_triggers_and_anti_triggers(self):
        # Orchestration decision must teach BOTH when to orchestrate and when to stay solo,
        # otherwise the agent either dives in solo or forces workflows onto trivia.
        for mode in (True, False):
            result = _build(native_tools=mode)
            assert "# Orchestration decision" in result
            # trigger toward orchestration
            assert "fan-out" in result
            # anti-trigger: small/linear work stays solo
            assert "SOLO" in result
            # names the trap explicitly (phrase wraps across a line in the source)
            assert '"because it feels' in result
            assert 'faster"' in result

    def test_efficiency_teaches_locate_then_narrow_read(self):
        # The agent must locate (grep/LSP) then read a targeted range — NOT pull
        # whole files into context. The old "Read files WHOLE" rule was the bug.
        for mode in (True, False):
            result = _build(native_tools=mode)
            assert "LOCATE before you read" in result
            assert "TARGETED range" in result
            assert "Read files WHOLE." not in result

    def test_for_subagent_drops_orchestration_and_user_blocks(self):
        # Субагент не может звать subagent и пишет не юзеру, а главному
        # агенту. Эти блоки — повторяющийся мёртвый вес на каждой итерации.
        for native in (True, False):
            main = _build(native_tools=native, for_subagent=False)
            sub = _build(native_tools=native, for_subagent=True)
            # у главного есть, у субагента нет
            assert "# Subagents" in main and "# Subagents" not in sub
            assert "# Orchestration decision" in main
            assert "# Orchestration decision" not in sub
            # субагент заметно короче
            assert len(sub) < len(main)
            # но критичное для работы — сохранено
            assert "# Efficiency" in sub
            assert "# Hard constraints" in sub
            assert "# Deliverable discipline" in sub
            assert "# Tool strategy" in sub

    def test_includes_environment_block(self):
        result = _build(working_dir="/tmp/some-dir")
        assert "# Environment" in result
        assert "/tmp/some-dir" in result
        assert "- Mode: agent" in result

    def test_proof_is_substituted(self):
        result = _build(proof="PROOF-TOKEN-XYZ")
        assert "PROOF-TOKEN-XYZ" in result
        assert "{proof}" not in result

    def test_proof_empty_no_placeholder_left(self):
        assert "{proof}" not in _build()

    def test_fenced_mode_mentions_call_markers(self):
        result = _build(native_tools=False)
        assert ":::call" in result
        assert "call:::" in result
        # text-mode формат вызова присутствует только в fenced
        assert "# Tool call format: text mode" in result

    def test_native_mode_no_fenced_markers(self):
        result = _build(native_tools=True)
        assert "NATIVE function calling" in result
        # Инъецированная persistent-memory может содержать ':::call' как часть
        # фактов — это артефакт окружения, а не дефект промта. Проверяем, что
        # САМ авторский промт (без блока <persistent_memory>) не несёт fenced-
        # маркеров в native-режиме.
        authored = result.split("<persistent_memory>")[0]
        assert ":::call" not in authored
        assert "call:::" not in authored
        assert "# Tool call format: text mode" not in result

    def test_agent_and_planning_mode_differ(self):
        agent = _build(mode="agent")
        planning = _build(mode="planning")
        assert "- Mode: agent" in agent
        assert "- Mode: planning" in planning
        assert "# Planning mode" in planning

    def test_think_off_no_think_block(self):
        assert "# Think format" not in _build(think_enabled=False)

    def test_think_on_appends_think_block(self):
        assert "# Think format" in _build(think_enabled=True)

    def test_native_and_fenced_differ(self):
        assert _build(native_tools=False) != _build(native_tools=True)

    def test_lsp_tools_present_in_both_modes(self):
        # Блок LSP-заметок (S5.2) — fenced-only: в native-режиме LSP-аргументы
        # дублируются JSON-схемами, поэтому секция намеренно опускается.
        assert "## LSP tools" in _build(native_tools=False)
        assert "## LSP tools" not in _build(native_tools=True)

    def test_tool_strategy_present_in_both_modes(self):
        assert "# Tool strategy" in _build(native_tools=False)
        assert "# Tool strategy" in _build(native_tools=True)

class TestDefaultSystemPrompt:
    def test_system_prompt_constant_non_empty(self):
        assert isinstance(SYSTEM_PROMPT, str)
        assert len(SYSTEM_PROMPT) > 1000

    def test_system_prompt_has_proof_placeholder(self):
        # Legacy SYSTEM_PROMPT хранит {proof} для raw .replace() вызовов.
        assert "{proof}" in SYSTEM_PROMPT

    def test_assemble_default_non_empty(self):
        result = _assemble_default_system_prompt()
        assert isinstance(result, str)
        assert len(result) > 1000

    def test_assemble_default_contains_header_and_anchors(self):
        result = _assemble_default_system_prompt()
        assert BASE_HEADER in result
        assert "# Subagents" in result
        assert "{proof}" in result

class TestBlockSelectors:
    @pytest.mark.parametrize(
        "selector",
        [
            tool_format_block_for,
            execution_model_block_for,
            response_structure_block_for,
            planning_block_for,
            tool_strategy_block_for,
            docx_block_for,
            hard_constraints_block_for,
            think_block_for,
        ],
    )
    def test_returns_non_empty_for_both_modes(self, selector):
        assert selector(native_tools=True).strip()
        assert selector(native_tools=False).strip()

    def test_tool_format_differs_by_mode(self):
        assert tool_format_block_for(True) == TOOL_FORMAT_BLOCK_NATIVE
        assert tool_format_block_for(False) == TOOL_FORMAT_BLOCK

    def test_docx_same_both_modes(self):
        assert docx_block_for(True) == DOCX_BLOCK
        assert docx_block_for(False) == DOCX_BLOCK

    def test_hard_constraints_differs_by_mode(self):
        assert hard_constraints_block_for(True) == HARD_CONSTRAINTS_BLOCK_NATIVE
        assert hard_constraints_block_for(False) == HARD_CONSTRAINTS_BLOCK

    def test_think_block_same_both_modes_only_via_function(self):
        # native think-блок не содержит fenced-маркеров, fenced — содержит.
        assert ":::call" not in think_block_for(True)
        assert ":::call" in think_block_for(False)

    def test_native_format_block_has_no_fenced_markers(self):
        assert ":::call" not in TOOL_FORMAT_BLOCK_NATIVE
        assert "call:::" not in TOOL_FORMAT_BLOCK_NATIVE

class TestBuildToolResults:
    def test_empty_list(self):
        out = build_tool_results([])
        assert "<runtime_tool_results" in out
        assert "</runtime_tool_results>" in out

    def test_single_result_header_and_output(self):
        out = build_tool_results([{"command": "ls", "exit_code": 0, "output": "file.txt"}])
        assert 'command="ls"' in out
        assert "file.txt" in out
        assert "exit_code" not in out

    def test_non_zero_exit_in_header(self):
        out = build_tool_results([{"command": "false", "exit_code": 1, "output": ""}])
        assert 'command="false"' in out
        assert 'exit_code="1"' in out

    def test_multiple_results_joined(self):
        out = build_tool_results([
            {"command": "a", "exit_code": 0, "output": "1"},
            {"command": "b", "exit_code": 0, "output": "2"},
        ])
        assert '<result index="1"' in out
        assert '<result index="2"' in out
        assert 'command="a"' in out
        assert 'command="b"' in out

    def test_falls_back_to_name_when_no_command(self):
        out = build_tool_results([{"name": "read_files", "output": "x"}])
        assert "read_files" in out
