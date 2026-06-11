"""Хранилище HTML-исходников docx-документов.

Когда create_docx рендерит HTML → DOCX, исходный HTML сохраняется рядом
в .data/docx_sources/ под именем, производным от абсолютного пути docx.
Это позволяет агенту:
  • править существующий документ через patch_file по HTML-источнику,
    а не переписывать его заново;
  • при чтении docx получить точный исходник без потерь pandoc round-trip.

Ключ — sha1 от абсолютного пути .docx. Рядом кладём .meta с оригинальным
путём для отладки/листинга.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from logger import logger
from config.paths import BASE_DIR

def _sources_dir() -> Path:
    d = BASE_DIR / "docx_sources"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _key_for(docx_path: Path) -> str:
    try:
        abs_str = str(docx_path.resolve())
    except Exception:
        abs_str = str(docx_path)
    # Полный sha1-hex (40 символов): 16-символьный префикс давал заметный риск
    # коллизий, при которых исходник одного docx перезаписывал другой.
    return hashlib.sha1(abs_str.encode("utf-8")).hexdigest()

def save_source(docx_path: Path, html: str) -> Path | None:
    """Сохраняет HTML-исходник для данного docx. Возвращает путь к .html или None."""
    try:
        key = _key_for(docx_path)
        d = _sources_dir()
        html_file = d / f"{key}.html"
        html_file.write_text(html, encoding="utf-8")
        (d / f"{key}.meta").write_text(str(docx_path.resolve()), encoding="utf-8")
        logger.info("docx_source saved: {} → {} ({}b)", docx_path.name, html_file.name, len(html))
        return html_file
    except Exception as e:
        logger.opt(exception=True).warning("docx_source save failed for {}: {}", docx_path, e)
        return None

def load_source(docx_path: Path) -> str | None:
    """Возвращает сохранённый HTML-исходник для docx или None, если его нет."""
    try:
        key = _key_for(docx_path)
        html_file = _sources_dir() / f"{key}.html"
        if not html_file.exists():
            return None
        return html_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.opt(exception=True).warning("docx_source load failed for {}: {}", docx_path, e)
        return None

def has_source(docx_path: Path) -> bool:
    try:
        return (_sources_dir() / f"{_key_for(docx_path)}.html").exists()
    except Exception:
        return False

def delete_source(docx_path: Path) -> None:
    """Удаляет сохранённый исходник (например при delete_file docx)."""
    try:
        key = _key_for(docx_path)
        d = _sources_dir()
        for suffix in (".html", ".meta"):
            f = d / f"{key}{suffix}"
            if f.exists():
                f.unlink()
    except Exception as e:
        logger.opt(exception=True).debug("docx_source delete failed for {}: {}", docx_path, e)
    # Шаблон-оригинал тоже чистим.
    delete_template(docx_path)


def save_template(docx_path: Path) -> Path | None:
    """Кладёт копию ОРИГИНАЛЬНОГО .docx как шаблон для точного round-trip.

    При записи нового docx, чей текст совпадает с прочитанным внешним
    документом, мы клонируем этот шаблон и патчим только текст — так
    сохраняются секции, стили, колонтитулы, нумерация, картинки, ширины
    таблиц (pandoc их теряет). Ключ — sha1 от абсолютного пути docx.
    """
    try:
        import shutil
        if not docx_path.exists():
            return None
        key = _key_for(docx_path)
        tpl = _sources_dir() / f"{key}.template.docx"
        shutil.copyfile(str(docx_path), str(tpl))
        # .meta — оригинальный путь, для листинга/отладки и поиска по содержимому.
        (_sources_dir() / f"{key}.template.meta").write_text(
            str(docx_path.resolve()), encoding="utf-8"
        )
        logger.info("docx_template saved: {} → {} ({}b)", docx_path.name, tpl.name, tpl.stat().st_size)
        return tpl
    except Exception as e:
        logger.opt(exception=True).warning("docx_template save failed for {}: {}", docx_path, e)
        return None


def iter_templates() -> list[Path]:
    """Все сохранённые шаблоны-оригиналы (.template.docx)."""
    try:
        return sorted(_sources_dir().glob("*.template.docx"))
    except Exception:
        return []


def load_template(docx_path: Path) -> Path | None:
    """Путь к сохранённому шаблону-оригиналу для docx или None."""
    try:
        tpl = _sources_dir() / f"{_key_for(docx_path)}.template.docx"
        return tpl if tpl.exists() and tpl.stat().st_size > 0 else None
    except Exception:
        return None


def delete_template(docx_path: Path) -> None:
    try:
        key = _key_for(docx_path)
        d = _sources_dir()
        for suffix in (".template.docx", ".template.meta"):
            f = d / f"{key}{suffix}"
            if f.exists():
                f.unlink()
    except Exception as e:
        logger.opt(exception=True).debug("docx_template delete failed for {}: {}", docx_path, e)


__all__ = [
    "save_source", "load_source", "has_source", "delete_source",
    "save_template", "load_template", "delete_template",
]