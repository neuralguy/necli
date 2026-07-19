"""Снапшоты рабочей директории для /undo N (и redo через /undo -N).

Отдельный git-репозиторий (GIT_DIR в .data/undo/<hash>/git, work-tree = рабочая
директория) — НЕ трогает проектный .git. Перед каждым раундом диалога делается
коммит-снапшот текущего состояния файлов.

Модель позиции:
  - текущая ветка (HEAD) = ТЕКУЩАЯ позиция во времени.
  - ref refs/undo/tip   = верхушка всей timeline снапшотов.
/undo N  откатывает HEAD на N снапшотов назад (tip не трогаем → redo возможен).
/undo -N возвращает HEAD на N снапшотов вперёд (redo).
Новый снапшот после отката отрезает «будущее» (parent = текущий HEAD), как undo
в редакторах — после правки в прошлом redo больше недоступен.

Раунд = один пользовательский запрос + полный ответ ИИ, а не отдельный API-запрос.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_GIT_TIMEOUT = 30
_TIP_REF = "refs/undo/tip"

# Что НЕ снапшотить. Паттерны кладутся в info/exclude нашего отдельного git-dir —
# они не влияют на проектный .git.
_EXCLUDE_PATTERNS = (
    ".git/",
    ".data/",
    "logs/",
    "__pycache__/",
    "*.pyc",
    "node_modules/",
    ".venv/",
    "venv/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".necli_symlinks",
)

def _store_dir(workdir: str) -> Path:
    from config.paths import BASE_DIR
    key = hashlib.sha1(os.path.abspath(workdir).encode("utf-8")).hexdigest()[:12]
    return BASE_DIR / "undo" / key / "git"

def _git(args: list[str], workdir: str, *, check: bool = True) -> tuple[int, str, str]:
    env = dict(os.environ)
    env["GIT_DIR"] = str(_store_dir(workdir))
    env["GIT_WORK_TREE"] = os.path.abspath(workdir)
    if os.name == "nt":
        env.setdefault("PYTHONUTF8", "1")
    else:
        env.setdefault("LC_ALL", "C.UTF-8")
        env.setdefault("LANG", "C.UTF-8")
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True, timeout=_GIT_TIMEOUT, env=env,
        )
    except FileNotFoundError:
        raise RuntimeError("git CLI not found — /undo requires git")  # noqa: B904
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {args[0]} timed out after {_GIT_TIMEOUT}s")  # noqa: B904
    if check and r.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit={r.returncode}): "
            f"{(r.stderr or r.stdout or '').strip()}"
        )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()

def _ensure_store(workdir: str) -> bool:
    """Создаёт undo-репозиторий при первом обращении. Возвращает True если готов."""
    import shutil
    if shutil.which("git") is None:
        logger.warning("undo_store: git not available, /undo disabled")
        return False

    store = _store_dir(workdir)
    if (store / "HEAD").exists():
        return True

    store.mkdir(parents=True, exist_ok=True)
    try:
        _git(["init"], workdir)
        _git(["config", "user.name", "necli-undo"], workdir)
        _git(["config", "user.email", "undo@local"], workdir)
        (store / "info").mkdir(parents=True, exist_ok=True)
        (store / "info" / "exclude").write_text(
            "\n".join(_EXCLUDE_PATTERNS) + "\n", encoding="utf-8",
        )
        logger.info("undo_store: initialized at %s for %s", store, workdir)
        return True
    except Exception as e:
        logger.error("undo_store: init failed: %s", e, exc_info=True)
        return False

def snapshot_round(workdir: str, label: str = "") -> None:
    """Коммитит текущее состояние файлов как снапшот ПЕРЕД новым раундом.

    parent = текущий HEAD: если мы «в прошлом» (после undo), будущие снапшоты
    становятся недостижимыми из tip — redo после новой правки невозможен.
    Тихо ничего не делает при ошибке.
    """
    try:
        if not _ensure_store(workdir):
            return
        _git(["add", "-A"], workdir, check=False)
        _rc, out, _ = _git(["status", "--porcelain"], workdir, check=False)
        msg = (label or "round").strip()[:200] or "round"
        _git(["commit", "--allow-empty", "-m", msg], workdir, check=False)
        # Верхушка timeline = новый HEAD (отрезает старое «будущее» после undo).
        _git(["update-ref", _TIP_REF, "HEAD"], workdir, check=False)
        logger.info("undo_store: snapshot taken (%s) dirty=%s", msg, bool(out))
    except Exception as e:
        logger.error("undo_store: snapshot failed: %s", e, exc_info=True)

def cleanup_store(workdir: str) -> int:
    """Удаляет undo-репозиторий рабочей директории после завершения сессии."""
    store_root = _store_dir(workdir).parent
    if not store_root.exists():
        return 0
    try:
        size = 0
        for root, _dirs, files in os.walk(store_root, onerror=lambda _e: None):
            for file_name in files:
                try:
                    size += os.lstat(os.path.join(root, file_name)).st_size
                except OSError:  # noqa: PERF203
                    continue
        import shutil
        shutil.rmtree(store_root, ignore_errors=True)
        if not store_root.exists():
            logger.info("undo_store: cleaned %s (~%d bytes)", store_root, size)
            return size
    except Exception as e:
        logger.debug("undo_store: cleanup failed: %s", e, exc_info=True)
    return 0

def undo_rounds(workdir: str, n: int) -> tuple[bool, int, list[str]]:
    """Перемещает рабочее дерево по timeline снапшотов.

    n > 0 — откат (undo) на N раундов назад.
    n < 0 — возврат (redo) на |N| раундов вперёд.
    Возвращает (ok, moved, changed_files): moved>0 откат, moved<0 redo, 0 — край.
    ok=False если снапшотов нет.
    """
    if n == 0:
        n = 1
    store = _store_dir(workdir)
    if not (store / "HEAD").exists():
        return (False, 0, [])

    # Полная timeline снапшотов от старого к новому.
    rc, out, _ = _git(["rev-list", "--reverse", _TIP_REF], workdir, check=False)
    if rc != 0:
        # tip ещё не создан (старый стор) — fallback на HEAD
        rc, out, _ = _git(["rev-list", "--reverse", "HEAD"], workdir, check=False)
        if rc != 0:
            return (False, 0, [])
    commits = [c for c in out.splitlines() if c.strip()]
    k = len(commits)
    if k == 0:
        return (False, 0, [])

    rc, cur, _ = _git(["rev-parse", "HEAD"], workdir, check=False)
    cur = cur.strip()
    try:
        idx = commits.index(cur)
    except ValueError:
        idx = k - 1

    new_idx = idx - n
    if new_idx < 0:
        new_idx = 0
    elif new_idx > k - 1:
        new_idx = k - 1
    moved = idx - new_idx
    if moved == 0:
        return (True, 0, [])

    target = commits[new_idx]

    # Файлы, которые изменятся при переходе (в любую сторону).
    lo, hi = (target, cur) if moved > 0 else (cur, target)
    rc, diff_out, _ = _git(["diff", "--name-only", f"{lo}..{hi}"], workdir, check=False)
    changed = [f for f in diff_out.splitlines() if f.strip()] if rc == 0 else []
    rc, unt_out, _ = _git(
        ["ls-files", "--others", "--exclude-standard"], workdir, check=False,
    )
    if rc == 0:
        for f in unt_out.splitlines():
            if f.strip() and f not in changed:
                changed.append(f)

    # check=False как у соседних git-вызовов: ошибку логируем, но не роняем
    # вызывающий код жёстким исключением посреди отката.
    rc_reset, _, err_reset = _git(["reset", "--hard", target], workdir, check=False)
    if rc_reset != 0:
        logger.error("undo_store: reset --hard %s failed: %s", target[:8], err_reset)
    _git(["clean", "-fd"], workdir, check=False)
    logger.info(
        "undo_store: moved %d (idx %d→%d) to %s, %d file(s)",
        moved, idx, new_idx, target[:8], len(changed),
    )
    return (True, moved, changed)
