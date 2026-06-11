"""config/paths.py — резолв путей и директорий."""

import sys
from pathlib import Path


from config import paths

class TestResolveBaseDir:
    def test_env_override(self, monkeypatch, tmp_path):
        target = tmp_path / "custom_home"
        monkeypatch.setenv("NECLI_HOME", str(target))
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert paths._resolve_base_dir() == target.resolve()

    def test_env_override_expands_user(self, monkeypatch):
        monkeypatch.setenv("NECLI_HOME", "~/necli_test_home")
        result = paths._resolve_base_dir()
        assert "~" not in str(result)
        assert result.is_absolute()

    def test_frozen_uses_home_necli(self, monkeypatch):
        monkeypatch.delenv("NECLI_HOME", raising=False)
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert paths._resolve_base_dir() == Path.home() / ".necli"

    def test_source_uses_dotdata(self, monkeypatch):
        monkeypatch.delenv("NECLI_HOME", raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        result = paths._resolve_base_dir()
        assert result.name == ".data"
        assert result.is_absolute()

class TestResourcePath:
    def test_no_meipass_uses_project_root(self, monkeypatch):
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        result = paths.resource_path("skills", "x.md")
        assert result.parts[-2:] == ("skills", "x.md")
        assert result.is_absolute()

    def test_meipass_used_when_frozen(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        result = paths.resource_path("_bundle", "agents")
        assert result == tmp_path / "_bundle" / "agents"

    def test_no_parts_returns_base(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
        assert paths.resource_path() == tmp_path

class TestDerivedConstants:
    def test_layout(self):
        assert paths.SESSIONS_DIR == paths.BASE_DIR / "sessions"
        assert paths.SKILLS_DIR == paths.BASE_DIR / "skills"
        assert paths.CONFIG_FILE == paths.BASE_DIR / "config.json"
        assert paths.APIS_FILE == paths.BASE_DIR / "apis.json"
        assert paths.UI_FILE == paths.BASE_DIR / "ui.json"

class TestEnsureDirs:
    def test_creates_dirs(self, monkeypatch, tmp_path):
        base = tmp_path / ".data"
        monkeypatch.setattr(paths, "BASE_DIR", base, raising=False)
        monkeypatch.setattr(paths, "SESSIONS_DIR", base / "sessions", raising=False)
        monkeypatch.setattr(paths, "SKILLS_DIR", base / "skills", raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        paths.ensure_dirs()
        assert base.is_dir()
        assert (base / "sessions").is_dir()
        assert (base / "skills").is_dir()

    def test_seed_bundled_noop_when_not_frozen(self, monkeypatch):
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        # must not raise
        paths._seed_bundled("skills")