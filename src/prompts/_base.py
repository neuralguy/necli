"""Compatibility re-exports for legacy imports.

Prompt sections now live in focused modules, mirroring the section-oriented
layout used by claude-code-main. Keep this module thin so old imports from
`prompts._base` continue to work.
"""

from prompts._core import (
    BASE_HEADER,
    EXECUTION_MODEL_BLOCK,
    EXECUTION_MODEL_BLOCK_NATIVE,
    LANGUAGE_BLOCK,
    execution_model_block_for,
)
from prompts._interaction import (
    RESPONSE_STRUCTURE_BLOCK,
    RESPONSE_STRUCTURE_BLOCK_NATIVE,
    TONE_AND_OUTPUT_BLOCK,
    response_structure_block_for,
)
from prompts._workflow import (
    ORCHESTRATION_TRIGGER_BLOCK,
    EFFICIENCY_BLOCK,
    PLANNING_BLOCK,
    PLANNING_BLOCK_NATIVE,
    planning_block_for,
)
from prompts._tooling import (
    TOOL_FORMAT_BLOCK,
    TOOL_FORMAT_BLOCK_NATIVE,
    FENCED_SYNTAX_BLOCK,
    TOOLS_LIST_BLOCK,
    LSP_TOOLS_BLOCK,
    TOOL_STRATEGY_BLOCK,
    TOOL_STRATEGY_BLOCK_NATIVE,
    TOOLS_REFERENCE_BLOCK,
    LSP_BLOCK,
    tool_format_block_for,
    tool_strategy_block_for,
)
from prompts._modalities import (
    WEB_SEARCH_BLOCK,
    DOCX_BLOCK,
    docx_block_for,
)
from prompts._quality import (
    HARD_CONSTRAINTS_BLOCK,
    HARD_CONSTRAINTS_BLOCK_NATIVE,
    AGENT_RULES_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    CRAFT_BLOCK,
    VERIFICATION_GATE_BLOCK,
    hard_constraints_block_for,
)
from prompts._subagents import SUBAGENTS_BLOCK

__all__ = [
    "BASE_HEADER",
    "EXECUTION_MODEL_BLOCK",
    "EXECUTION_MODEL_BLOCK_NATIVE",
    "LANGUAGE_BLOCK",
    "execution_model_block_for",
    "RESPONSE_STRUCTURE_BLOCK",
    "RESPONSE_STRUCTURE_BLOCK_NATIVE",
    "TONE_AND_OUTPUT_BLOCK",
    "response_structure_block_for",
    "ORCHESTRATION_TRIGGER_BLOCK",
    "EFFICIENCY_BLOCK",
    "PLANNING_BLOCK",
    "PLANNING_BLOCK_NATIVE",
    "planning_block_for",
    "TOOL_FORMAT_BLOCK",
    "TOOL_FORMAT_BLOCK_NATIVE",
    "FENCED_SYNTAX_BLOCK",
    "TOOLS_LIST_BLOCK",
    "LSP_TOOLS_BLOCK",
    "TOOL_STRATEGY_BLOCK",
    "TOOL_STRATEGY_BLOCK_NATIVE",
    "TOOLS_REFERENCE_BLOCK",
    "LSP_BLOCK",
    "tool_format_block_for",
    "tool_strategy_block_for",
    "WEB_SEARCH_BLOCK",
    "DOCX_BLOCK",
    "docx_block_for",
    "HARD_CONSTRAINTS_BLOCK",
    "HARD_CONSTRAINTS_BLOCK_NATIVE",
    "AGENT_RULES_BLOCK",
    "DELIVERABLE_DISCIPLINE_BLOCK",
    "CRAFT_BLOCK",
    "VERIFICATION_GATE_BLOCK",
    "hard_constraints_block_for",
    "SUBAGENTS_BLOCK",
]