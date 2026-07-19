"""Project prompts package.

Backward compatibility: re-exports the same names as the old prompts.py module.
SYSTEM_PROMPT remains a single string with {proof} placeholder for callers that
do raw .replace("{proof}", ...) (agent/subagent_api.py, agent/messages.py).

For mode-aware assembly use system_prompt.build_system_prompt(mode=...).
"""

from prompts._agent import AGENT_MODE_BLOCK
from prompts._autonomous import AUTONOMOUS_MODE_BLOCK
from prompts._core import (
    BASE_HEADER,
    EXECUTION_MODEL_BLOCK,
    LANGUAGE_BLOCK,
    execution_model_block_for,
)
from prompts._interaction import (
    RESPONSE_STRUCTURE_BLOCK,
    TONE_AND_OUTPUT_BLOCK,
    response_structure_block_for,
)
from prompts._modalities import (
    DOCX_BLOCK,
    WEB_SEARCH_BLOCK,
    docx_block_for,
)
from prompts._notices import (
    ACTIVE_PLAN_NOTICE,
    COMPRESS_PROMPT,
    CONTINUE_MESSAGE,
    CONVERSATION_CONTEXT_FOOTER,
    CONVERSATION_CONTEXT_HEADER,
    INTERRUPTED_NOTICE,
    REFLECT_PROMPT,
)
from prompts._planning import PLANNING_MODE_BLOCK
from prompts._quality import (
    AGENT_RULES_BLOCK,
    CRAFT_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    HARD_CONSTRAINTS_BLOCK,
    VERIFICATION_GATE_BLOCK,
    hard_constraints_block_for,
)
from prompts._settings import (
    MODE_SWITCH_TO_AGENT,
    MODE_SWITCH_TO_AUTONOMOUS,
    MODE_SWITCH_TO_PLANNING,
    THINK_BLOCK,
    THINK_SWITCH_OFF,
    THINK_SWITCH_ON,
    TOOL_FORMAT_TEXT_BLOCK,
    think_block_for,
)
from prompts._subagents import SUBAGENTS_BLOCK
from prompts._tooling import (
    FENCED_SYNTAX_BLOCK,
    LSP_BLOCK,
    LSP_TOOLS_BLOCK,
    TOOL_FORMAT_BLOCK,
    TOOL_STRATEGY_BLOCK,
    TOOLS_LIST_BLOCK,
    TOOLS_REFERENCE_BLOCK,
    tool_format_block_for,
    tool_strategy_block_for,
)
from prompts._workflow import (
    EFFICIENCY_BLOCK,
    ORCHESTRATION_TRIGGER_BLOCK,
    PLANNING_BLOCK,
    planning_block_for,
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
    "ACTIVE_PLAN_NOTICE",
    "AGENT_MODE_BLOCK",
    "AGENT_RULES_BLOCK",
    "AUTONOMOUS_MODE_BLOCK",
    "BASE_HEADER",
    "COMPRESS_PROMPT",
    "CONTINUE_MESSAGE",
    "CONVERSATION_CONTEXT_FOOTER",
    "CONVERSATION_CONTEXT_HEADER",
    "CRAFT_BLOCK",
    "DELIVERABLE_DISCIPLINE_BLOCK",
    "DOCX_BLOCK",
    "EFFICIENCY_BLOCK",
    "EXECUTION_MODEL_BLOCK",
    "FENCED_SYNTAX_BLOCK",
    "HARD_CONSTRAINTS_BLOCK",
    "INTERRUPTED_NOTICE",
    "LANGUAGE_BLOCK",
    "LSP_BLOCK",
    "LSP_TOOLS_BLOCK",
    "MODE_SWITCH_TO_AGENT",
    "MODE_SWITCH_TO_AUTONOMOUS",
    "MODE_SWITCH_TO_PLANNING",
    "ORCHESTRATION_TRIGGER_BLOCK",
    "PLANNING_BLOCK",
    "PLANNING_MODE_BLOCK",
    "REFLECT_PROMPT",
    "RESPONSE_STRUCTURE_BLOCK",
    "SUBAGENTS_BLOCK",
    "SYSTEM_PROMPT",
    "THINK_BLOCK",
    "THINK_SWITCH_OFF",
    "THINK_SWITCH_ON",
    "TONE_AND_OUTPUT_BLOCK",
    "TOOLS_LIST_BLOCK",
    "TOOLS_REFERENCE_BLOCK",
    "TOOL_FORMAT_BLOCK",
    "TOOL_FORMAT_TEXT_BLOCK",
    "TOOL_STRATEGY_BLOCK",
    "VERIFICATION_GATE_BLOCK",
    "WEB_SEARCH_BLOCK",
    "docx_block_for",
    "execution_model_block_for",
    "hard_constraints_block_for",
    "planning_block_for",
    "response_structure_block_for",
    "think_block_for",
    "tool_format_block_for",
    "tool_strategy_block_for",
]
