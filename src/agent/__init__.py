"""
Агентный цикл со стримингом.

Публичный API:
    from agent import run_agent, run_agent_interactive, get_current_plan, AgentContext
"""

from agent.context import AgentContext
from agent.loop import (
    run_agent,
    run_agent_interactive,
    get_current_plan,
    get_current_ctx,
    set_current_ctx,
    build_first_message,
)

__all__ = [
    "AgentContext",
    "run_agent",
    "run_agent_interactive",
    "get_current_plan",
    "get_current_ctx",
    "set_current_ctx",
    "build_first_message",
]
