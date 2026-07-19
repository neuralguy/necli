"""config/constants.py — IGNORE_DIRS, READ_ONLY_TOOLS, is_ignored_dir, env-настройки."""

from config import constants
from config.constants import (
    IGNORE_DIRS,
    READ_ONLY_TOOLS,
    RESPONSE_TIMEOUT,
    TARGET_MODEL,
    is_ignored_dir,
)


class TestIgnoreDirs:
    def test_is_frozenset(self):
        assert isinstance(IGNORE_DIRS, frozenset)

    def test_contains_common_dirs(self):
        for name in (".git", "__pycache__", "node_modules", ".venv", ".data", "logs"):
            assert name in IGNORE_DIRS

    def test_no_empty_entries(self):
        assert all(d for d in IGNORE_DIRS)

class TestIsIgnoredDir:
    def test_explicit_name(self):
        assert is_ignored_dir(".git") is True
        assert is_ignored_dir("node_modules") is True

    def test_egg_info_pattern(self):
        assert is_ignored_dir("mypkg.egg-info") is True

    def test_not_ignored(self):
        assert is_ignored_dir("src") is False
        assert is_ignored_dir("config") is False

    def test_partial_match_not_ignored(self):
        assert is_ignored_dir("git") is False
        assert is_ignored_dir("my_node_modules") is False

class TestReadOnlyTools:
    def test_is_frozenset(self):
        assert isinstance(READ_ONLY_TOOLS, frozenset)

    def test_contains_read_tools(self):
        for name in ("read_files",):
            assert name in READ_ONLY_TOOLS

    def test_contains_lsp_tools(self):
        for name in ("lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics"):
            assert name in READ_ONLY_TOOLS

    def test_excludes_write_tools(self):
        for name in ("create_file", "patch_file", "shell"):
            assert name not in READ_ONLY_TOOLS

class TestEnvSettings:
    def test_response_timeout_positive_int(self):
        assert isinstance(RESPONSE_TIMEOUT, int)
        assert RESPONSE_TIMEOUT > 0

    def test_target_model_nonempty_str(self):
        assert isinstance(TARGET_MODEL, str)
        assert TARGET_MODEL

    def test_module_exposes_get(self):
        assert hasattr(constants, "get")
