"""Public agent API with lazy imports to keep package startup cheap and avoid import cycles."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'AgentContext': ('agent.context', 'AgentContext'),
    'build_first_message': ('agent.loop', 'build_first_message'),
    'get_current_ctx': ('agent.loop', 'get_current_ctx'),
    'run_agent': ('agent.loop', 'run_agent'),
    'run_agent_interactive': ('agent.loop', 'run_agent_interactive'),
    'set_current_ctx': ('agent.loop', 'set_current_ctx'),
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attr = target
    value = getattr(import_module(module_name), attr)
    globals()[name] = value
    return value


def __dir__():
    return sorted(set(globals()) | set(_LAZY))


__all__ = list(_LAZY)
