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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.paths import global_memory_dir, memory_dir_for
from logger import logger

MEMORY_TYPES: tuple[str, ...] = ("user", "feedback", "project", "reference")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)
_SAFE_NAME_RE = re.compile(r"[^a-z0-9._-]+")
_MAX_FILE_CHARS = 8_000


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


def _scan_dir(mdir: Path) -> list[MemoryFile]:
    if not mdir.exists():
        return []
    out: list[MemoryFile] = []
    for p in sorted(mdir.glob("*.md")):
        mf = read_memory(p)
        if mf is not None:
            out.append(mf)
    return out


def scan_memories(
    working_dir: str | None = None, *, scope: str = "project"
) -> list[MemoryFile]:
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


def read_memory(path: Path) -> Optional[MemoryFile]:
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
        path=path, type=mtype, created=created, updated=updated,
        body=body, extra=meta,
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
    mdir = global_memory_dir() if scope == "global" else memory_dir_for(working_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    path = mdir / _safe_filename(name)

    now = timestamp or today
    created = now
    existing = read_memory(path) if path.exists() else None
    if existing is not None and existing.created:
        created = existing.created

    extra = dict(existing.extra) if existing is not None else {}
    body = (body or "").strip()[:_MAX_FILE_CHARS]
    mf = MemoryFile(path=path, type=mtype, created=created, updated=now, body=body, extra=extra)
    try:
        path.write_text(mf.render(), encoding="utf-8")
        logger.info("memory: wrote {} (type={})", path.name, mtype)
    except OSError as e:
        logger.error("memory: write failed {}: {}", path, e)
        raise
    return mf


def _is_pinned(f: MemoryFile) -> bool:
    pinned = f.extra.get("pinned", "").strip().lower()
    priority = f.extra.get("priority", "").strip().lower()
    return pinned in ("1", "true", "yes", "on") or priority in ("pinned", "high", "critical")


def _time_suffix(f: MemoryFile) -> str:
    details = []
    if _is_pinned(f):
        details.append("pinned=true")
    if f.created:
        details.append(f"created={f.created}")
    if f.updated:
        details.append(f"updated={f.updated}")
    return f" ({', '.join(details)})" if details else ""


def format_memory_block(working_dir: str | None = None, *, max_chars: int = 6_000) -> str:
    """Собирает память (глобальную + проекта) в блок для системного промпта.

    Глобальная (кросс-проектная) память идёт первой и помечается [global …],
    затем память текущего проекта. Возвращает пустую строку, если памяти нет.
    """
    global_files = scan_memories(working_dir, scope="global")
    project_files = scan_memories(working_dir, scope="project")
    if not global_files and not project_files:
        return ""

    # Группируем по типу в осмысленном порядке (внутри каждой области).
    order = {t: i for i, t in enumerate(MEMORY_TYPES)}
    sort_key = lambda f: (order.get(f.type, 99), f.name)  # noqa: E731
    global_files.sort(key=sort_key)
    project_files.sort(key=sort_key)

    parts: list[str] = [
        "<persistent_memory>",
        "Долговременная память из прошлых сессий. Используй её, чтобы учитывать "
        "предпочтения пользователя и контекст. Записи [global …] относятся ко "
        "ВСЕМ проектам (кто пользователь, общие предпочтения/стиль работы); "
        "остальные — к текущему проекту. Если факт устарел — обнови файл "
        "(тем же scope).",
        "",
    ]
    entries: list[tuple[str, MemoryFile]] = [
        *[("global", f) for f in global_files],
        *[("project", f) for f in project_files],
    ]
    pinned_entries = [(scope_label, f) for scope_label, f in entries if _is_pinned(f)]
    regular_entries = [(scope_label, f) for scope_label, f in entries if not _is_pinned(f)]

    def _chunk(scope_label: str, f: MemoryFile) -> str:
        tag = f"{scope_label}/{f.type}" if scope_label == "global" else f.type
        return f"### [{tag}] {f.name}{_time_suffix(f)}\n{f.body}\n"

    for scope_label, f in pinned_entries:
        parts.append(_chunk(scope_label, f))

    used = 0
    truncated = False
    for scope_label, f in regular_entries:
        chunk = _chunk(scope_label, f)
        if used + len(chunk) > max_chars:
            truncated = True
            break
        parts.append(chunk)
        used += len(chunk)
    if truncated:
        parts.append("… (память усечена по лимиту)")
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
