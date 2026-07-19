from apis.messages import AIMessageChunk
from apis.models import ApiProviderDefinition
from apis.providers.anthropic_provider import AnthropicProvider, create_anthropic_provider


def _fake_api_key(provider_id: str) -> str:
    assert provider_id
    return "test-token"


def _fake_credentials(provider_id: str) -> list[dict]:
    assert provider_id
    return [{"key": "test-token", "main": True}]


def test_session_identifiers_are_stable(monkeypatch):
    monkeypatch.setattr("apis.providers.anthropic_provider.get_api_key", _fake_api_key)
    monkeypatch.setattr("apis.providers.anthropic_provider.get_api_credentials", _fake_credentials)

    provider = create_anthropic_provider(
        ApiProviderDefinition(
            id="gateway",
            name="Gateway",
            type="anthropic",
            api_format="anthropic",
            base_url="https://gateway.example.com",
            extra={
                "prompt_cache": "on",
                "session_id_header": "X-Session-Id",
                "inject_metadata": {"user_id": '{"session_id": ""}'},
            },
        ),
        "test-model",
    )

    first_headers = provider._get_headers()
    second_headers = provider._get_headers()
    assert first_headers["X-Session-Id"] == second_headers["X-Session-Id"]

    params = {"metadata": {}}
    provider._inject_extra_metadata(params)
    user_id = params["metadata"]["user_id"]
    assert first_headers["X-Session-Id"] in user_id


def test_system_as_first_message_keeps_only_top_level_system():
    provider = AnthropicProvider(model="claude-opus-4-8")
    original_messages = [
        {"role": "user", "content": [{"type": "text", "text": "volatile user prompt"}]},
    ]
    system, messages = provider._apply_system_as_message(
        "stable system prompt",
        original_messages,
    )

    assert system == "stable system prompt"
    assert messages == original_messages


def test_cache_control_marks_system_and_current_turn_for_next_request():
    params = {
        "system": "stable system prompt",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "volatile user prompt"},
            ],
        }],
    }

    AnthropicProvider._apply_cache_control(params)

    assert params["system"] == [{
        "type": "text",
        "text": "stable system prompt",
        "cache_control": {"type": "ephemeral"},
    }]
    # Текущий user-turn уникален для этого запроса, но в следующем запросе он уже
    # станет стабильной историей. Marker здесь пишет cache entry для next turn.
    assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_cache_control_marks_reusable_history_before_fresh_tail():
    params = {
        "system": "stable system prompt",
        "tools": [{"name": "read_files", "input_schema": {"type": "object"}}],
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "first prompt"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "stable answer"}]},
            {"role": "user", "content": [{"type": "text", "text": "new prompt"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "fresh answer"}]},
            {"role": "user", "content": [{"type": "text", "text": "current prompt"}]},
        ],
    }

    AnthropicProvider._apply_cache_control(params)

    assert params["system"] == [{
        "type": "text",
        "text": "stable system prompt",
        "cache_control": {"type": "ephemeral"},
    }]
    # Marker на tools не нужен: system breakpoint уже кэширует prefix tools→system,
    # а отдельный tools marker съедал слот и оставлял только ~2K hit.
    assert "cache_control" not in params["tools"][0]
    # Приоритет — reusable history до свежего хвоста, а не предыдущий assistant/current user.
    assert params["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][1]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["messages"][2]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in params["messages"][3]["content"][0]
    assert "cache_control" not in params["messages"][4]["content"][0]


def test_cache_control_uses_at_most_four_breakpoints():
    params = {
        "system": "stable system prompt",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": f"turn {i}"}]}
            for i in range(6)
        ],
    }

    AnthropicProvider._apply_cache_control(params)

    marks = 0
    system = params["system"]
    if isinstance(system, list):
        marks += sum(1 for block in system if "cache_control" in block)
    for msg in params["messages"]:
        marks += sum(1 for block in msg["content"] if "cache_control" in block)
    assert marks == 4


def test_anthropic_usage_keeps_cache_counters():
    usage = AnthropicProvider._convert_usage_anthropic(
        {
            "input_tokens": 100,
            "output_tokens": 7,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 20,
        }
    )

    assert usage == {
        "input_tokens": 200,
        "output_tokens": 7,
        "total_tokens": 207,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 20,
    }


def test_stream_chunk_usage_preserves_cache_counters():
    chunk = AIMessageChunk(
        content="",
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 7,
            "total_tokens": 107,
            "cache_read_input_tokens": 80,
            "cache_creation_input_tokens": 20,
        },
    )

    assert chunk.usage_metadata["cache_read_input_tokens"] == 80
    assert chunk.usage_metadata["cache_creation_input_tokens"] == 20
