"""Git worktree-based изоляция для субагентов.

Каждый субагент работает в отдельном git worktree на отдельной ветке.
После завершения главный агент видит ветки и руками делает merge.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


_GIT_TIMEOUT = 30
# ВАЖНО: симлинкуем ТОЛЬКО read/execute-каталоги (venv, node_modules).
# `.data` и `logs` симлинковать НЕЛЬЗЯ — субагент пишет туда по относительным
# путям, а `resolve_path` идёт через realpath() и следует за симлинком, в
# итоге запись утекает в main worktree. Если в .data/logs нужны общие
# артефакты — пусть субагент создаёт их у себя в worktree, потом
# orchestrator решает что мержить.
_SYMLINK_TARGETS = (".venv", "venv", "node_modules")


class GitError(RuntimeError):
    """Любая git-операция, которая не удалась. Сообщение — для пользователя."""


@dataclass
class WorktreeHandle:
    sub_idx: int
    branch: str
    path: str
    base_sha: str
    run_id: str
    files_changed: list[str] = field(default_factory=list)
    diff_stat: str = ""
    commit_sha: str = ""
    commits_count: int = 0
    has_changes: bool = False


def _run_git(
    args: list[str],
    cwd: str,
    check: bool = True,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """subprocess.run('git', *args). Возвращает (rc, stdout, stderr).

    env_extra: доп. переменные окружения (напр. GIT_INDEX_FILE для
    операций над временным индексом без правки реального).
    """
    env = dict(os.environ)
    if sys.platform == "win32":
        env["PYTHONUTF8"] = "1"
    else:
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
    if env_extra:
        env.update(env_extra)
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
            env=env,
        )
    except FileNotFoundError as e:
        raise GitError(
            "git CLI not found on system. Install git to use subagents."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {args[0]} timed out after {_GIT_TIMEOUT}s") from e

    if check and r.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit={r.returncode}):\n"
            f"{(r.stderr or r.stdout or '').strip()}"
        )
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def git_available() -> bool:
    return shutil.which("git") is not None


def is_git_repo(root: str) -> bool:
    if not git_available():
        return False
    rc, _, _ = _run_git(["rev-parse", "--is-inside-work-tree"], cwd=root, check=False)
    return rc == 0


def _get_head_sha(root: str) -> Optional[str]:
    rc, out, _ = _run_git(["rev-parse", "HEAD"], cwd=root, check=False)
    if rc != 0:
        return None
    return out.strip() or None


def ensure_git_repo(root: str) -> str:
    """Гарантирует git-репозиторий с минимум одним коммитом.

    Если репо нет — init + initial commit. Если репо есть, но нет коммитов —
    делает initial commit. Возвращает HEAD sha. Бросает GitError при сбое.
    """
    if not git_available():
        raise GitError(
            "git CLI not found on system. Subagents require git for isolation.\n"
            "Install: apt install git / brew install git / choco install git"
        )

    root_p = Path(root).resolve()
    if not root_p.is_dir():
        raise GitError(f"Working directory does not exist: {root}")

    if not (root_p / ".git").exists() and not is_git_repo(str(root_p)):
        logger.info("subagent_git: initializing git repo at %s", root_p)
        _run_git(["init"], cwd=str(root_p))
        # минимальный конфиг — если нет user.name/email, commit упадёт
        rc, name, _ = _run_git(["config", "user.name"], cwd=str(root_p), check=False)
        if rc != 0 or not name:
            _run_git(["config", "user.name", "necli-agent"], cwd=str(root_p))
        rc, email, _ = _run_git(["config", "user.email"], cwd=str(root_p), check=False)
        if rc != 0 or not email:
            _run_git(["config", "user.email", "necli@local"], cwd=str(root_p))

    head = _get_head_sha(str(root_p))
    if head is None:
        # репо без коммитов — делаем initial
        logger.info("subagent_git: no commits in repo, making initial commit")
        _run_git(["add", "-A"], cwd=str(root_p), check=False)
        # commit может быть пустым (если ничего не tracked-able) — допустим
        _run_git(
            ["commit", "--allow-empty", "-m", "necli: initial commit for subagents"],
            cwd=str(root_p),
        )
        head = _get_head_sha(str(root_p))
        if head is None:
            raise GitError("Failed to create initial commit")
    return head


def _link_runtime_target(src: Path, dst: Path) -> bool:
    try:
        os.symlink(os.path.join(str(src.parent), src.name), dst, target_is_directory=src.is_dir())
        return True
    except OSError as e:
        if sys.platform != "win32" or not src.is_dir():
            logger.warning("subagent_git: symlink %s failed: %s", dst, e)
            return False
    try:
        r = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(dst), str(src)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode == 0:
            logger.debug("subagent_git: junction %s -> %s", dst, src)
            return True
        logger.warning("subagent_git: junction %s failed: %s", dst, (r.stderr or r.stdout).strip())
    except Exception as e:
        logger.warning("subagent_git: junction %s failed: %s", dst, e)
    return False



def _setup_symlinks(worktree: Path, root: Path) -> None:
    """Создаёт симлинки для _SYMLINK_TARGETS (.venv/venv/node_modules) из корня
    в worktree. .git/info/exclude НЕ трогаем (см. ниже почему).

    Симлинки нужны чтобы субагент мог запускать тесты/линтеры (`.venv`). Они НЕ
    должны коммититься — иначе main-repo после merge получит симлинк-блобы.
    Поэтому имена созданных линков сохраняются в worktree/.necli_symlinks, а
    commit_worktree снимает их из индекса (`git rm --cached`) перед коммитом.
    """
    # ВНИМАНИЕ: info/exclude общий для всего репо — туда писать нельзя.
    # Любой паттерн `.venv` оттуда заэкскьюдит всё `.venv/...` И в main, и в
    # любом worktree. И — что критичнее — паттерн вроде `.data` сматчит
    # `.data/test_subagent/...` файлы субагента, и `git add -A` их пропустит,
    # коммит выйдет пустой, изменения исчезнут вместе с cleanup.
    #
    # Поэтому: НЕ трогаем .git/info/exclude. Целевые симлинки (.venv,
    # node_modules) и так в проектном .gitignore — а если кто-то их забыл
    # добавить, симлинк на каталог попадёт в коммит как 120000-mode объект,
    # но это безвредно (мы НЕ мержим эти ветки автоматически).

    # Сами симлинки. Имена созданных линков сохраняем в worktree/.necli_symlinks;
    # commit_worktree вычищает их из индекса через `git rm --cached` прямо перед
    # коммитом, чтобы симлинк-блобы не попали в ветку субагента.
    created: list[str] = []
    for name in _SYMLINK_TARGETS:
        src = root / name
        if not src.exists():
            continue
        dst = worktree / name
        if dst.exists() or dst.is_symlink():
            continue
        if _link_runtime_target(src, dst):
            logger.debug("subagent_git: runtime link %s -> %s", dst, src)
            created.append(name)
    # Сохраняем список созданных симлинков рядом с worktree для commit_worktree.
    if created:
        try:
            (worktree / ".necli_symlinks").write_text(
                "\n".join(created) + "\n", encoding="utf-8",
            )
        except OSError as e:
            logger.warning("subagent_git: marker write failed: %s", e)


def create_worktree(
    root: str,
    sub_idx: int,
    run_id: str,
    base_sha: str,
) -> WorktreeHandle:
    """Создаёт worktree для одного субагента.

    Путь: <root>/.data/subagents/<run_id>/sub-<N>/
    Ветка: subagent/<run_id>-<N>
    """
    root_p = Path(root).resolve()
    wt_dir = root_p / ".data" / "subagents" / run_id / f"sub-{sub_idx + 1}"
    wt_dir.parent.mkdir(parents=True, exist_ok=True)

    # если каталог уже существует (повтор run_id) — снести
    if wt_dir.exists():
        _run_git(
            ["worktree", "remove", "--force", str(wt_dir)],
            cwd=str(root_p),
            check=False,
        )
        if wt_dir.exists():
            shutil.rmtree(wt_dir, ignore_errors=True)

    branch = f"subagent/{run_id}-{sub_idx + 1}"

    # На случай висящей ветки от предыдущего прогона — удалим
    _run_git(["branch", "-D", branch], cwd=str(root_p), check=False)

    _run_git(
        ["worktree", "add", "-b", branch, str(wt_dir), base_sha],
        cwd=str(root_p),
    )
    _setup_symlinks(wt_dir, root_p)

    logger.info(
        "subagent_git: created worktree sub=%d path=%s branch=%s base=%s",
        sub_idx + 1, wt_dir, branch, base_sha[:8],
    )
    return WorktreeHandle(
        sub_idx=sub_idx,
        branch=branch,
        path=str(wt_dir),
        base_sha=base_sha,
        run_id=run_id,
    )


def commit_worktree(handle: WorktreeHandle, message: str) -> None:
    """`git add -Af && git commit -m ...` внутри worktree.

    -A: все изменения (новые/изменённые/удалённые).
    -f: ИГНОРИРУЕМ gitignore. Это критично — проектный .gitignore обычно
    содержит .data/, logs/, __pycache__/, .venv/. Субагенту разрешено писать
    в эти места (например артефакты тестов, временные файлы) и эти изменения
    ОБЯЗАНЫ попасть в коммит на ветке субагента, иначе при cleanup worktree
    они исчезнут навсегда, а отчёт покажет "no changes" при наличии работы.
    Мусор-каталоги (__pycache__) — приемлемая цена; они видны в diff и юзер
    решит, мержить их или нет.

    Если изменений всё-таки нет — фиксирует has_changes=False.
    """
    wt = handle.path
    # check=True (в отличие от соседних check=False): force-add ОБЯЗАН пройти —
    # если git add упадёт, работа субагента не закоммитится и исчезнет при
    # cleanup, поэтому сбой надо немедленно поднять как GitError.
    _run_git(["add", "-A", "-f"], cwd=wt)

    # Снимаем из индекса симлинки, которые мы сами повесили (.venv/node_modules/...).
    # Они нужны субагенту в рантайме, но в коммит лезть не должны.
    marker = Path(wt) / ".necli_symlinks"
    if marker.is_file():
        try:
            names = [n.strip() for n in marker.read_text(encoding="utf-8").splitlines() if n.strip()]
        except OSError:
            names = []
        for n in names:
            _run_git(["rm", "--cached", "-f", "--ignore-unmatch", n], cwd=wt, check=False)
        # сам marker тоже не нужен в коммите
        _run_git(["rm", "--cached", "-f", "--ignore-unmatch", ".necli_symlinks"], cwd=wt, check=False)

    # Проверяем именно STAGED изменения (что реально попадёт в коммит), а не
    # `git status --porcelain`: последний показывает и untracked `.necli_symlinks`,
    # который мы только что сняли из индекса (git rm --cached). У read-only
    # субагента индекс пуст, но untracked marker оставался бы в porcelain →
    # has_changes=True → git commit падает с "nothing to commit" (exit=1).
    rc, out, _ = _run_git(
        ["diff", "--cached", "--name-only"], cwd=wt, check=False,
    )
    if rc != 0 or not out.strip():
        handle.has_changes = False
        logger.info(
            "subagent_git: sub=%d branch=%s — no changes",
            handle.sub_idx + 1, handle.branch,
        )
        return

    handle.has_changes = True
    safe_msg = (message or "subagent work").strip()
    if len(safe_msg) > 200:
        safe_msg = safe_msg[:197] + "..."
    _run_git(["commit", "-m", safe_msg], cwd=wt)


def summarize_changes(handle: WorktreeHandle, root: str) -> None:
    """Заполняет handle.files_changed/diff_stat/commit_sha/commits_count."""
    wt = handle.path
    head_sha = _get_head_sha(wt) or ""
    handle.commit_sha = head_sha

    # количество коммитов от base
    rc, out, _ = _run_git(
        ["rev-list", "--count", f"{handle.base_sha}..HEAD"],
        cwd=wt, check=False,
    )
    if rc == 0 and out.strip().isdigit():
        handle.commits_count = int(out.strip())

    # список файлов
    rc, out, _ = _run_git(
        ["diff", "--name-only", f"{handle.base_sha}..HEAD"],
        cwd=wt, check=False,
    )
    if rc == 0:
        handle.files_changed = [ln for ln in out.splitlines() if ln.strip()]

    # diff stat
    rc, out, _ = _run_git(
        ["diff", "--stat", f"{handle.base_sha}..HEAD"],
        cwd=wt, check=False,
    )
    if rc == 0:
        handle.diff_stat = out.strip()


def summarize_worktree_changes(handle: WorktreeHandle) -> None:
    """Inspect uncommitted worktree changes without mutating the real index."""
    wt = handle.path
    handle.commit_sha = _get_head_sha(wt) or ""
    fd, index_path = tempfile.mkstemp(prefix="necli-subagent-index-")
    os.close(fd)
    try:
        os.unlink(index_path)
    except OSError:
        pass
    env = {"GIT_INDEX_FILE": index_path}
    try:
        rc, _, _ = _run_git(["read-tree", "HEAD"], cwd=wt, check=False, env_extra=env)
        if rc != 0:
            return
        _run_git(["add", "-A", "-f"], cwd=wt, check=False, env_extra=env)

        marker = Path(wt) / ".necli_symlinks"
        if marker.is_file():
            try:
                names = [n.strip() for n in marker.read_text(encoding="utf-8").splitlines() if n.strip()]
            except OSError:
                names = []
            for n in names:
                _run_git(["rm", "--cached", "-f", "--ignore-unmatch", n], cwd=wt, check=False, env_extra=env)
            _run_git(["rm", "--cached", "-f", "--ignore-unmatch", ".necli_symlinks"], cwd=wt, check=False, env_extra=env)

        rc, out, _ = _run_git(["diff", "--cached", "--name-only"], cwd=wt, check=False, env_extra=env)
        if rc == 0:
            handle.files_changed = [ln for ln in out.splitlines() if ln.strip()]
        rc, out, _ = _run_git(["diff", "--cached", "--stat"], cwd=wt, check=False, env_extra=env)
        if rc == 0:
            handle.diff_stat = out.strip()
        handle.has_changes = bool(handle.files_changed)
    finally:
        try:
            os.unlink(index_path)
        except OSError:
            pass


def _remove_windows_junctions(path: Path) -> None:
    marker = path / ".necli_symlinks"
    if not marker.is_file():
        return
    try:
        names = [n.strip() for n in marker.read_text(encoding="utf-8").splitlines() if n.strip()]
    except OSError:
        return
    for name in names:
        target = path / name
        if not target.exists():
            continue
        try:
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                subprocess.run(["cmd", "/c", "rmdir", str(target)], capture_output=True, timeout=10)
        except Exception:
            logger.debug("subagent_git: runtime link cleanup failed: %s", target, exc_info=True)

def cleanup_worktree(root: str, handle: WorktreeHandle) -> None:
    """Удаляет worktree (но НЕ ветку — её главный агент может ещё merge'ить).

    После удаления sub-N каталога пытается удалить родительский run-id
    каталог, если он стал пустым.
    """
    _run_git(
        ["worktree", "remove", "--force", handle.path],
        cwd=root, check=False,
    )
    # если git не удалил каталог (например симлинки помешали) — добиваем вручную
    wt_path = Path(handle.path)
    if wt_path.exists():
        if sys.platform == "win32":
            _remove_windows_junctions(wt_path)
        shutil.rmtree(wt_path, ignore_errors=True)

    # Снимаем пустой родительский каталог run-id, чтобы не оставлять мусор
    # в .data/subagents/. Если там ещё что-то лежит (параллельный sub ещё
    # не закончил уборку) — rmdir мирно упадёт, мы это игнорим.
    parent = wt_path.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


_STALE_BRANCH_AGE = 7200  # 2 часа в секундах

def cleanup_stale_branches(root: str, current_run_id: Optional[str] = None) -> int:
    """Удаляет ТОЛЬКО устаревшие ветки subagent/*.

    Удаляем ветку, только если её последний коммит старше _STALE_BRANCH_AGE
    (2 часа). Это защищает несмерженные ветки параллельных/недавних прогонов
    от случайного сноса. Ветки текущего прогона (current_run_id) не трогаем
    никогда. Текущая активная ветка (HEAD) тоже пропускается.

    Возвращает количество удалённых веток.
    """
    if not is_git_repo(root):
        return 0

    rc, current, _ = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=root, check=False)
    current = current.strip() if rc == 0 else ""

    rc, out, _ = _run_git(
        [
            "for-each-ref",
            "--format=%(refname:short) %(committerdate:unix)",
            "refs/heads/subagent/",
        ],
        cwd=root, check=False,
    )
    if rc != 0 or not out.strip():
        return 0

    now = time.time()
    run_marker = f"subagent/{current_run_id}-" if current_run_id else None
    removed = 0
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.rsplit(" ", 1)
        branch = parts[0].strip()
        try:
            ctime = float(parts[1]) if len(parts) == 2 else 0.0
        except ValueError:
            ctime = 0.0
        if not branch:
            continue
        if branch == current:
            logger.info("subagent_git: skip active branch %s", branch)
            continue
        if run_marker and branch.startswith(run_marker):
            logger.info("subagent_git: skip current-run branch %s", branch)
            continue
        if now - ctime < _STALE_BRANCH_AGE:
            logger.info("subagent_git: keep recent branch %s", branch)
            continue
        rc, _, err = _run_git(["branch", "-D", branch], cwd=root, check=False)
        if rc == 0:
            removed += 1
        else:
            logger.warning("subagent_git: failed to delete %s: %s", branch, err)
    if removed:
        logger.info("subagent_git: cleaned up %d stale subagent branches", removed)
    return removed


def gen_run_id() -> str:
    """Короткий уникальный id для имён worktree/веток. timestamp + uuid4 prefix."""
    ts = time.strftime("%Y%m%d-%H%M%S")
    return f"{ts}-{uuid.uuid4().hex[:6]}"