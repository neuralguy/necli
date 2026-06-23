"""skills/manager.py — обнаружение, загрузка, активация скиллов."""

import pytest

from skills import manager


@pytest.fixture(autouse=True)
def _isolated(isolated_data, monkeypatch):
    skills_dir = isolated_data / "skills"
    skills_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(manager, "DEFAULT_SKILLS_DIR", skills_dir)
    monkeypatch.setattr(manager, "USER_SKILLS_DIR", skills_dir)
    manager.reset_active_skills()
    yield
    manager.reset_active_skills()


def _make_skill(skills_dir, name, description="desc", body="content"):
    sd = skills_dir / name
    sd.mkdir()
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return sd


class TestParseFrontmatter:
    def test_basic(self):
        text = "---\nname: foo\ndescription: bar\n---\n\nbody here"
        meta, body = manager._parse_frontmatter(text)
        assert meta == {"name": "foo", "description": "bar"}
        # regex matches '\n---\s*\n' → захватывает один \n, остаётся "\nbody here" или "body here"
        assert body.strip() == "body here"

    def test_no_frontmatter(self):
        meta, body = manager._parse_frontmatter("just body")
        assert meta == {}
        assert body == "just body"

    def test_disable_model_invocation_true(self):
        text = "---\nname: x\ndisable-model-invocation: true\n---\nbody"
        meta, _ = manager._parse_frontmatter(text)
        assert meta["disable-model-invocation"] == "true"


class TestDiscoverSkills:
    def test_empty_directory(self):
        assert manager.discover_skills() == []

    def test_finds_skills(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo", description="X")
        _make_skill(isolated_data / "skills", "bar", description="Y")
        skills = manager.discover_skills()
        names = [s.name for s in skills]
        assert "foo" in names
        assert "bar" in names

    def test_ignores_files(self, isolated_data):
        skills_dir = isolated_data / "skills"
        (skills_dir / "notdir.txt").write_text("x")
        assert manager.discover_skills() == []


class TestLoadSkill:
    def test_by_name(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo", description="X", body="instruction")
        skill = manager.load_skill("foo")
        assert skill is not None
        assert skill.description == "X"
        assert "instruction" in skill.body

    def test_missing(self, isolated_data):
        assert manager.load_skill("nonexistent") is None


class TestCreateSkill:
    def test_creates_file(self, isolated_data):
        skill = manager.create_skill("new", "desc text", "body content")
        assert skill is not None
        assert (isolated_data / "skills" / "new" / "SKILL.md").exists()


class TestRemoveSkill:
    def test_existing(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo")
        assert manager.remove_skill("foo") is True
        assert not (isolated_data / "skills" / "foo").exists()

    def test_missing(self, isolated_data):
        assert manager.remove_skill("nope") is False


class TestActivation:
    def test_activate_adds_pending(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo", body="instructions")
        manager.activate_skill("foo")
        assert manager.is_skill_active("foo") is True
        msgs = manager.consume_pending_messages()
        assert len(msgs) == 1
        assert "foo" in msgs[0]
        assert "instructions" in msgs[0]

    def test_consume_clears(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo")
        manager.activate_skill("foo")
        manager.consume_pending_messages()
        assert manager.consume_pending_messages() == []

    def test_deactivate(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo")
        manager.activate_skill("foo")
        manager.consume_pending_messages()  # очищаем буфер
        manager.deactivate_skill("foo")
        assert manager.is_skill_active("foo") is False
        msgs = manager.consume_pending_messages()
        assert any("ДЕАКТИВИРОВАН" in m for m in msgs)

    def test_active_set_names(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo")
        _make_skill(isolated_data / "skills", "bar")
        manager.activate_skill("foo")
        manager.activate_skill("bar")
        assert manager.get_active_skill_names() == {"foo", "bar"}

    def test_reset_active(self, isolated_data):
        _make_skill(isolated_data / "skills", "foo")
        manager.activate_skill("foo")
        manager.reset_active_skills()
        assert manager.get_active_skill_names() == set()


class TestBuildSkillsPrompt:
    def test_empty_when_no_skills(self):
        assert manager.build_skills_prompt() == ""

    def test_lists_skills(self, isolated_data):
        _make_skill(isolated_data / "skills", "docx-mastery", description="DOCX handler")
        prompt = manager.build_skills_prompt()
        assert "docx-mastery" in prompt
        assert "DOCX handler" in prompt
        assert "skill" in prompt