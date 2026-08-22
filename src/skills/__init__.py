"""Public skill facade with lazy imports."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'ACTIVE_WINDOW_ROUNDS': ('skills.registry', 'ACTIVE_WINDOW_ROUNDS'),
    'SKILL_TOOLS': ('skills.registry', 'SKILL_TOOLS'),
    'SkillInfo': ('skills.manager', 'SkillInfo'),
    'activate_skill': ('skills.manager', 'activate_skill'),
    'active_skills_from_messages': ('skills.registry', 'active_skills_from_messages'),
    'consume_pending_messages': ('skills.manager', 'consume_pending_messages'),
    'create_skill': ('skills.manager', 'create_skill'),
    'deactivate_skill': ('skills.manager', 'deactivate_skill'),
    'discover_skills': ('skills.manager', 'discover_skills'),
    'get_active_skills': ('skills.manager', 'get_active_skills'),
    'get_skills_dir': ('skills.manager', 'get_skills_dir'),
    'is_skill_active': ('skills.manager', 'is_skill_active'),
    'list_skills': ('skills.manager', 'list_skills'),
    'load_skill': ('skills.manager', 'load_skill'),
    'remove_skill': ('skills.manager', 'remove_skill'),
    'reset_active_skills': ('skills.manager', 'reset_active_skills'),
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
