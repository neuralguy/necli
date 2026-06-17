"""Project prompts package.

Backward compatibility: re-exports the same names as the old prompts.py module.
SYSTEM_PROMPT remains a single string with {proof} placeholder for callers that
do raw .replace("{proof}", ...) (agent/subagent_api.py, agent/messages.py).

For mode-aware assembly use system_prompt.build_system_prompt(mode=...).
"""

from prompts._base import (
    BASE_HEADER,
    TOOL_FORMAT_BLOCK,
    EXECUTION_MODEL_BLOCK,
    RESPONSE_STRUCTURE_BLOCK,
    TONE_AND_OUTPUT_BLOCK,
    ORCHESTRATION_TRIGGER_BLOCK,
    EFFICIENCY_BLOCK,
    PLANNING_BLOCK,
    FENCED_SYNTAX_BLOCK,
    TOOLS_LIST_BLOCK,
    TOOL_STRATEGY_BLOCK,
    LSP_TOOLS_BLOCK,
    TOOLS_REFERENCE_BLOCK,
    LSP_BLOCK,
    WEB_SEARCH_BLOCK,
    DOCX_BLOCK,
    HARD_CONSTRAINTS_BLOCK,
    AGENT_RULES_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    CRAFT_BLOCK,
    VERIFICATION_GATE_BLOCK,
    SUBAGENTS_BLOCK,
    LANGUAGE_BLOCK,
    tool_format_block_for,
    execution_model_block_for,
    response_structure_block_for,
    planning_block_for,
    docx_block_for,
    hard_constraints_block_for,
    tool_strategy_block_for,
)
from prompts._agent import AGENT_MODE_BLOCK
from prompts._planning import PLANNING_MODE_BLOCK
from prompts._notices import (
    CONTINUE_MESSAGE,
    INTERRUPTED_NOTICE,
    REPROMPT_SUFFIX,
    COMPRESS_PROMPT,
    REFLECT_PROMPT,
    ACTIVE_PLAN_NOTICE,
    CONVERSATION_CONTEXT_HEADER,
    CONVERSATION_CONTEXT_FOOTER,
)
from prompts._settings import (
    THINK_BLOCK,
    TOOL_FORMAT_TEXT_BLOCK,
    THINK_SWITCH_ON,
    THINK_SWITCH_OFF,
    MODE_SWITCH_TO_PLANNING,
    MODE_SWITCH_TO_AGENT,
    think_block_for,
)


def _assemble_default_system_prompt() -> str:
    """Полный prompt для agent-mode со всеми секциями (для legacy-вызовов).

    Содержит плейсхолдер {proof} для замены вызывающей стороной.
    """
    return "\n".join([
        BASE_HEADER,
        "{proof}",
        TOOL_FORMAT_BLOCK,
        EXECUTION_MODEL_BLOCK,
        RESPONSE_STRUCTURE_BLOCK,
        TONE_AND_OUTPUT_BLOCK,
        ORCHESTRATION_TRIGGER_BLOCK,
        EFFICIENCY_BLOCK,
        PLANNING_BLOCK,
        TOOLS_REFERENCE_BLOCK,
        LSP_BLOCK,
        WEB_SEARCH_BLOCK,
        DOCX_BLOCK,
        HARD_CONSTRAINTS_BLOCK,
        AGENT_RULES_BLOCK,
        DELIVERABLE_DISCIPLINE_BLOCK,
        CRAFT_BLOCK,
        VERIFICATION_GATE_BLOCK,
        AGENT_MODE_BLOCK,
        SUBAGENTS_BLOCK,
        LANGUAGE_BLOCK,
    ])


SYSTEM_PROMPT = _assemble_default_system_prompt()


__all__ = [
    "SYSTEM_PROMPT",
    "CONTINUE_MESSAGE",
    "INTERRUPTED_NOTICE",
    "REPROMPT_SUFFIX",
    "ACTIVE_PLAN_NOTICE",
    "CONVERSATION_CONTEXT_HEADER",
    "CONVERSATION_CONTEXT_FOOTER",
    "COMPRESS_PROMPT",
    "REFLECT_PROMPT",
    "THINK_BLOCK",
    "TOOL_FORMAT_TEXT_BLOCK",
    "THINK_SWITCH_ON",
    "THINK_SWITCH_OFF",
    "MODE_SWITCH_TO_PLANNING",
    "MODE_SWITCH_TO_AGENT",
    "BASE_HEADER",
    "TOOL_FORMAT_BLOCK",
    "EXECUTION_MODEL_BLOCK",
    "RESPONSE_STRUCTURE_BLOCK",
    "TONE_AND_OUTPUT_BLOCK",
    "ORCHESTRATION_TRIGGER_BLOCK",
    "EFFICIENCY_BLOCK",
    "PLANNING_BLOCK",
    "FENCED_SYNTAX_BLOCK",
    "TOOLS_LIST_BLOCK",
    "TOOL_STRATEGY_BLOCK",
    "LSP_TOOLS_BLOCK",
    "TOOLS_REFERENCE_BLOCK",
    "LSP_BLOCK",
    "WEB_SEARCH_BLOCK",
    "DOCX_BLOCK",
    "HARD_CONSTRAINTS_BLOCK",
    "AGENT_RULES_BLOCK",
    "DELIVERABLE_DISCIPLINE_BLOCK",
    "CRAFT_BLOCK",
    "VERIFICATION_GATE_BLOCK",
    "AGENT_MODE_BLOCK",
    "PLANNING_MODE_BLOCK",
    "SUBAGENTS_BLOCK",
    "LANGUAGE_BLOCK",
    "tool_format_block_for",
    "execution_model_block_for",
    "response_structure_block_for",
    "planning_block_for",
    "docx_block_for",
    "hard_constraints_block_for",
    "tool_strategy_block_for",
    "think_block_for",
]