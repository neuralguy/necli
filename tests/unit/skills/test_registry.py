"""Тесты skills.registry — маппинг скилл→тулы и расчёт активных скиллов."""

from apis.messages import AIMessage, HumanMessage, ToolMessage
from skills.registry import (
    ACTIVE_WINDOW_ROUNDS,
    GATED_TOOLS,
    SKILL_TOOLS,
    active_skills_from_messages,
    is_tool_gated_out,
    skill_for_tool,
    tools_for_skill,
    visible_gated_tools,
)


# ---------------- статический маппинг ----------------
def test_gated_tools_is_union():
    expected = set()
    for tools in SKILL_TOOLS.values():
        expected |= tools
    assert expected == GATED_TOOLS


def test_web_skill_tools():
    assert tools_for_skill("web") == {"web_search", "image_search"}


def test_skill_for_tool():
    assert skill_for_tool("web_search") == "web"
    assert skill_for_tool("image_search") == "web"
    assert skill_for_tool("ssh") == "ssh"
    assert skill_for_tool("subagent") == "subagents"
    assert skill_for_tool("shell") is None


def test_visible_gated_tools():
    assert visible_gated_tools(set()) == set()
    assert visible_gated_tools({"web"}) == {"web_search", "image_search"}
    assert visible_gated_tools({"web", "ssh"}) == {"web_search", "image_search", "ssh"}


def test_is_tool_gated_out():
    # ungated tool never gated out
    assert is_tool_gated_out("shell", set()) is False
    # gated tool hidden when its skill inactive
    assert is_tool_gated_out("web_search", set()) is True
    assert is_tool_gated_out("web_search", {"ssh"}) is True
    # visible when its skill active
    assert is_tool_gated_out("web_search", {"web"}) is False


# ---------------- активность по истории (native-mode) ----------------
def _ai_skill(name, cid="c1"):
    return AIMessage(content="", tool_calls=[{"name": "skill", "id": cid, "args": {"name": name}}])


def test_active_empty_history():
    assert active_skills_from_messages([]) == set()


def test_active_native_just_loaded():
    msgs = [HumanMessage(content="go"), _ai_skill("web"),
            ToolMessage(content="# web", tool_call_id="c1", name="skill")]
    assert active_skills_from_messages(msgs) == {"web"}


def test_active_native_within_window():
    # load at round 1, total 5 rounds → age 4 → still active (window 5)
    msgs = [HumanMessage(content="go"), _ai_skill("web"),
            ToolMessage(content="# web", tool_call_id="c1", name="skill")]
    for i in range(2, ACTIVE_WINDOW_ROUNDS + 1):  # rounds 2..5
        msgs += [HumanMessage(content=f"m{i}"), AIMessage(content="ok")]
    assert active_skills_from_messages(msgs) == {"web"}


def test_active_native_expired_after_window():
    # load at round 1, total 6 rounds → age 5 → expired
    msgs = [HumanMessage(content="go"), _ai_skill("web"),
            ToolMessage(content="# web", tool_call_id="c1", name="skill")]
    for i in range(2, ACTIVE_WINDOW_ROUNDS + 2):  # rounds 2..6
        msgs += [HumanMessage(content=f"m{i}"), AIMessage(content="ok")]
    assert active_skills_from_messages(msgs) == set()


def test_active_reload_refreshes_window():
    # load round 1, let it nearly expire, reload at round 5 → active again
    msgs = [HumanMessage(content="go"), _ai_skill("web", "c1"),
            ToolMessage(content="# web", tool_call_id="c1", name="skill")]
    for i in range(2, 5):  # rounds 2..4
        msgs += [HumanMessage(content=f"m{i}"), AIMessage(content="ok")]
    # round 5: reload
    msgs += [HumanMessage(content="reload"), _ai_skill("web", "c2"),
             ToolMessage(content="# web", tool_call_id="c2", name="skill")]
    assert active_skills_from_messages(msgs) == {"web"}


def test_active_multiple_skills():
    msgs = [
        HumanMessage(content="go"),
        _ai_skill("web", "c1"), ToolMessage(content="# web", tool_call_id="c1", name="skill"),
        _ai_skill("ssh", "c2"), ToolMessage(content="# ssh", tool_call_id="c2", name="skill"),
    ]
    assert active_skills_from_messages(msgs) == {"web", "ssh"}


# ---------------- активность по истории (text-mode) ----------------
def test_active_text_mode():
    msgs = [HumanMessage(content="go"), AIMessage(content="resp"),
            HumanMessage(content="$ skill web\n# web body")]
    assert active_skills_from_messages(msgs) == {"web"}


def test_active_text_mode_in_combined_block():
    combined = "$ shell ls\nfile1\n---\n$ skill web\n# web body"
    msgs = [HumanMessage(content="go"), AIMessage(content="resp"),
            HumanMessage(content=combined)]
    assert active_skills_from_messages(msgs) == {"web"}


def test_active_name_from_content_fallback():
    # native ToolMessage without matching AIMessage tool_call → name from content
    msgs = [HumanMessage(content="go"),
            ToolMessage(content="$ skill web\n# web body", tool_call_id="x", name="skill")]
    assert active_skills_from_messages(msgs) == {"web"}
