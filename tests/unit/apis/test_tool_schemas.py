"""apis/tool_schemas.py — валидность OpenAI-схем для agent/plan режимов."""

import pytest

from apis.tool_schemas import (
    TOOL_SCHEMAS,
    get_tool_schemas,
    invalidate_schemas_cache,
)
from config import READ_ONLY_TOOLS
from tools.registry import TOOL_REGISTRY

WRITE_TOOLS = {
    "shell", "write_file", "patch_file", "create_file", "delete_file",
    "rename_file", "copy_file", "move_file", "create_docx", "mkdir",
    "rmdir", "apply_diff", "ssh", "subagent",
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
        assert READ_ONLY_TOOLS <= names

    def test_returns_fresh_list_copy(self):
        a = get_tool_schemas("agent")
        b = get_tool_schemas("agent")
        assert a is not b
        assert _names(a) == _names(b)

class TestPlanMode:
    def test_excludes_write_tools(self):
        names = set(_names(get_tool_schemas("plan")))
        offenders = WRITE_TOOLS & names
        assert not offenders, f"plan mode leaks write tools: {offenders}"

    def test_only_read_only_plus_plan(self):
        names = set(_names(get_tool_schemas("plan")))
        allowed = set(READ_ONLY_TOOLS) | {"plan", "think"}
        assert names <= allowed, f"unexpected tools in plan mode: {names - allowed}"

    def test_includes_all_read_only_tools(self):
        names = set(_names(get_tool_schemas("plan")))
        assert READ_ONLY_TOOLS <= names

    def test_plan_tool_always_present(self):
        assert "plan" in _names(get_tool_schemas("plan"))

    def test_plan_subset_of_agent(self):
        plan_names = set(_names(get_tool_schemas("plan")))
        agent_names = set(_names(get_tool_schemas("agent")))
        # plan может содержать только think/plan вне agent — но они есть и в agent при тех же условиях
        assert (plan_names - {"think"}) <= agent_names

class TestSchemaValidity:
    def test_all_schemas_json_serializable(self):
        import json
        json.dumps(get_tool_schemas("agent"))
        json.dumps(get_tool_schemas("plan"))