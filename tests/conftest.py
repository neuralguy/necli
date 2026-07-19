"""Общие фикстуры для всего тестового набора."""

import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def disable_auto_checks_by_default(monkeypatch):
    monkeypatch.setenv("NECLI_AUTO_CHECKS", "0")


@pytest.fixture
def tmp_workdir(tmp_path):
    """Подменяет рабочую директорию tools/_paths на tmp_path."""
    from tools._paths import get_working_dir, set_working_dir
    orig = get_working_dir()
    set_working_dir(str(tmp_path))
    try:
        yield tmp_path
    finally:
        set_working_dir(orig)


@pytest.fixture
def isolated_data(tmp_path, monkeypatch):
    """Изолирует config-каталоги (.data/sessions, config.json, apis.json) во временной папке."""
    import config
    from config import paths as _paths

    data_dir = tmp_path / ".data"
    sessions_dir = data_dir / "sessions"
    skills_dir = data_dir / "skills"
    config_file = data_dir / "config.json"
    apis_file = data_dir / "apis.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(_paths, "BASE_DIR", data_dir, raising=False)
    monkeypatch.setattr(_paths, "SESSIONS_DIR", sessions_dir, raising=False)
    monkeypatch.setattr(_paths, "SKILLS_DIR", skills_dir, raising=False)
    monkeypatch.setattr(_paths, "CONFIG_FILE", config_file, raising=False)
    monkeypatch.setattr(_paths, "APIS_FILE", apis_file, raising=False)
    monkeypatch.setattr(config, "SESSIONS_DIR", sessions_dir, raising=False)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file, raising=False)
    monkeypatch.setattr(config, "BASE_DIR", data_dir, raising=False)

    from config import settings as _s
    monkeypatch.setattr(_s, "CONFIG_FILE", config_file, raising=False)
    monkeypatch.setattr(_s, "_config_cache", None, raising=False)

    from apis import config as _ac
    monkeypatch.setattr(_ac, "APIS_FILE", apis_file, raising=False)
    monkeypatch.setattr(_ac, "_apis_cache", None, raising=False)
    monkeypatch.setattr(_ac, "_apis_load_failed", False, raising=False)

    yield data_dir

    _s._config_cache = None
    _ac._apis_cache = None


@pytest.fixture(autouse=True)
def clear_read_cache_between_tests():
    """Read-cache в tools/file_ops/read.py — process-global, чистим между тестами."""
    try:
        from tools.file_ops.read import _READ_CACHE
        _READ_CACHE.clear()
    except Exception:
        pass
    yield
    try:
        from tools.file_ops.read import _READ_CACHE
        _READ_CACHE.clear()
    except Exception:
        pass


@pytest.fixture
def make_tool_call():
    """Фабрика ToolCall — короткий способ построить вызов в тестах."""
    from tools.models import ToolCall

    def _factory(tool_name: str, args: dict | None = None, command: str = "", raw: str = ""):
        return ToolCall(
            command=command or tool_name,
            tool_name=tool_name,
            args=args or {},
            raw=raw,
        )

    return _factory
