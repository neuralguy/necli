"""apis/tool_schemas.py — валидность OpenAI-схем для agent/plan режимов."""

import pytest

from apis.tool_schemas import (
    TOOL_SCHEMAS,
    get_tool_schemas,
    invalidate_schemas_cache,
    tool_requires_args,
)
from config import READ_ONLY_TOOLS
from tools.registry import TOOL_REGISTRY

# ssh/subagent теперь гейтятся скиллами и в agent-mode по умолчанию скрыты —
# тестируются отдельно в TestSkillGating. Здесь только негейтящиеся write-тулы.
WRITE_TOOLS = {
    "shell", "patch_file", "create_file", "create_docx",
}

def _names(schemas):
    return [s["function"]["name"] for s in schemas]

@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_schemas_cache()
    yield
    invalidate_schemas_cache()

class TestSchemaShape:
    def test_every_schema_is_valid_openai_function(self):
        for s in TOOL_SCHEMAS:
            assert s["type"] == "function"
            fn = s["function"]
            assert isinstance(fn["name"], str) and fn["name"]
            assert isinstance(fn["description"], str) and fn["description"]
            params = fn["parameters"]
            assert params["type"] == "object"
            assert isinstance(params["properties"], dict)

    def test_required_fields_subset_of_properties(self):
        for s in TOOL_SCHEMAS:
            params = s["function"]["parameters"]
            required = params.get("required", [])
            assert isinstance(required, list)
            props = set(params["properties"].keys())
            assert set(required) <= props, (
                f"{s['function']['name']}: required {required} not in properties {props}"
            )

    def test_names_are_unique(self):
        names = _names(TOOL_SCHEMAS)
        assert len(names) == len(set(names))

class TestNamesMatchRegistry:
    def test_executable_schema_tools_exist_in_registry(self):
        # plan/think — UI-only (не в registry); read_file — алиас.
        ui_only = {"plan", "think"}
        for s in TOOL_SCHEMAS:
            name = s["function"]["name"]
            if name in ui_only:
                continue
            assert name in TOOL_REGISTRY, f"schema tool '{name}' missing from TOOL_REGISTRY"

    def test_read_only_tools_have_schemas(self):
        schema_names = set(_names(TOOL_SCHEMAS))
        for t in READ_ONLY_TOOLS:
            assert t in schema_names, f"read-only tool '{t}' has no schema"

class TestAgentMode:
    def test_returns_nonempty_list_of_dicts(self):
        schemas = get_tool_schemas("agent")
        assert isinstance(schemas, list) and schemas
        for s in schemas:
            assert isinstance(s, dict)
            assert s["type"] == "function"

    def test_includes_write_tools(self):
        names = set(_names(get_tool_schemas("agent")))
        for t in WRITE_TOOLS:
            assert t in names, f"agent mode missing write tool '{t}'"

    def test_includes_read_only_tools(self):
        names = set(_names(get_tool_schemas("agent")))
        assert names >= READ_ONLY_TOOLS

    def test_returns_fresh_list_copy(self):
        a = get_tool_schemas("agent")
        b = get_tool_schemas("agent")
        assert a is not b
        assert _names(a) == _names(b)

class TestSkillGating:
    """Гейтящиеся скиллами тулы скрыты, пока скилл не активен."""

    GATED = {"web_search", "image_search", "ssh", "subagent"}  # noqa: RUF012

    def test_gated_hidden_by_default(self):
        names = set(_names(get_tool_schemas("agent")))
        assert not (self.GATED & names), f"gated tools leaked: {self.GATED & names}"

    def test_gated_hidden_with_empty_active(self):
        names = set(_names(get_tool_schemas("agent", set())))
        assert not (self.GATED & names)

    def test_web_skill_exposes_web_tools(self):
        names = set(_names(get_tool_schemas("agent", {"web"})))
        assert "web_search" in names
        assert "image_search" in names
        # but not ssh/subagent (different skills)
        assert "ssh" not in names
        assert "subagent" not in names

    def test_ssh_skill_exposes_ssh(self):
        names = set(_names(get_tool_schemas("agent", {"ssh"})))
        assert "ssh" in names
        assert "web_search" not in names

    def test_subagents_skill_exposes_orchestration(self):
        names = set(_names(get_tool_schemas("agent", {"subagents"})))
        assert "subagent" in names

    def test_all_skills_active_exposes_all_gated(self):
        names = set(_names(get_tool_schemas("agent", {"web", "ssh", "subagents"})))
        assert names >= self.GATED

    def test_ungated_tools_always_present(self):
        names = set(_names(get_tool_schemas("agent", set())))
        assert "shell" in names
        assert "read_files" in names
        assert "skill" in names

    def test_cache_distinguishes_active_skills(self):
        # разный набор активных скиллов → разные результаты (кэш не путает)
        n_none = set(_names(get_tool_schemas("agent", set())))
        n_web = set(_names(get_tool_schemas("agent", {"web"})))
        assert "web_search" not in n_none
        assert "web_search" in n_web


class TestPlanMode:
    def test_excludes_write_tools(self):
        names = set(_names(get_tool_schemas("plan")))
        offenders = WRITE_TOOLS & names
        assert not offenders, f"plan mode leaks write tools: {offenders}"

    def test_only_read_only_plus_plan(self):
        names = set(_names(get_tool_schemas("plan")))
        allowed = set(READ_ONLY_TOOLS) | {"poll", "skill", "web_search", "plan", "think"}
        assert names <= allowed, f"unexpected tools in plan mode: {names - allowed}"

    def test_includes_all_read_only_tools(self):
        names = set(_names(get_tool_schemas("plan")))
        assert names >= READ_ONLY_TOOLS

    def test_plan_tool_always_present(self):
        assert "plan" in _names(get_tool_schemas("plan"))

    def test_plan_subset_of_agent(self):
        plan_names = set(_names(get_tool_schemas("plan")))
        agent_names = set(_names(get_tool_schemas("agent", {"web"})))
        # plan может содержать только think/plan вне agent — но они есть и в agent при тех же условиях
        assert (plan_names - {"think"}) <= agent_names


class TestAutonomousMode:
    def test_excludes_write_tools_except_shell(self):
        names = set(_names(get_tool_schemas("autonomous", {"subagents"})))
        offenders = (WRITE_TOOLS - {"shell"}) & names
        assert not offenders, f"autonomous mode leaks write tools: {offenders}"
        assert "shell" in names

    def test_includes_subagent_when_skill_active(self):
        names = set(_names(get_tool_schemas("autonomous", {"subagents"})))
        assert "subagent" in names

    def test_hides_subagent_without_skill(self):
        names = set(_names(get_tool_schemas("autonomous", set())))
        assert "subagent" not in names

class TestDocxScreenshotSchema:
    """docx_screenshot должен экспонировать dpi, иначе native-вызовы не задают его."""

    def _props(self):
        for s in TOOL_SCHEMAS:
            if s["function"]["name"] == "docx_screenshot":
                return s["function"]["parameters"]["properties"]
        pytest.fail("docx_screenshot schema not found")

    def test_dpi_present(self):
        props = self._props()
        assert "dpi" in props, "docx_screenshot schema must expose 'dpi'"

    def test_dpi_is_integer(self):
        assert self._props()["dpi"]["type"] == "integer"

    def test_dpi_range_matches_code_clamp(self):
        dpi = self._props()["dpi"]
        assert dpi.get("minimum") == 50
        assert dpi.get("maximum") == 600
        assert dpi.get("default") == 200

class TestSchemaValidity:
    def test_all_schemas_json_serializable(self):
        import json
        json.dumps(get_tool_schemas("agent"))
        json.dumps(get_tool_schemas("plan"))

class TestToolRequiresArgs:
    """Используется для восстановления потерянных при стриминге native-args:
    пустой {} у тула с required-полями → нужен фолбэк-перезапрос."""

    def test_tools_with_required_params(self):
        assert tool_requires_args("memory_write") is True
        assert tool_requires_args("memory_read") is True
        assert tool_requires_args("shell") is True

    def test_noarg_tools(self):
        assert tool_requires_args("memory_list") is False

    def test_unknown_tool_is_false(self):
        assert tool_requires_args("definitely_not_a_tool") is False
