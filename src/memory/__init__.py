"""Персистентная память агента (memdir).

Порт memory-системы Claude Code под necli. Хранит долговременные факты,
не выводимые из текущего состояния проекта (предпочтения пользователя,
обратная связь, контекст работы, внешние референсы), в markdown-файлах с
frontmatter в .data/memory/<project>/.

Память:
  - подмешивается в системный промпт следующих сессий через
    format_memory_block() (см. system_prompt._build_memory_block);
  - пополняется фоновым one-shot вызовом модели extract_memories() (extract.py),
    запускается из интерактивного цикла каждые N сообщений;
  - редактируется моделью напрямую через инструмент memory.

Публичный API:
  scan_memories(working_dir)        -> list[MemoryFile]
  format_memory_block(working_dir)  -> str   (для системного промпта)
  format_manifest(working_dir)      -> str   (краткий список для extract-промпта)
  read_memory / write_memory / delete_memory -> CRUD
  extract_memories(transcript, ...) -> int   (фоновое извлечение фактов)
"""

from .cleanup import maybe_cleanup_memories
from .extract import extract_memories
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
    "extract_memories",
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
