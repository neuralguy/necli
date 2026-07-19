from apis.base import BaseProvider
from apis.messages import HumanMessage, SystemMessage


class DummyOpenRouterProvider(BaseProvider):
    def __init__(self) -> None:
        super().__init__(model="anthropic/claude-sonnet-4-6")
        self._provider_name = "OpenRouter"
        self._api_url = "https://openrouter.ai/api/v1/chat/completions"


def test_openai_compatible_claude_routes_get_cache_breakpoints():
    provider = DummyOpenRouterProvider()
    params = provider._build_params()
    params["messages"] = provider._convert_messages([
        SystemMessage(content="stable system"),
        HumanMessage(content="old turn"),
        HumanMessage(content="new turn"),
    ])

    provider._apply_openai_compatible_cache_control(params)

    assert params["messages"][0]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][-2]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert isinstance(params["messages"][-1]["content"], str)


def test_provider_prompt_cache_on_uses_native_gpt_cache_without_anthropic_markers():
    provider = BaseProvider(model="gpt-5.6-terra")
    provider._prompt_cache_mode = "on"
    params = provider._build_params()
    params["messages"] = provider._convert_messages([
        SystemMessage(content="stable system"),
        HumanMessage(content="old turn"),
        HumanMessage(content="new turn"),
    ])

    provider._apply_openai_compatible_cache_control(params)

    assert all("cache_control" not in str(message) for message in params["messages"])


def test_provider_prompt_cache_off_disables_cache_control():
    provider = DummyOpenRouterProvider()
    provider._prompt_cache_mode = "off"
    params = provider._build_params()
    params["messages"] = provider._convert_messages([
        SystemMessage(content="stable system"),
        HumanMessage(content="old turn"),
        HumanMessage(content="new turn"),
    ])

    provider._apply_openai_compatible_cache_control(params)

    assert all("cache_control" not in str(message) for message in params["messages"])


    provider = BaseProvider(model="gpt-5.5")
    provider._provider_name = "OpenAI"
    provider._api_url = "https://api.openai.com/v1/chat/completions"
    params = provider._build_params()
    params["messages"] = provider._convert_messages([
        SystemMessage(content="stable system"),
        HumanMessage(content="old turn"),
        HumanMessage(content="new turn"),
    ])

    provider._apply_openai_compatible_cache_control(params)

    assert all("cache_control" not in str(message) for message in params["messages"])


def test_openai_usage_exposes_cached_tokens():
    usage = BaseProvider._convert_usage({
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "prompt_tokens_details": {"cached_tokens": 80},
    })

    assert usage["input_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 80
