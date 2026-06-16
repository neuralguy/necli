"""apis/registry.py — загрузка определений, фабрика инстансов."""

import pytest

from apis.registry import (
    load_all,
    reload_providers,
    get_definition,
    list_providers,
    list_api_models,
    resolve_api_model,
    _parse_model,
    _parse_definition,
)


class TestParseModel:
    def test_minimal(self):
        m = _parse_model({"id": "m1"})
        assert m.id == "m1"
        assert m.display_name == "m1"  # fallback
        assert m.context_window == 128_000

    def test_full(self):
        m = _parse_model({
            "id": "m1", "display_name": "M1",
            "context_window": 200000,
            "input_price": 1.5, "output_price": 2.0,
        })
        assert m.display_name == "M1"
        assert m.context_window == 200000
        assert m.input_price == 1.5
        assert m.output_price == 2.0


class TestParseDefinition:
    def test_minimal(self):
        defn = _parse_definition({"id": "x"})
        assert defn.id == "x"
        assert defn.name == "x"
        assert defn.type == "openai_compatible"
        assert defn.api_format == "openai"
        assert defn.enabled is True

    def test_with_models(self):
        defn = _parse_definition({
            "id": "x", "name": "X",
            "models": [{"id": "m1"}, {"id": "m2"}],
        })
        assert len(defn.models) == 2
        assert defn.models[0].id == "m1"

    def test_disabled_flag(self):
        defn = _parse_definition({"id": "x", "enabled": False})
        assert defn.enabled is False


class TestLoadAll:
    def test_loads_builtins(self, isolated_data):
        reload_providers()
        load_all()
        assert get_definition("openai") is not None
        assert get_definition("anthropic") is not None
        assert get_definition("google") is not None

    def test_get_unknown_returns_none(self, isolated_data):
        reload_providers()
        assert get_definition("totally_unknown_provider") is None


class TestListProviders:
    def test_includes_builtins(self, isolated_data):
        reload_providers()
        ids = [p["id"] for p in list_providers()]
        assert "openai" in ids
        assert "anthropic" in ids

    def test_provider_meta_keys(self, isolated_data):
        reload_providers()
        providers = list_providers()
        for p in providers:
            assert set(p.keys()) >= {"id", "name", "type", "base_url", "enabled", "has_key", "models", "default_model"}


class TestListApiModels:
    def test_flat_list(self, isolated_data):
        reload_providers()
        models = list_api_models()
        assert len(models) > 0
        for m in models:
            assert set(m.keys()) >= {"provider_id", "model_id", "display_name", "context_window"}

    def test_skips_disabled_providers(self, isolated_data):
        from apis.config import add_api_config
        add_api_config(provider_id="dis", name="Dis", base_url="u",
                       models=[{"id": "dm1", "display_name": "DM1"}],
                       enabled=False)
        reload_providers()
        models = list_api_models()
        assert not any(m["provider_id"] == "dis" for m in models)


class TestResolveApiModel:
    def test_exact_model_id(self, isolated_data):
        reload_providers()
        result = resolve_api_model("gpt-5")
        assert result is not None
        provider_id, model_id = result
        assert provider_id == "openai"
        assert model_id == "gpt-5"

    def test_exact_display_name(self, isolated_data):
        reload_providers()
        result = resolve_api_model("Claude Opus 4.7")
        assert result is not None
        assert result[1] == "claude-opus-4-7"

    def test_unknown(self, isolated_data):
        reload_providers()
        assert resolve_api_model("totally-not-a-model-xyz") is None

    def test_case_insensitive(self, isolated_data):
        reload_providers()
        result = resolve_api_model("GPT-5")
        assert result is not None


class TestGetProvider:
    def test_disabled_raises(self, isolated_data, monkeypatch):
        from apis import registry as r
        from apis.config import add_api_config
        add_api_config(provider_id="dis", name="Dis", base_url="u", enabled=False)
        reload_providers()
        with pytest.raises(ValueError):
            r.get_provider("dis", "any")

    def test_unknown_raises(self, isolated_data):
        from apis import registry as r
        reload_providers()
        with pytest.raises(KeyError):
            r.get_provider("totally-unknown", "any")