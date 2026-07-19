"""Запись текста в системный буфер обмена.

Linux: wl-copy (Wayland) → xclip (X11)
macOS: pbcopy
Windows: clip.exe
"""

import os
import shutil
import subprocess

from logger import logger


def _real_env() -> dict:
    """DISPLAY/WAYLAND_DISPLAY как при старте процесса (на случай подмены)."""
    env = os.environ.copy()
    return env


def copy_to_clipboard(text: str) -> str | None:
    """Копирует текст в системный буфер. Возвращает None при успехе, иначе строку с ошибкой."""
    if not text:
        return "empty text"

    env = _real_env()
    data = text.encode("utf-8")

    # detach=True → процесс становится демоном (держит буфер пока кто-то не возьмёт),
    # subprocess.run на него ждать НЕ должен (timeout). Используем Popen + write + close.
    candidates: list[tuple[str, list[str], bool]] = []
    if shutil.which("wl-copy"):
        candidates.append(("wl-copy", ["wl-copy"], False))
    if shutil.which("xclip"):
        # xclip держит селекцию до тех пор, пока её не вставят — поэтому detach.
        candidates.append(("xclip", ["xclip", "-selection", "clipboard"], True))
    if shutil.which("xsel"):
        candidates.append(("xsel", ["xsel", "--clipboard", "--input"], True))
    if shutil.which("pbcopy"):
        candidates.append(("pbcopy", ["pbcopy"], False))
    if shutil.which("clip.exe"):
        candidates.append(("clip.exe", ["clip.exe"], False))

    last_err = "no clipboard tool found (wl-copy/xclip/xsel/pbcopy/clip.exe)"
    for name, cmd, detach in candidates:
        try:
            if detach:
                # Демонизируем: новый process group, /dev/null для stdout/stderr,
                # пишем данные через stdin и закрываем — процесс продолжает жить.
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    start_new_session=True,
                )
                try:
                    proc.stdin.write(data)
                    proc.stdin.close()
                except Exception as e:
                    last_err = f"{name}: write failed: {e}"
                    logger.warning("clipboard.copy failed via {}: {}", name, e)
                    continue
                logger.info("clipboard.copy: {} ok detached ({} bytes)", name, len(data))
                return None
            else:
                r = subprocess.run(cmd, input=data, env=env, capture_output=True, timeout=5)
                if r.returncode == 0:
                    logger.info("clipboard.copy: {} ok ({} bytes)", name, len(data))
                    return None
                last_err = f"{name} exit {r.returncode}: {r.stderr.decode('utf-8', 'replace').strip()[:200]}"
        except Exception as e:
            last_err = f"{name}: {e}"
            logger.warning("clipboard.copy failed via {}: {}", name, e)
            continue

    return last_err
