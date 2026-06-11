from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from config.paths import BASE_DIR

logger = logging.getLogger(__name__)

SKILLS_DIR = BASE_DIR / "skills"
SKILL_FILENAME = "SKILL.md"

_active_skills: set[str] = set()
_pending_messages: list[str] = []

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class SkillInfo:
    name: str
    description: str
    path: Path
    disable_model_invocation: bool = False
    _body: Optional[str] = field(default=None, repr=False)

    @property
    def body(self) -> str:
        if self._body is None:
            self._body = _load_body(self.path)
        return self._body


def get_skills_dir() -> Path:
    return SKILLS_DIR


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


def _load_body(skill_path: Path) -> str:
    skill_md = skill_path / SKILL_FILENAME
    if not skill_md.exists():
        return ""
    text = skill_md.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(text)
    return body.strip()


def _load_skill_info(skill_dir: Path) -> Optional[SkillInfo]:
    skill_md = skill_dir / SKILL_FILENAME
    if not skill_md.exists():
        return None
    text = skill_md.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    name = meta.get("name", skill_dir.name)
    description = meta.get("description", "")
    if not description:
        first_para = body.strip().split("\n\n")[0] if body.strip() else ""
        description = first_para[:200]
    disable = meta.get("disable-model-invocation", "false").lower() == "true"
    return SkillInfo(
        name=name,
        description=description,
        path=skill_dir,
        disable_model_invocation=disable,
        _body=body.strip(),
    )


def discover_skills() -> list[SkillInfo]:
    if not SKILLS_DIR.exists():
        return []
    skills = []
    for d in sorted(SKILLS_DIR.iterdir()):
        if d.is_dir():
            info = _load_skill_info(d)
            if info:
                skills.append(info)
    return skills


def list_skills() -> list[SkillInfo]:
    return discover_skills()


def load_skill(name: str) -> Optional[SkillInfo]:
    for skill in discover_skills():
        if skill.name == name:
            return skill
    skill_dir = SKILLS_DIR / name
    if skill_dir.exists():
        return _load_skill_info(skill_dir)
    return None


def create_skill(name: str, description: str, content: str) -> Optional[SkillInfo]:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    skill_dir = SKILLS_DIR / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / SKILL_FILENAME
    text = f"---\nname: {name}\ndescription: {description}\n---\n\n{content}\n"
    skill_md.write_text(text, encoding="utf-8")
    return _load_skill_info(skill_dir)


def add_skill(source_path: Path) -> SkillInfo:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    if source_path.is_file() and source_path.name == SKILL_FILENAME:
        source_path = source_path.parent
    if not source_path.is_dir():
        raise ValueError(f"Ожидалась директория со SKILL.md: {source_path}")
    skill_md = source_path / SKILL_FILENAME
    if not skill_md.exists():
        raise FileNotFoundError(f"Не найден {SKILL_FILENAME} в {source_path}")
    dest = SKILLS_DIR / source_path.name
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source_path, dest)
    info = _load_skill_info(dest)
    if not info:
        raise ValueError(f"Не удалось загрузить скилл из {dest}")
    return info


def remove_skill(name: str) -> bool:
    skill = load_skill(name)
    if skill is None:
        logger.warning("skill remove: not found %s", name)
        return False
    shutil.rmtree(skill.path)
    _active_skills.discard(name)
    logger.info("skill remove: %s", name)
    return True


def activate_skill(name: str) -> None:
    skill = load_skill(name)
    if skill is None:
        logger.warning("skill activate: not found %s", name)
        return
    _active_skills.add(name)
    logger.info("skill activate: %s", name)
    msg = (
        f"━━━ СКИЛЛ АКТИВИРОВАН: {name} ━━━\n"
        f"Следуй этим инструкциям до деактивации:\n\n"
        f"{skill.body}\n"
        f"━━━ КОНЕЦ СКИЛЛА: {name} ━━━"
    )
    _pending_messages.append(msg)


def deactivate_skill(name: str) -> None:
    _active_skills.discard(name)
    logger.info("skill deactivate: %s", name)
    msg = (
        f"━━━ СКИЛЛ ДЕАКТИВИРОВАН: {name} ━━━\n"
        f"Скилл '{name}' больше не действует. "
        f"Не следуй его инструкциям."
    )
    _pending_messages.append(msg)


def is_skill_active(name: str) -> bool:
    return name in _active_skills


def consume_pending_messages() -> list[str]:
    msgs = list(_pending_messages)
    _pending_messages.clear()
    return msgs


def reset_active_skills() -> None:
    _active_skills.clear()
    _pending_messages.clear()


def get_active_skill_names() -> set[str]:
    return set(_active_skills)

def build_skills_prompt() -> str:
    skills = discover_skills()
    if not skills:
        return ""
    available = []
    for s in skills:
        if not s.disable_model_invocation or s.name in _active_skills:
            desc = s.description[:250] if s.description else "(без описания)"
            available.append(f"  - {s.name}: {desc}")
    if not available:
        return ""
    example_json = '{"name": "<имя_скилла>"}'
    lines = [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "СКИЛЛЫ",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "Скиллы — специализированные инструкции, расширяющие твои возможности.",
        "Если задача соответствует доступному скиллу, загрузи его через инструмент skill:",
        ":::call skill",
        example_json,
        "call:::",
        "Скилл вернёт детальные инструкции. Следуй им.",
        "",
        "Доступные скиллы:",
    ]
    lines.extend(available)
    lines.append("")
    return "\n".join(lines)
