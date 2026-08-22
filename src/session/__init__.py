"""Public session facade with lazy imports."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'Message': ('session.message', 'Message'),
    'Session': ('session.session', 'Session'),
    'count_tokens': ('session.tokens', 'count_tokens'),
    'get_statistics': ('session.storage', 'get_statistics'),
    'list_sessions': ('session.storage', 'list_sessions'),
    'load': ('session.storage', 'load'),
    'save': ('session.storage', 'save'),
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
