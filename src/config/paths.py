import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

def _resolve_base_dir() -> Path:
    """Каталог пользовательских данных.

    - Запуск из исходников: .data рядом с кодом (как раньше).
    - Frozen-бинарник (PyInstaller): ~/.necli — рядом с распакованным
      _MEIxxx хранить нельзя (временная папка стирается между запусками).
    - Override через NECLI_HOME.
    """
    env = os.environ.get("NECLI_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path.home() / ".necli"
    # src/config/paths.py → корень репозитория на три уровня вверх.
    return Path(__file__).resolve().parent.parent.parent / ".data"

def resource_path(*parts: str) -> Path:
    """Путь к упакованному ресурсу (read-only, идёт внутри бинарника).

    В frozen-режиме ресурсы лежат в sys._MEIPASS, иначе — в корне проекта.
    """
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base.joinpath(*parts)

BASE_DIR = _resolve_base_dir()
SESSIONS_DIR = BASE_DIR / "sessions"
SKILLS_DIR = BASE_DIR / "skills"
CONFIG_FILE = BASE_DIR / "config.json"
APIS_FILE = BASE_DIR / "apis.json"
UI_FILE = BASE_DIR / "ui.json"
HOOKS_FILE = BASE_DIR / "hooks.json"
MEMORY_DIR = BASE_DIR / "memory"


def memory_dir_for(working_dir: str | None = None) -> Path:
    """Каталог персистентной памяти для конкретного проекта (working_dir).

    Память изолируется по проекту: путь рабочей директории кодируется в имя
    подпапки (как в claude-code: projects/<slug>/memory). Это не даёт памяти
    одного проекта протекать в другой.
    """
    import hashlib

    base = Path(working_dir).expanduser().resolve() if working_dir else Path.cwd()
    # Человекочитаемый префикс + хэш для уникальности (на случай коллизий имён).
    slug = base.name or "root"
    digest = hashlib.sha1(str(base).encode("utf-8")).hexdigest()[:10]
    return MEMORY_DIR / f"{slug}-{digest}"

def global_memory_dir() -> Path:
    """Каталог кросс-проектной (глобальной) памяти.

    Сюда пишутся факты, НЕ привязанные к конкретному проекту: кто пользователь,
    его общие предпочтения и стиль работы, универсальные референсы. Эта память
    подмешивается в системный промпт В ЛЮБОМ проекте, в отличие от проектной.
    """
    return MEMORY_DIR / "_global"

def _seed_bundled(rel: str) -> None:
    """Копирует встроенные ресурсы из бинарника в ~/.necli при первом запуске."""
    if not getattr(sys, "frozen", False):
        return
    import shutil
    src = resource_path("_bundle", *rel.split("/"))
    dst = BASE_DIR / rel
    if not src.exists() or dst.exists():
        return
    try:
        shutil.copytree(src, dst)
    except OSError as exc:
        logger.warning("Не удалось скопировать встроенный ресурс %s → %s: %s", src, dst, exc)


def ensure_dirs() -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    _seed_bundled("skills/default")
    _seed_bundled("agents")
    # Дефолтные скиллы версионируются/поставляются с приложением; пользовательские
    # лежат отдельно (создаются юзером, не попадают в git).
    (SKILLS_DIR / "default").mkdir(parents=True, exist_ok=True)
    (SKILLS_DIR / "user").mkdir(parents=True, exist_ok=True)
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)