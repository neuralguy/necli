"""Чтение/запись memory-файлов (markdown + YAML-подобный frontmatter).

Формат файла:

    ---
    type: feedback
    created: 2026-06-11T14:30:00+03:00
    updated: 2026-06-11T14:30:00+03:00
    ---
    Лид-строка с самим правилом.

    **Why:** причина.
    **How to apply:** когда применять.

Типы памяти (4, как в claude-code):
  user      — кто пользователь, его роль/предпочтения/уровень.
  feedback  — как подходить к работе (что делать / чего избегать).
  project   — контекст текущей работы/целей/инцидентов (не выводимо из кода).
  reference — внешние факты/ссылки/значения, полезные в будущем.

Память НЕ должна дублировать то, что выводимо из кода/git/AGENTS.md.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from config._atomic import atomic_write_text
from config.paths import global_memory_dir, memory_dir_for
from logger import logger

MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_SAFE_NAME_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_FILE_CHARS = 8_000
_WORD_RE = re.compile(r"[\w-]{2,}", re.UNICODE)


@dataclass
class MemoryFile:
    path: Path
    type: str = "project"
    created: str = ""
    updated: str = ""
    body: str = ""
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.path.name

    def render(self) -> str:
        """Сериализует обратно в markdown с frontmatter."""
        meta_lines = [f"type: {self.type}"]
        if self.created:
            meta_lines.append(f"created: {self.created}")
        if self.updated:
            meta_lines.append(f"updated: {self.updated}")
        for k, v in self.extra.items():
            meta_lines.append(f"{k}: {v}")
        meta = "\n".join(meta_lines)
        return f"---\n{meta}\n---\n{self.body.strip()}\n"


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text.strip()
    raw_meta, body = m.group(1), m.group(2)
    meta: dict[str, str] = {}
    for line in raw_meta.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = v.strip()
    return meta, body.strip()


def _safe_filename(name: str) -> str:
    name = name.strip().lower().replace(" ", "-")
    name = _SAFE_NAME_RE.sub("-", name).strip("-")
    if not name:
        name = "memory"
    if not name.endswith(".md"):
        name += ".md"
    return name


def memory_path(name: str, *, working_dir: str | None = None, scope: str = "project") -> Path:
    """Canonical in-scope path for a memory name.

    All CRUD operations use this function so ``read`` cannot bypass the same
    filename normalization that protects ``write`` and ``delete``.
    """
    mdir = global_memory_dir() if scope == "global" else memory_dir_for(working_dir)
    return mdir / _safe_filename(name)


def _scan_dir(mdir: Path) -> list[MemoryFile]:
    if not mdir.exists():
        return []
    out: list[MemoryFile] = []
    for p in sorted(mdir.glob("*.md")):
        mf = read_memory(p)
        if mf is not None:
            out.append(mf)
    return out


def scan_memories(working_dir: str | None = None, *, scope: str = "project") -> list[MemoryFile]:
    """Сканирует memory-файлы.

    scope="project" — память текущего проекта (working_dir).
    scope="global"  — кросс-проектная память (_global).
    scope="all"     — обе, глобальная первой.
    """
    if scope == "global":
        return _scan_dir(global_memory_dir())
    if scope == "all":
        return _scan_dir(global_memory_dir()) + _scan_dir(memory_dir_for(working_dir))
    return _scan_dir(memory_dir_for(working_dir))


def read_memory(path: Path) -> MemoryFile | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("memory: read failed {}: {}", path, e)
        return None
    meta, body = _parse_frontmatter(text)
    mtype = meta.pop("type", "project")
    if mtype not in MEMORY_TYPES:
        mtype = "project"
    created = meta.pop("created", "")
    updated = meta.pop("updated", "")
    return MemoryFile(
        path=path,
        type=mtype,
        created=created,
        updated=updated,
        body=body,
        extra=meta,
    )


def write_memory(
    name: str,
    body: str,
    *,
    mtype: str = "project",
    today: str = "",
    timestamp: str = "",
    working_dir: str | None = None,
    scope: str = "project",
) -> MemoryFile:
    """Создаёт или перезаписывает memory-файл. Возвращает MemoryFile.

    timestamp — абсолютные дата+время добавления/обновления. today оставлен для
    обратной совместимости date-only тестов и старых call-site'ов. Модуль не
    дёргает системные часы, чтобы быть детерминированным в тестах/воркфлоу.
    scope="project" пишет в память проекта, scope="global" — в кросс-проектную.
    """
    if mtype not in MEMORY_TYPES:
        mtype = "project"
    path = memory_path(name, working_dir=working_dir, scope=scope)
    path.parent.mkdir(parents=True, exist_ok=True)

    now = timestamp or today
    created = now
    existing = read_memory(path) if path.exists() else None
    if existing is not None and existing.created:
        created = existing.created

    extra = dict(existing.extra) if existing is not None else {}
    body = (body or "").strip()[:_MAX_FILE_CHARS]
    mf = MemoryFile(path=path, type=mtype, created=created, updated=now, body=body, extra=extra)
    try:
        atomic_write_text(path, mf.render())
        logger.info("memory: wrote {} (type={})", path.name, mtype)
    except OSError as e:
        logger.error("memory: write failed {}: {}", path, e)
        raise
    return mf


def delete_memory(
    name: str,
    *,
    working_dir: str | None = None,
    scope: str = "project",
) -> MemoryFile | None:
    """Удаляет memory-файл и возвращает его последнее содержимое.

    Имя всегда нормализуется через тот же путь, что и при записи, поэтому
    удалить файл за пределами каталога памяти невозможно.
    """
    path = memory_path(name, working_dir=working_dir, scope=scope)
    if not path.is_file():
        return None
    existing = read_memory(path)
    if existing is None:
        return None
    path.unlink()
    logger.info("memory: deleted {} (scope={})", path.name, scope)
    return existing


def _is_pinned(f: MemoryFile) -> bool:
    pinned = f.extra.get("pinned", "").strip().lower()
    priority = f.extra.get("priority", "").strip().lower()
    return pinned in ("1", "true", "yes", "on") or priority in ("pinned", "high", "critical")


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in _WORD_RE.findall(text or "")}


def find_similar_memories(
    query: str,
    working_dir: str | None = None,
    *,
    limit: int = 8,
) -> list[MemoryFile]:
    """Return memories lexically closest to *query*, across both scopes."""
    files = scan_memories(working_dir, scope="all")
    query_tokens = _tokens(query)
    if not query_tokens:
        return []
    document_frequency: dict[str, int] = {}
    token_sets: list[set[str]] = []
    for memory in files:
        tokens = _tokens(f"{memory.path.stem} {memory.type} {memory.body}")
        token_sets.append(tokens)
        for token in tokens:
            document_frequency[token] = document_frequency.get(token, 0) + 1
    total = max(1, len(files))
    ranked: list[tuple[float, MemoryFile]] = []
    for memory, tokens in zip(files, token_sets, strict=True):
        overlap = query_tokens & tokens
        if not overlap:
            continue
        score = sum(math.log((total + 1) / (document_frequency[t] + 1)) + 1 for t in overlap)
        score /= math.sqrt(max(1, len(tokens)))
        ranked.append((score, memory))
    ranked.sort(key=lambda item: (item[0], item[1].updated, item[1].name), reverse=True)
    return [memory for _, memory in ranked[: max(0, limit)]]


def _time_suffix(f: MemoryFile) -> str:
    details = []
    if _is_pinned(f):
        details.append("pinned=true")
    if f.created:
        details.append(f"created={f.created}")
    if f.updated:
        details.append(f"updated={f.updated}")
    return f" ({', '.join(details)})" if details else ""


def format_memory_block(
    working_dir: str | None = None,
    *,
    query: str = "",
    max_chars: int = 6_000,
    relevant_limit: int = 8,
) -> str:
    """Собирает память (глобальную + проекта) в блок для системного промпта.

    Глобальная (кросс-проектная) память идёт первой и помечается [global …],
    затем память текущего проекта. Возвращает пустую строку, если памяти нет.
    """
    global_files = scan_memories(working_dir, scope="global")
    project_files = scan_memories(working_dir, scope="project")
    if not global_files and not project_files:
        return ""

    parts: list[str] = [
        "<persistent_memory>",
        (
            "Долговременная память из прошлых сессий. Используй её, чтобы учитывать "
            "предпочтения пользователя и контекст. Записи [global …] относятся ко "
            "ВСЕМ проектам (кто пользователь, общие предпочтения/стиль работы); "
            "остальные — к текущему проекту. Если факт устарел — обнови файл "
            "(тем же scope)."
        ),
        "",
    ]
    entries: list[tuple[str, MemoryFile]] = [
        *[("global", f) for f in global_files],
        *[("project", f) for f in project_files],
    ]
    pinned_entries = [(scope_label, f) for scope_label, f in entries if _is_pinned(f)]
    relevant = find_similar_memories(query, working_dir, limit=relevant_limit)
    relevant_paths = {f.path for f in relevant}
    regular_entries = [
        (scope_label, f)
        for scope_label, f in entries
        if not _is_pinned(f) and f.path in relevant_paths
    ]
    regular_entries.sort(key=lambda item: relevant_paths and relevant.index(item[1]))

    def _chunk(scope_label: str, f: MemoryFile) -> str:
        tag = f"{scope_label}/{f.type}" if scope_label == "global" else f.type
        return f"### [{tag}] {f.name}{_time_suffix(f)}\n{f.body}\n"

    used = 0
    truncated = False
    for scope_label, f in [*pinned_entries, *regular_entries]:
        chunk = _chunk(scope_label, f)
        if used + len(chunk) > max_chars:
            truncated = True
            break
        parts.append(chunk)
        used += len(chunk)
    if truncated:
        parts.append("… (часть выбранной памяти не поместилась в лимит)")
    parts.append("</persistent_memory>")
    return "\n".join(parts)


def format_manifest(working_dir: str | None = None) -> str:
    """Краткий перечень существующих memory-файлов (для extract-промпта)."""
    files = scan_memories(working_dir, scope="all")
    if not files:
        return ""
    lines = [
        f"- {f.name} (type={f.type}{_time_suffix(f)}): "
        f"{f.body.splitlines()[0][:80] if f.body else ''}"
        for f in files
    ]
    return "\n".join(lines)


def format_similar_memories(query: str, working_dir: str | None = None, *, limit: int = 8) -> str:
    """Full candidate records used before a model changes persistent memory."""
    files = find_similar_memories(query, working_dir, limit=limit)
    if not files:
        return ""
    global_root = global_memory_dir()
    parts = []
    for memory in files:
        scope = "global" if memory.path.parent == global_root else "project"
        parts.append(
            f"### {scope}/{memory.name} (type={memory.type}{_time_suffix(memory)})\n{memory.body}"
        )
    return "\n\n".join(parts)
