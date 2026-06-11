"""Чтение/запись memory-файлов (markdown + YAML-подобный frontmatter).

Формат файла:

    ---
    type: feedback
    created: 2026-06-11
    updated: 2026-06-11
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

from config.paths import memory_dir_for
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


def scan_memories(working_dir: str | None = None) -> list[MemoryFile]:
    """Сканирует все memory-файлы проекта."""
    mdir = memory_dir_for(working_dir)
    if not mdir.exists():
        return []
    out: list[MemoryFile] = []
    for p in sorted(mdir.glob("*.md")):
        mf = read_memory(p)
        if mf is not None:
            out.append(mf)
    return out


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
    working_dir: str | None = None,
) -> MemoryFile:
    """Создаёт или перезаписывает memory-файл. Возвращает MemoryFile.

    today — абсолютная дата (передаётся снаружи; модуль не дёргает системные
    часы намеренно, чтобы быть детерминированным в тестах/воркфлоу).
    """
    if mtype not in MEMORY_TYPES:
        mtype = "project"
    mdir = memory_dir_for(working_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    path = mdir / _safe_filename(name)

    created = today
    existing = read_memory(path) if path.exists() else None
    if existing is not None and existing.created:
        created = existing.created

    body = (body or "").strip()[:_MAX_FILE_CHARS]
    mf = MemoryFile(path=path, type=mtype, created=created, updated=today, body=body)
    try:
        path.write_text(mf.render(), encoding="utf-8")
        logger.info("memory: wrote {} (type={})", path.name, mtype)
    except OSError as e:
        logger.error("memory: write failed {}: {}", path, e)
    return mf


def format_memory_block(working_dir: str | None = None, *, max_chars: int = 6_000) -> str:
    """Собирает память проекта в блок для системного промпта.

    Возвращает пустую строку, если памяти нет.
    """
    files = scan_memories(working_dir)
    if not files:
        return ""

    # Группируем по типу в осмысленном порядке.
    order = {t: i for i, t in enumerate(MEMORY_TYPES)}
    files.sort(key=lambda f: (order.get(f.type, 99), f.name))

    parts: list[str] = [
        "<persistent_memory>",
        "Долговременная память из прошлых сессий этого проекта. "
        "Используй её, чтобы учитывать предпочтения пользователя и контекст. "
        "Если факт устарел — обнови соответствующий memory-файл.",
        "",
    ]
    used = 0
    for f in files:
        chunk = f"### [{f.type}] {f.name}\n{f.body}\n"
        if used + len(chunk) > max_chars:
            parts.append("… (память усечена по лимиту)")
            break
        parts.append(chunk)
        used += len(chunk)
    parts.append("</persistent_memory>")
    return "\n".join(parts)


def format_manifest(working_dir: str | None = None) -> str:
    """Краткий перечень существующих memory-файлов (для extract-промпта)."""
    files = scan_memories(working_dir)
    if not files:
        return ""
    lines = [f"- {f.name} (type={f.type}): {f.body.splitlines()[0][:80] if f.body else ''}" for f in files]
    return "\n".join(lines)
