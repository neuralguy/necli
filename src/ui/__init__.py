"""Lightweight UI package exports.

Keep package import cheap: modules such as ``logger`` import ``ui._emoji_width``
during process startup, and Python executes this file before loading the submodule.
Heavy clipboard/prompt/formatting imports are therefore resolved lazily.
"""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    "grab_image_from_clipboard": ("ui.clipboard", "grab_image_from_clipboard"),
    "format_cost": ("ui.formatting", "format_cost"),
    "format_tokens": ("ui.formatting", "format_tokens"),
    "InputPrompt": ("ui.prompt", "InputPrompt"),
    "_EOF": ("ui.prompt", "_EOF"),
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
