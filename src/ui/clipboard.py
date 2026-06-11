"""
Чтение изображений из системного буфера обмена.

Linux:   xclip или wl-paste
macOS:   osascript + pngpaste
Windows: Pillow ImageGrab (не приоритет)
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

import config

logger = logging.getLogger(__name__)

_IMAGES_DIR = config.BASE_DIR / "clipboard_images"
_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

# Запоминаем ОРИГИНАЛЬНЫЙ DISPLAY при импорте модуля,
# ДО возможной подмены DISPLAY
_ORIGINAL_DISPLAY: Optional[str] = os.environ.get("DISPLAY")
_ORIGINAL_WAYLAND_DISPLAY: Optional[str] = os.environ.get("WAYLAND_DISPLAY")


def _has_cmd(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _get_real_clipboard_env() -> dict:
    """
    Возвращает окружение с ОРИГИНАЛЬНЫМ DISPLAY/WAYLAND_DISPLAY,
    чтобы xclip/wl-paste обращались к реальному рабочему столу,
    а не к Xvfb :99.
    """
    env = os.environ.copy()
    if _ORIGINAL_DISPLAY is not None:
        env["DISPLAY"] = _ORIGINAL_DISPLAY
    elif "DISPLAY" in env and env["DISPLAY"] == ":99":
        # Xvfb — пробуем стандартный :0
        env["DISPLAY"] = ":0"
    if _ORIGINAL_WAYLAND_DISPLAY is not None:
        env["WAYLAND_DISPLAY"] = _ORIGINAL_WAYLAND_DISPLAY
    return env


def grab_image_from_clipboard() -> Optional[Path]:
    """
    Пытается извлечь изображение из системного буфера обмена.

    Возвращает Path к PNG-файлу или None если в буфере нет изображения.
    """
    # Генерируем уникальное имя
    ts = int(time.time() * 1000)
    dest = _IMAGES_DIR / f"clip_{ts}.png"
    logger.debug("clipboard.grab: trying tools (xclip/wl-paste/pngpaste/pillow)")

    # Linux X11: xclip
    if _has_cmd("xclip"):
        result = _grab_xclip(dest)
        if result:
            logger.info("clipboard.grab: xclip ok → %s (%d bytes)", result, result.stat().st_size)
            return result

    # Linux Wayland: wl-paste
    if _has_cmd("wl-paste"):
        result = _grab_wl_paste(dest)
        if result:
            logger.info("clipboard.grab: wl-paste ok → %s", result)
            return result

    # macOS: pngpaste или osascript
    if _has_cmd("pngpaste"):
        result = _grab_pngpaste(dest)
        if result:
            logger.info("clipboard.grab: pngpaste ok → %s", result)
            return result

    # Fallback: Pillow ImageGrab (macOS, Windows)
    result = _grab_pillow(dest)
    if result:
        logger.info("clipboard.grab: pillow ok → %s", result)
        return result

    logger.debug("clipboard.grab: no image in clipboard")
    return None


def _grab_xclip(dest: Path) -> Optional[Path]:
    """Извлекает изображение через xclip (X11)."""
    try:
        env = _get_real_clipboard_env()

        # Проверяем есть ли изображение в буфере
        check = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "TARGETS", "-o"],
            capture_output=True, text=True, timeout=3,
            env=env,
        )
        targets = check.stdout.lower()

        mime = None
        if "image/png" in targets:
            mime = "image/png"
        elif "image/jpeg" in targets:
            mime = "image/jpeg"
        elif "image/bmp" in targets:
            mime = "image/bmp"

        if not mime:
            return None

        result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", mime, "-o"],
            capture_output=True, timeout=5,
            env=env,
        )
        if result.returncode != 0 or not result.stdout:
            return None

        dest.write_bytes(result.stdout)

        # Проверяем что файл не пустой и похож на изображение
        if dest.stat().st_size < 100:
            dest.unlink(missing_ok=True)
            return None

        return dest
    except Exception as e:
        logger.debug("clipboard.grab: xclip failed: %s", e, exc_info=True)
        dest.unlink(missing_ok=True)
        return None


def _grab_wl_paste(dest: Path) -> Optional[Path]:
    """Извлекает изображение через wl-paste (Wayland)."""
    try:
        env = _get_real_clipboard_env()

        # Проверяем тип содержимого
        check = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=3,
            env=env,
        )
        types = check.stdout.lower()

        mime = None
        if "image/png" in types:
            mime = "image/png"
        elif "image/jpeg" in types:
            mime = "image/jpeg"

        if not mime:
            return None

        result = subprocess.run(
            ["wl-paste", "--type", mime],
            capture_output=True, timeout=5,
            env=env,
        )
        if result.returncode != 0 or not result.stdout:
            return None

        dest.write_bytes(result.stdout)
        if dest.stat().st_size < 100:
            dest.unlink(missing_ok=True)
            return None

        return dest
    except Exception as e:
        logger.debug("clipboard.grab: wl-paste failed: %s", e, exc_info=True)
        dest.unlink(missing_ok=True)
        return None


def _grab_pngpaste(dest: Path) -> Optional[Path]:
    """Извлекает изображение через pngpaste (macOS)."""
    try:
        result = subprocess.run(
            ["pngpaste", str(dest)],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            dest.unlink(missing_ok=True)
            return None
        if dest.exists() and dest.stat().st_size > 100:
            return dest
        dest.unlink(missing_ok=True)
        return None
    except Exception as e:
        logger.debug("clipboard.grab: pngpaste failed: %s", e, exc_info=True)
        dest.unlink(missing_ok=True)
        return None


def _grab_pillow(dest: Path) -> Optional[Path]:
    """Извлекает изображение через Pillow (cross-platform fallback)."""
    try:
        from PIL import ImageGrab
        img = ImageGrab.grabclipboard()
        if img is None:
            return None
        # ImageGrab может вернуть список путей файлов
        if isinstance(img, list):
            # Проверяем — может это файл изображения
            for f in img:
                if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")):
                    import shutil as sh
                    sh.copy2(f, dest)
                    return dest
            return None
        # PIL Image object
        img.save(str(dest), "PNG")
        if dest.stat().st_size > 100:
            return dest
        dest.unlink(missing_ok=True)
        return None
    except ImportError as e:
        logger.debug("clipboard.grab: Pillow ImageGrab unavailable: %s", e)
        return None
    except Exception as e:
        logger.debug("clipboard.grab: Pillow ImageGrab failed: %s", e, exc_info=True)
        dest.unlink(missing_ok=True)
        return None


def cleanup_old_images(max_age_hours: int = 24):
    """Удаляет старые файлы из clipboard_images."""
    cutoff = time.time() - max_age_hours * 3600
    try:
        for f in _IMAGES_DIR.iterdir():
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception as e:
        logger.debug("clipboard cleanup failed: %s", e, exc_info=True)

