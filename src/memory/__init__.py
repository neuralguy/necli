"""Персистентная память агента (memdir).

Порт memory-системы Claude Code под necli. Хранит долговременные факты,
не выводимые из текущего состояния проекта (предпочтения пользователя,
обратная связь, контекст работы, внешние референсы), в markdown-файлах с
frontmatter в .data/memory/<project>/.

Память:
  - подмешивается в системный промпт следующих сессий через
    format_memory_block() (см. system_prompt._build_memory_block);
  - редактируется основной моделью напрямую через инструмент memory;
  - периодически проверяется и упорядочивается отдельным аудитом раз в три дня.

Публичный API:
  scan_memories(working_dir)        -> list[MemoryFile]
  format_memory_block(working_dir)  -> str   (для системного промпта)
  read_memory / write_memory / delete_memory -> CRUD
"""

from .cleanup import maybe_cleanup_memories
from .memdir import (
    MEMORY_TYPES,
    MemoryFile,
    delete_memory,
    find_similar_memories,
    format_manifest,
    format_memory_block,
    format_similar_memories,
    memory_path,
    read_memory,
    scan_memories,
    write_memory,
)

__all__ = [
    "MEMORY_TYPES",
    "MemoryFile",
    "delete_memory",
    "find_similar_memories",
    "format_manifest",
    "format_memory_block",
    "format_similar_memories",
    "maybe_cleanup_memories",
    "memory_path",
    "read_memory",
    "scan_memories",
    "write_memory",
]
