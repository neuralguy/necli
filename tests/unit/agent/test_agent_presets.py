"""agent/agent_presets.py — discovery/parse пресетов субагентов из .data/agents."""

import pytest

from agent import agent_presets

@pytest.fixture
def presets_dir(isolated_data, monkeypatch):
    """Изолирует AGENTS_DIR внутри isolated_data/.data/agents."""
    d = isolated_data / "agents"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(agent_presets, "AGENTS_DIR", d, raising=False)
    return d

def _write_preset(presets_dir, name, text):
    pdir = presets_dir / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / agent_presets.PRESET_FILENAME).write_text(text, encoding="utf-8")
    return pdir

SAMPLE = """\
---
name: my-coder
description: Writes focused code changes
model: claude-opus-4-8
---

You are the CODER subagent.
Implement the requested change with minimal diff.
"""

class TestParseFrontmatter:
    def test_full_frontmatter(self):
        meta, body = agent_presets._parse_frontmatter(SAMPLE)
        assert meta["name"] == "my-coder"
        assert meta["description"] == "Writes focused code changes"
        assert meta["model"] == "claude-opus-4-8"
        assert "You are the CODER subagent." in body

    def test_no_frontmatter_returns_full_text(self):
        text = "just a body, no frontmatter\n"
        meta, body = agent_presets._parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_keys_lowercased(self):
        text = "---\nName: Foo\nMODEL: opus\n---\nbody\n"
        meta, _ = agent_presets._parse_frontmatter(text)
        assert meta["name"] == "Foo"
        assert meta["model"] == "opus"

class TestLoadPresetInfo:
    def test_full(self, presets_dir):
        pdir = _write_preset(presets_dir, "my-coder", SAMPLE)
        info = agent_presets._load_preset_info(pdir)
        assert info is not None
        assert info.name == "my-coder"
        assert info.description == "Writes focused code changes"
        assert info.model == "claude-opus-4-8"
        assert "CODER subagent" in info.body

    def test_missing_file_returns_none(self, presets_dir):
        empty = presets_dir / "empty"
        empty.mkdir()
        assert agent_presets._load_preset_info(empty) is None

    def test_name_defaults_to_dir_name(self, presets_dir):
        text = "---\ndescription: no name field\n---\nbody text\n"
        pdir = _write_preset(presets_dir, "dirname-preset", text)
        info = agent_presets._load_preset_info(pdir)
        assert info.name == "dirname-preset"

    def test_description_falls_back_to_first_paragraph(self, presets_dir):
        text = "---\nname: x\n---\nFirst paragraph here.\n\nSecond paragraph.\n"
        pdir = _write_preset(presets_dir, "x", text)
        info = agent_presets._load_preset_info(pdir)
        assert info.description == "First paragraph here."

    def test_empty_model_becomes_none(self, presets_dir):
        text = "---\nname: x\nmodel:   \n---\nbody\n"
        pdir = _write_preset(presets_dir, "x", text)
        info = agent_presets._load_preset_info(pdir)
        assert info.model is None

class TestAgentPresetProperties:
    def test_body_lazy_load(self, presets_dir):
        pdir = _write_preset(presets_dir, "lazy", SAMPLE)
        info = agent_presets.AgentPreset(
            name="lazy", description="d", path=pdir,
        )
        assert "CODER subagent" in info.body

class TestDiscoverPresets:
    def test_empty_when_dir_missing(self, isolated_data, monkeypatch):
        monkeypatch.setattr(agent_presets, "AGENTS_DIR", isolated_data / "no_agents")
        assert agent_presets.discover_presets() == []

    def test_empty_when_no_presets(self, presets_dir):
        assert agent_presets.discover_presets() == []

    def test_finds_multiple_sorted(self, presets_dir):
        _write_preset(presets_dir, "zeta", "---\nname: zeta\n---\nz body\n")
        _write_preset(presets_dir, "alpha", "---\nname: alpha\n---\na body\n")
        names = [p.name for p in agent_presets.discover_presets()]
        assert names == ["alpha", "zeta"]

    def test_skips_dirs_without_agent_md(self, presets_dir):
        _write_preset(presets_dir, "good", "---\nname: good\n---\nbody\n")
        (presets_dir / "bad").mkdir()
        names = [p.name for p in agent_presets.discover_presets()]
        assert names == ["good"]

    def test_list_presets_alias(self, presets_dir):
        _write_preset(presets_dir, "good", "---\nname: good\n---\nbody\n")
        assert [p.name for p in agent_presets.list_presets()] == ["good"]

class TestLoadPreset:
    def test_empty_name_returns_none(self, presets_dir):
        assert agent_presets.load_preset("") is None

    def test_found_by_name(self, presets_dir):
        _write_preset(presets_dir, "my-coder", SAMPLE)
        p = agent_presets.load_preset("my-coder")
        assert p is not None
        assert p.name == "my-coder"

    def test_found_by_dir_when_name_differs(self, presets_dir):
        # frontmatter name differs from dir name; lookup by dir name
        _write_preset(presets_dir, "dirkey", "---\nname: internal\n---\nbody\n")
        p = agent_presets.load_preset("dirkey")
        assert p is not None
        assert p.name == "internal"

    def test_unknown_returns_none(self, presets_dir):
        assert agent_presets.load_preset("nope") is None

class TestCreatePreset:
    def test_creates_file_and_returns_preset(self, presets_dir):
        p = agent_presets.create_preset(
            name="brand-new",
            description="A new role",
            body="Do the thing.",
            model="claude-opus-4-8",
        )
        assert p is not None
        assert p.name == "brand-new"
        assert p.description == "A new role"
        assert p.model == "claude-opus-4-8"
        assert p.body == "Do the thing."
        md = presets_dir / "brand-new" / agent_presets.PRESET_FILENAME
        assert md.exists()

    def test_created_preset_discoverable(self, presets_dir):
        agent_presets.create_preset("disc", "desc", "body")
        assert "disc" in [p.name for p in agent_presets.discover_presets()]

    def test_minimal_no_model(self, presets_dir):
        p = agent_presets.create_preset("min", "d", "b")
        assert p.model is None

class TestRemovePreset:
    def test_removes_existing(self, presets_dir):
        agent_presets.create_preset("temp", "d", "b")
        assert agent_presets.remove_preset("temp") is True
        assert agent_presets.load_preset("temp") is None

    def test_unknown_returns_false(self, presets_dir):
        assert agent_presets.remove_preset("ghost") is False

class TestBuildPresetsPrompt:
    def test_empty_when_no_presets(self, presets_dir):
        assert agent_presets.build_presets_prompt() == ""

    def test_lists_preset_name_and_description(self, presets_dir):
        agent_presets.create_preset("my-coder", "Writes code", "body")
        prompt = agent_presets.build_presets_prompt()
        assert "my-coder" in prompt
        assert "Writes code" in prompt
        assert "AVAILABLE AGENT PRESETS" in prompt

    def test_includes_model_meta_tag(self, presets_dir):
        agent_presets.create_preset(
            "tagged", "desc", "body",
            model="claude-opus-4-8",
        )
        prompt = agent_presets.build_presets_prompt()
        assert "model=claude-opus-4-8" in prompt
        assert "mode=" not in prompt
        assert "tools=" not in prompt