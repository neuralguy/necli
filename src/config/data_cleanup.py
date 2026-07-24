"""Автоматическая очистка мусора из каталога данных (.data).

Запускается тихо при старте (не чаще раза в сутки, маркер .last_cleanup) и
удаляет ТОЛЬКО заведомо временные/протухшие данные с консервативными порогами.

Что НИКОГДА не трогаем:
  - конфиги и реестры: config.json, apis.json, ui.json, hooks.json,
    *_servers.json, pinned_sessions.json
  - каталоги пользовательского контента: agents/, skills/, memory/
  - закреплённые (pinned) сессии и последние KEEP_RECENT_SESSIONS сессий

Безопасность превыше всего: любая ошибка логируется и проглатывается — сбой
очистки не должен мешать запуску приложения.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
from pathlib import Path

from config.paths import BASE_DIR
from logger import logger

DAY = 86400

# ── Пороги (безопасная политика) ─────────────────────────────────────────────
SESSION_MAX_AGE_DAYS = 30      # сессии старше — кандидаты на удаление…
KEEP_RECENT_SESSIONS = 100     # …но последние N по времени всегда храним
RUNS_MAX_AGE_DAYS = 14         # subagents/
TEMP_MAX_AGE_DAYS = 7          # clipboard_images/ docx_shots/ docx_sources/ uploads/
SOCKET_MAX_AGE_DAYS = 1        # ssh_sockets/ — мёртвые сокеты
UNDO_MAX_AGE_DAYS = 60         # чужие (не текущие) undo-репы старше — удалить
MIN_INTERVAL_SECONDS = DAY     # не чаще раза в сутки

_MARKER = BASE_DIR / ".last_cleanup"


def maybe_cleanup() -> None:
    """Вызывается при старте. Запускает очистку не чаще раза в сутки."""
    try:
        if not BASE_DIR.is_dir():
            return
        if _ran_recently():
            return
        freed = run_cleanup()
        _touch_marker()
        if freed:
            logger.info("data_cleanup: freed ~%s", _human(freed))
    except Exception as e:
        logger.debug("data_cleanup.maybe_cleanup failed: %s", e, exc_info=True)


def run_cleanup() -> int:
    """Выполняет очистку. Возвращает примерно сколько байт освобождено."""
    freed = 0
    freed += _clean_root_junk()
    freed += _clean_sessions()
    freed += _clean_runs("subagents", RUNS_MAX_AGE_DAYS)
    for name in ("clipboard_images", "docx_shots", "docx_sources", "uploads"):
        freed += _clean_temp_files(name, TEMP_MAX_AGE_DAYS)
    freed += _clean_ssh_sockets()
    freed += _clean_stale_undo()
    return freed


# ── root junk ────────────────────────────────────────────────────────────────

def _clean_root_junk() -> int:
    freed = 0
    for name in (
        "_git_stats.py",
        "api_providers.json",
        "diff_target.txt",
        "docx_reference.docx",
    ):
        path = BASE_DIR / name
        if path.is_file():
            freed += _unlink(path)
    return freed

# ── sessions ─────────────────────────────────────────────────────────────────

def _clean_sessions() -> int:
    base = BASE_DIR / "sessions"
    if not base.is_dir():
        return 0
    pinned = _pinned_session_ids()
    entries = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        entries.append((mtime, child))
    # Сортируем по времени (новые первыми) и защищаем последние KEEP_RECENT.
    entries.sort(key=lambda x: x[0], reverse=True)
    protected_recent = {p for _, p in entries[:KEEP_RECENT_SESSIONS]}
    cutoff = time.time() - SESSION_MAX_AGE_DAYS * DAY
    freed = 0
    for mtime, path in entries:
        if path in protected_recent:
            continue
        if path.name in pinned:
            continue
        if mtime >= cutoff:
            continue
        freed += _rmtree(path)
    return freed


def _pinned_session_ids() -> set[str]:
    path = BASE_DIR / "pinned_sessions.json"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list):
            return {str(x) for x in data}
    except FileNotFoundError:
        return set()
    except Exception as e:
        logger.debug("data_cleanup: read pinned failed: %s", e, exc_info=True)
    return set()


# ── subagents ────────────────────────────────────────────────────────────────

def _clean_runs(name: str, max_age_days: int) -> int:
    base = BASE_DIR / name
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_days * DAY
    freed = 0
    for child in base.iterdir():
        try:
            if child.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        freed += _rmtree(child) if child.is_dir() else _unlink(child)
    return freed


# ── временные файлы (плоские каталоги) ───────────────────────────────────────

def _clean_temp_files(name: str, max_age_days: int) -> int:
    base = BASE_DIR / name
    if not base.is_dir():
        return 0
    cutoff = time.time() - max_age_days * DAY
    freed = 0
    for child in base.iterdir():
        try:
            if child.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        freed += _unlink(child) if child.is_file() else _rmtree(child)
    return freed


# ── ssh sockets ──────────────────────────────────────────────────────────────

def _clean_ssh_sockets() -> int:
    base = BASE_DIR / "ssh_sockets"
    if not base.is_dir():
        return 0
    cutoff = time.time() - SOCKET_MAX_AGE_DAYS * DAY
    freed = 0
    for child in base.iterdir():
        try:
            st = child.stat()
        except OSError:
            continue
        # Сокеты — это не обычные файлы; удаляем старые сокеты и любой мусор.
        is_socket = stat.S_ISSOCK(st.st_mode)
        if (is_socket or child.is_file()) and st.st_mtime < cutoff:
            freed += _unlink(child)
    return freed


# ── undo (git shadow-репозитории) ────────────────────────────────────────────

def _clean_stale_undo() -> int:
    base = BASE_DIR / "undo"
    if not base.is_dir():
        return 0
    current = _current_undo_key()
    cutoff = time.time() - UNDO_MAX_AGE_DAYS * DAY
    freed = 0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if child.name == current:
            continue  # активный undo текущего проекта — не трогаем
        try:
            # mtime каталога git/ как индикатор последней активности репо.
            git_dir = child / "git"
            ref = git_dir if git_dir.is_dir() else child
            if ref.stat().st_mtime >= cutoff:
                continue
        except OSError:
            continue
        freed += _rmtree(child)
    return freed


def _current_undo_key() -> str:
    try:
        workdir = os.path.abspath(os.getcwd())
        return hashlib.sha1(workdir.encode("utf-8")).hexdigest()[:12]
    except Exception:
        return ""


# ── маркер «не чаще раза в сутки» ────────────────────────────────────────────

def _ran_recently() -> bool:
    try:
        return (time.time() - _MARKER.stat().st_mtime) < MIN_INTERVAL_SECONDS
    except OSError:
        return False


def _touch_marker() -> None:
    try:
        _MARKER.write_text(str(int(time.time())), encoding="utf-8")
    except OSError as e:
        logger.debug("data_cleanup: touch marker failed: %s", e, exc_info=True)


# ── низкоуровневые удаления (с подсчётом освобождённого) ──────────────────────

def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:  # noqa: PERF203
                continue
    return total


def _rmtree(path: Path) -> int:
    try:
        size = _dir_size(path)
    except Exception:
        size = 0
    shutil.rmtree(path, ignore_errors=True)
    return 0 if path.exists() else size


def _unlink(path: Path) -> int:
    try:
        size = os.lstat(path).st_size
    except OSError:
        size = 0
    try:
        path.unlink(missing_ok=True)
        return size
    except OSError as e:
        logger.debug("data_cleanup: unlink %s failed: %s", path, e, exc_info=True)
        return 0


def _human(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.0f}{unit}" if unit == "B" else f"{val:.1f}{unit}"
        val /= 1024
    return f"{val:.1f}GB"
