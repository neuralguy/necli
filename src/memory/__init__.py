"""Public persistent-memory facade with lazy imports."""

from __future__ import annotations

from importlib import import_module

_LAZY = {
    'MEMORY_TYPES': ('memory.memdir', 'MEMORY_TYPES'),
    'MemoryFile': ('memory.memdir', 'MemoryFile'),
    'delete_memory': ('memory.memdir', 'delete_memory'),
    'find_similar_memories': ('memory.memdir', 'find_similar_memories'),
    'format_manifest': ('memory.memdir', 'format_manifest'),
    'format_memory_block': ('memory.memdir', 'format_memory_block'),
    'format_similar_memories': ('memory.memdir', 'format_similar_memories'),
    'maybe_cleanup_memories': ('memory.cleanup', 'maybe_cleanup_memories'),
    'memory_path': ('memory.memdir', 'memory_path'),
    'read_memory': ('memory.memdir', 'read_memory'),
    'scan_memories': ('memory.memdir', 'scan_memories'),
    'write_memory': ('memory.memdir', 'write_memory'),
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
