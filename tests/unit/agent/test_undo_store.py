"""agent/undo_store.py — снапшоты рабочей директории для /undo."""

import shutil
from pathlib import Path

import pytest

from agent import undo_store

_HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not _HAS_GIT, reason="git CLI not available")

@pytest.fixture
def undo_env(tmp_path, monkeypatch):
    """Изолирует BASE_DIR (стор undo) и даёт рабочую директорию."""
    from config import paths as _paths
    base = tmp_path / ".data"
    base.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_paths, "BASE_DIR", base, raising=False)

    work = tmp_path / "work"
    work.mkdir()
    return str(work)

def _write(workdir: str, name: str, content: str) -> None:
    Path(workdir, name).write_text(content, encoding="utf-8")

def _read(workdir: str, name: str) -> str:
    return Path(workdir, name).read_text(encoding="utf-8")

class TestStoreDir:
    def test_deterministic_key(self, undo_env):
        a = undo_store._store_dir(undo_env)
        b = undo_store._store_dir(undo_env)
        assert a == b

    def test_different_workdir_different_key(self, undo_env, tmp_path):
        other = tmp_path / "other"
        other.mkdir()
        assert undo_store._store_dir(undo_env) != undo_store._store_dir(str(other))

class TestEnsureStore:
    def test_initializes_repo(self, undo_env):
        assert undo_store._ensure_store(undo_env) is True
        store = undo_store._store_dir(undo_env)
        assert (store / "HEAD").exists()
        assert (store / "info" / "exclude").exists()

    def test_idempotent(self, undo_env):
        assert undo_store._ensure_store(undo_env) is True
        assert undo_store._ensure_store(undo_env) is True

    def test_exclude_patterns_written(self, undo_env):
        undo_store._ensure_store(undo_env)
        excl = (undo_store._store_dir(undo_env) / "info" / "exclude").read_text()
        assert ".data/" in excl
        assert "__pycache__/" in excl

class TestSnapshotRound:
    def test_creates_commit(self, undo_env):
        _write(undo_env, "a.txt", "v1")
        undo_store.snapshot_round(undo_env, "first")
        rc, out, _ = undo_store._git(["rev-list", "--count", "HEAD"], undo_env, check=False)
        assert rc == 0
        assert int(out) == 1

    def test_tip_ref_updated(self, undo_env):
        _write(undo_env, "a.txt", "v1")
        undo_store.snapshot_round(undo_env, "first")
        rc, head, _ = undo_store._git(["rev-parse", "HEAD"], undo_env, check=False)
        rc2, tip, _ = undo_store._git(["rev-parse", undo_store._TIP_REF], undo_env, check=False)
        assert rc == 0 and rc2 == 0
        assert head == tip

    def test_multiple_snapshots_accumulate(self, undo_env):
        for i in range(3):
            _write(undo_env, "a.txt", f"v{i}")
            undo_store.snapshot_round(undo_env, f"r{i}")
        _rc, out, _ = undo_store._git(["rev-list", "--count", undo_store._TIP_REF], undo_env, check=False)
        assert int(out) == 3

    def test_no_workdir_no_crash(self, tmp_path, monkeypatch):
        # git недоступен → snapshot тихо выходит
        monkeypatch.setattr(shutil, "which", lambda _: None)
        from config import paths as _paths
        monkeypatch.setattr(_paths, "BASE_DIR", tmp_path / ".data", raising=False)
        work = tmp_path / "w"
        work.mkdir()
        undo_store.snapshot_round(str(work), "x")  # не должно бросать

class TestUndoRounds:
    def _three_snaps(self, workdir):
        for i in range(3):
            _write(workdir, "a.txt", f"v{i}")
            undo_store.snapshot_round(workdir, f"r{i}")

    def test_no_snapshots_returns_false(self, undo_env):
        ok, moved, changed = undo_store.undo_rounds(undo_env, 1)
        assert ok is False
        assert moved == 0
        assert changed == []

    def test_undo_one_restores_previous(self, undo_env):
        self._three_snaps(undo_env)
        assert _read(undo_env, "a.txt") == "v2"
        ok, moved, changed = undo_store.undo_rounds(undo_env, 1)
        assert ok is True
        assert moved == 1
        assert _read(undo_env, "a.txt") == "v1"
        assert "a.txt" in changed

    def test_undo_multiple(self, undo_env):
        self._three_snaps(undo_env)
        ok, moved, _ = undo_store.undo_rounds(undo_env, 2)
        assert ok is True
        assert moved == 2
        assert _read(undo_env, "a.txt") == "v0"

    def test_undo_clamped_at_oldest(self, undo_env):
        self._three_snaps(undo_env)
        ok, moved, _ = undo_store.undo_rounds(undo_env, 99)
        assert ok is True
        assert moved == 2  # только 2 шага назад от верхушки доступно
        assert _read(undo_env, "a.txt") == "v0"

    def test_redo_after_undo(self, undo_env):
        self._three_snaps(undo_env)
        undo_store.undo_rounds(undo_env, 2)
        assert _read(undo_env, "a.txt") == "v0"
        ok, moved, _ = undo_store.undo_rounds(undo_env, -1)
        assert ok is True
        assert moved == -1
        assert _read(undo_env, "a.txt") == "v1"

    def test_at_edge_no_move(self, undo_env):
        self._three_snaps(undo_env)
        # уже на верхушке → redo невозможен
        ok, moved, changed = undo_store.undo_rounds(undo_env, -1)
        assert ok is True
        assert moved == 0
        assert changed == []

    def test_n_zero_defaults_to_one(self, undo_env):
        self._three_snaps(undo_env)
        ok, moved, _ = undo_store.undo_rounds(undo_env, 0)
        assert ok is True
        assert moved == 1
        assert _read(undo_env, "a.txt") == "v1"

    def test_new_snapshot_cuts_future(self, undo_env):
        self._three_snaps(undo_env)
        undo_store.undo_rounds(undo_env, 2)  # вернулись к v0
        _write(undo_env, "a.txt", "branch")
        undo_store.snapshot_round(undo_env, "new")
        # redo теперь недоступен — мы на новой верхушке
        ok, moved, _ = undo_store.undo_rounds(undo_env, -1)
        assert ok is True
        assert moved == 0
