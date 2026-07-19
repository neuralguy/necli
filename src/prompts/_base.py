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
from prompts._modalities import (
    DOCX_BLOCK,
    WEB_SEARCH_BLOCK,
    docx_block_for,
)
from prompts._quality import (
    AGENT_RULES_BLOCK,
    CRAFT_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    HARD_CONSTRAINTS_BLOCK,
    HARD_CONSTRAINTS_BLOCK_NATIVE,
    VERIFICATION_GATE_BLOCK,
    hard_constraints_block_for,
)
from prompts._subagents import SUBAGENTS_BLOCK
from prompts._tooling import (
    FENCED_SYNTAX_BLOCK,
    LSP_BLOCK,
    LSP_TOOLS_BLOCK,
    TOOL_FORMAT_BLOCK,
    TOOL_FORMAT_BLOCK_NATIVE,
    TOOL_STRATEGY_BLOCK,
    TOOL_STRATEGY_BLOCK_NATIVE,
    TOOLS_LIST_BLOCK,
    TOOLS_REFERENCE_BLOCK,
    tool_format_block_for,
    tool_strategy_block_for,
)
from prompts._workflow import (
    EFFICIENCY_BLOCK,
    ORCHESTRATION_TRIGGER_BLOCK,
    PLANNING_BLOCK,
    PLANNING_BLOCK_NATIVE,
    planning_block_for,
)

__all__ = [
    "AGENT_RULES_BLOCK",
    "BASE_HEADER",
    "CRAFT_BLOCK",
    "DELIVERABLE_DISCIPLINE_BLOCK",
    "DOCX_BLOCK",
    "EFFICIENCY_BLOCK",
    "EXECUTION_MODEL_BLOCK",
    "EXECUTION_MODEL_BLOCK_NATIVE",
    "FENCED_SYNTAX_BLOCK",
    "HARD_CONSTRAINTS_BLOCK",
    "HARD_CONSTRAINTS_BLOCK_NATIVE",
    "LANGUAGE_BLOCK",
    "LSP_BLOCK",
    "LSP_TOOLS_BLOCK",
    "ORCHESTRATION_TRIGGER_BLOCK",
    "PLANNING_BLOCK",
    "PLANNING_BLOCK_NATIVE",
    "RESPONSE_STRUCTURE_BLOCK",
    "RESPONSE_STRUCTURE_BLOCK_NATIVE",
    "SUBAGENTS_BLOCK",
    "TONE_AND_OUTPUT_BLOCK",
    "TOOLS_LIST_BLOCK",
    "TOOLS_REFERENCE_BLOCK",
    "TOOL_FORMAT_BLOCK",
    "TOOL_FORMAT_BLOCK_NATIVE",
    "TOOL_STRATEGY_BLOCK",
    "TOOL_STRATEGY_BLOCK_NATIVE",
    "VERIFICATION_GATE_BLOCK",
    "WEB_SEARCH_BLOCK",
    "docx_block_for",
    "execution_model_block_for",
    "hard_constraints_block_for",
    "planning_block_for",
    "response_structure_block_for",
    "tool_format_block_for",
    "tool_strategy_block_for",
]
