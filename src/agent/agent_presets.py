"""Заготовки субагентов (presets) — переиспользуемые роли с готовым промптом.

Хранятся в .data/agents/<name>/AGENT.md по тому же паттерну, что и скиллы:
YAML-подобный frontmatter + markdown-тело (системная инструкция для субагента).

Frontmatter-поля:
  name        — имя пресета (для ссылки в tasks: {"preset": "<name>"})
  description — короткое описание (видно главному агенту в промпте)
  model       — (опц.) дефолтная модель субагента (display_name или model_id)

Тело файла = роль-инструкция, подмешивается субагенту как ROLE-блок.

Главный агент может СОЗДАВАТЬ новые пресеты, просто записав файл
.data/agents/<name>/AGENT.md через write_file — discover их подхватит.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.paths import BASE_DIR

logger = logging.getLogger(__name__)

AGENTS_DIR = BASE_DIR / "agents"
PRESET_FILENAME = "AGENT.md"

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class AgentPreset:
    name: str
    description: str
    path: Path
    model: Optional[str] = None
    _body: Optional[str] = None

    @property
    def body(self) -> str:
        if self._body is None:
            self._body = _load_body(self.path)
        return self._body


def get_agents_dir() -> Path:
    return AGENTS_DIR


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_yaml = m.group(1)
    body = text[m.end():]
    meta: dict[str, str] = {}
    for line in raw_yaml.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip().lower()] = val.strip()
    return meta, body


def _load_body(preset_path: Path) -> str:
    md = preset_path / PRESET_FILENAME
    if not md.exists():
        return ""
    text = md.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    return body.strip()


def _load_preset_info(preset_dir: Path) -> Optional[AgentPreset]:
    md = preset_dir / PRESET_FILENAME
    if not md.exists():
        return None
    text = md.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    name = meta.get("name", preset_dir.name)
    description = meta.get("description", "")
    if not description:
        first_para = body.strip().split("\n\n")[0] if body.strip() else ""
        description = first_para[:200]
    model = (meta.get("model") or "").strip() or None
    return AgentPreset(
        name=name,
        description=description,
        path=preset_dir,
        model=model,
        _body=body.strip(),
    )


def discover_presets() -> list[AgentPreset]:
    if not AGENTS_DIR.exists():
        return []
    presets = []
    for d in sorted(AGENTS_DIR.iterdir()):
        if d.is_dir():
            info = _load_preset_info(d)
            if info:
                presets.append(info)
    return presets


def list_presets() -> list[AgentPreset]:
    return discover_presets()


def load_preset(name: str) -> Optional[AgentPreset]:
    if not name:
        return None
    key = name.strip()
    for p in discover_presets():
        if p.name == key:
            return p
    preset_dir = AGENTS_DIR / key
    if preset_dir.exists():
        return _load_preset_info(preset_dir)
    return None


def create_preset(
    name: str,
    description: str,
    body: str,
    model: Optional[str] = None,
) -> AgentPreset:
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    preset_dir = AGENTS_DIR / name
    preset_dir.mkdir(parents=True, exist_ok=True)
    md = preset_dir / PRESET_FILENAME
    fm = [f"name: {name}", f"description: {description}"]
    if model:
        fm.append(f"model: {model}")
    text = "---\n" + "\n".join(fm) + "\n---\n\n" + body.rstrip() + "\n"
    md.write_text(text, encoding="utf-8")
    logger.info("agent_preset create: %s", name)
    return _load_preset_info(preset_dir)  # type: ignore[return-value]


def remove_preset(name: str) -> bool:
    p = load_preset(name)
    if p is None:
        logger.warning("agent_preset remove: not found %s", name)
        return False
    shutil.rmtree(p.path)
    logger.info("agent_preset remove: %s", name)
    return True


def build_presets_prompt() -> str:
    """Список доступных пресетов для system prompt главного агента."""
    presets = discover_presets()
    if not presets:
        return ""
    lines = ["", "AVAILABLE AGENT PRESETS (reusable subagent roles):"]
    for p in presets:
        meta = []
        if p.model:
            meta.append(f"model={p.model}")
        suffix = f"  [{', '.join(meta)}]" if meta else ""
        desc = (p.description or "")[:120]
        lines.append(f'  - "{p.name}": {desc}{suffix}')
    lines.append(
        'Reuse one via subagent task: {"preset": "<name>", "prompt": "<the task>"}. '
        "A preset supplies the role/instructions/model; you only give the concrete task."
    )
    lines.append("")
    return "\n".join(lines)