"""
Агентный цикл со стримингом.

Публичный API:
    from agent import run_agent, run_agent_interactive, AgentContext
"""

from agent.context import AgentContext
from agent.loop import (
    build_first_message,
    get_current_ctx,
    run_agent,
    run_agent_interactive,
    set_current_ctx,
)

__all__ = [
    "AgentContext",
    "build_first_message",
    "get_current_ctx",
    "run_agent",
    "run_agent_interactive",
    "set_current_ctx",
]
