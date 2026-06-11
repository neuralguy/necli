"""config/ui.py — UI-кастомизация через .data/ui.json."""

import json
import sys

import pytest

import config.ui  # noqa: F401  ensures sys.modules has the real module
from config.ui import DEFAULTS, UIConfig, _deep_merge

ui_mod = sys.modules["config.ui"]

@pytest.fixture
def ui_file(isolated_data, monkeypatch):
    """Изолирует UI_FILE и свежий UIConfig поверх isolated_data."""
    path = isolated_data / "ui.json"
    monkeypatch.setattr(ui_mod, "UI_FILE", path)
    return path

@pytest.fixture
def cfg(ui_file):
    return UIConfig()

class TestDeepMerge:
    def test_override_scalar(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_add_new_key(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_merge(self):
        base = {"x": {"a": 1, "b": 2}}
        over = {"x": {"b": 3, "c": 4}}
        assert _deep_merge(base, over) == {"x": {"a": 1, "b": 3, "c": 4}}

    def test_does_not_mutate_inputs(self):
        base = {"x": {"a": 1}}
        over = {"x": {"b": 2}}
        _deep_merge(base, over)
        assert base == {"x": {"a": 1}}
        assert over == {"x": {"b": 2}}

class TestToolLookup:
    def test_known_tool_emoji_label_color(self, cfg):
        t = cfg.tool("shell")
        assert t["label"] == "Shell"
        assert t["emoji"] == "⏺"
        assert t["color_role"] == "warning"

    def test_read_files_tool(self, cfg):
        t = cfg.tool("read_files")
        assert t["label"] == "Read"
        assert t["emoji"] == "📖"

    def test_unknown_tool_falls_back_to_default(self, cfg):
        t = cfg.tool("no_such_tool_xyz")
        assert t == DEFAULTS["tools"]["_default"]
        assert t["label"] == "Tool"

class TestGet:
    def test_dotted_path(self, cfg):
        assert cfg.get("tools.shell.emoji") == "⏺"

    def test_missing_returns_default(self, cfg):
        assert cfg.get("tools.shell.nope", "fallback") == "fallback"

    def test_missing_top_level(self, cfg):
        assert cfg.get("does.not.exist") is None

    def test_traverse_into_nondict_returns_default(self, cfg):
        # limits.max_width is an int → drilling further must hit default
        assert cfg.get("limits.max_width.deeper", "d") == "d"

    def test_limits_value(self, cfg):
        assert cfg.get("limits.compact_preview_lines") == 8

class TestMaxConcurrencyDefault:
    def test_default(self, cfg):
        assert cfg.get("subagent.max_concurrency") == 12

    def test_other_subagent_defaults(self, cfg):
        assert cfg.get("subagent.block_threshold") == 5
        assert cfg.get("subagent.header_emoji") == "🤖"

class TestOverridePrecedence:
    def test_user_file_overrides_default(self, ui_file, cfg):
        ui_file.write_text(
            json.dumps({"tools": {"shell": {"emoji": "💥", "color_role": "error"}}}),
            encoding="utf-8",
        )
        t = cfg.tool("shell")
        assert t["emoji"] == "💥"
        assert t["color_role"] == "error"
        # label not overridden → inherited from defaults via deep merge
        assert t["label"] == "Shell"

    def test_user_override_limit(self, ui_file, cfg):
        ui_file.write_text(
            json.dumps({"limits": {"max_width": 200}}), encoding="utf-8"
        )
        assert cfg.get("limits.max_width") == 200
        # untouched sibling stays default
        assert cfg.get("limits.compact_preview_lines") == 8

    def test_user_override_max_concurrency(self, ui_file, cfg):
        ui_file.write_text(
            json.dumps({"subagent": {"max_concurrency": 3}}), encoding="utf-8"
        )
        assert cfg.get("subagent.max_concurrency") == 3

    def test_new_default_key_appears_alongside_user_data(self, ui_file, cfg):
        # user file lacks subagent.max_concurrency → still merged from DEFAULTS
        ui_file.write_text(
            json.dumps({"subagent": {"header_emoji": "👾"}}), encoding="utf-8"
        )
        assert cfg.get("subagent.header_emoji") == "👾"
        assert cfg.get("subagent.max_concurrency") == 12

class TestFileGeneration:
    def test_writes_defaults_when_missing(self, ui_file, cfg):
        assert not ui_file.exists()
        cfg.get("limits.max_width")
        assert ui_file.exists()
        on_disk = json.loads(ui_file.read_text(encoding="utf-8"))
        assert on_disk["limits"]["max_width"] == 100

    def test_invalid_json_falls_back_to_defaults(self, ui_file, cfg):
        ui_file.write_text("{ not valid json", encoding="utf-8")
        assert cfg.get("limits.max_width") == 100

    def test_non_dict_json_falls_back_to_defaults(self, ui_file, cfg):
        ui_file.write_text("[1, 2, 3]", encoding="utf-8")
        assert cfg.get("limits.max_width") == 100

    def test_reload_picks_up_changes(self, ui_file, cfg):
        assert cfg.get("limits.max_width") == 100
        ui_file.write_text(
            json.dumps({"limits": {"max_width": 77}}), encoding="utf-8"
        )
        # cached → old value until reload
        assert cfg.get("limits.max_width") == 100
        cfg.reload()
        assert cfg.get("limits.max_width") == 77

class TestMcpDisplay:
    def test_substitutes_server_and_tool(self, cfg):
        d = cfg.mcp_display("myserver", "mytool")
        assert d["label"] == "myserver.mytool"
        assert d["emoji"] == "🔌"
        assert d["color_role"] == "magenta"