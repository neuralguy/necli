"""Public API-provider facade. Heavy provider registry imports are resolved only when requested."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'add_api_config': ('apis.config', 'add_api_config'),
    'get_api_key': ('apis.config', 'get_api_key'),
    'get_provider': ('apis.registry', 'get_provider'),
    'list_api_configs': ('apis.config', 'list_api_configs'),
    'list_providers': ('apis.registry', 'list_providers'),
    'reload_providers': ('apis.registry', 'reload_providers'),
    'remove_api_config': ('apis.config', 'remove_api_config'),
    'set_api_key': ('apis.config', 'set_api_key'),
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
