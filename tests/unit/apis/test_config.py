"""apis/config.py — APIs JSON store, ключи, tool_format."""

from apis.config import (
    add_api_config,
    add_model_to_provider,
    get_api_config,
    get_api_key,
    get_api_keys,
    list_api_configs,
    remove_api_config,
    remove_model_from_provider,
    set_api_key,
    set_provider_prompt_cache,
)


class TestAddRemove:
    def test_initial_empty(self, isolated_data):
        assert list_api_configs() == []

    def test_add(self, isolated_data):
        entry = add_api_config(
            provider_id="x",
            name="X",
            base_url="https://x.test/v1",
            api_key="key1",
            models=[{"id": "m1", "display_name": "M1"}],
            default_model="m1",
        )
        assert entry["id"] == "x"
        all_configs = list_api_configs()
        assert len(all_configs) == 1
        assert all_configs[0]["id"] == "x"
        assert get_api_key("x") == "key1"

    def test_update_same_id_replaces(self, isolated_data):
        add_api_config(provider_id="x", name="X1", base_url="https://x.test")
        add_api_config(provider_id="x", name="X2", base_url="https://x.test")
        configs = list_api_configs()
        assert len(configs) == 1
        assert configs[0]["name"] == "X2"

    def test_remove(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u", api_key="k")
        ok = remove_api_config("x")
        assert ok is True
        assert list_api_configs() == []
        # Ключи тоже удалены
        assert get_api_key("x") == ""

    def test_remove_unknown(self, isolated_data):
        assert remove_api_config("nope") is False

    def test_set_prompt_cache_mode(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")

        assert set_provider_prompt_cache("x", False) is True
        config = get_api_config("x")
        assert config is not None
        assert config["extra"]["prompt_cache"] == "off"

        assert set_provider_prompt_cache("x", True) is True
        config = get_api_config("x")
        assert config is not None
        assert config["extra"]["prompt_cache"] == "on"

    def test_set_prompt_cache_unknown_provider(self, isolated_data):
        assert set_provider_prompt_cache("missing", False) is False

    def test_get_api_config(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")
        assert get_api_config("x") is not None
        assert get_api_config("missing") is None


class TestKeys:
    def test_csv_split(self, isolated_data):
        set_api_key("x", "k1, k2 , k3")
        keys = get_api_keys("x")
        assert keys == ["k1", "k2", "k3"]

    def test_get_first(self, isolated_data):
        set_api_key("x", "first,second")
        assert get_api_key("x") == "first"

    def test_unknown_returns_empty(self, isolated_data):
        assert get_api_key("nope") == ""
        assert get_api_keys("nope") == []

    def test_empty_string_no_keys(self, isolated_data):
        set_api_key("x", "")
        assert get_api_keys("x") == []


class TestModels:
    def test_add(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")
        ok = add_model_to_provider("x", "m1", display_name="M1")
        assert ok is True
        config = get_api_config("x")
        assert any(m["id"] == "m1" for m in config["models"])

    def test_replace(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")
        add_model_to_provider("x", "m1", display_name="M1")
        add_model_to_provider("x", "m1", display_name="M1 New")
        models = get_api_config("x")["models"]
        assert len([m for m in models if m["id"] == "m1"]) == 1
        assert next(m for m in models if m["id"] == "m1")["display_name"] == "M1 New"

    def test_remove(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")
        add_model_to_provider("x", "m1")
        assert remove_model_from_provider("x", "m1") is True
        config = get_api_config("x")
        assert not any(m["id"] == "m1" for m in config["models"])

    def test_remove_unknown(self, isolated_data):
        add_api_config(provider_id="x", name="X", base_url="u")
        assert remove_model_from_provider("x", "missing") is False
