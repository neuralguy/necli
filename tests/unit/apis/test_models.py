"""apis/models.py — ApiProviderDefinition / ApiModelInfo."""

from apis.models import ApiModelInfo, ApiProviderDefinition


class TestApiModelInfo:
    def test_defaults(self):
        m = ApiModelInfo(id="m1", display_name="M1")
        assert m.context_window == 128_000
        assert m.input_price == 0.0
        assert m.output_price == 0.0

    def test_frozen(self):
        import dataclasses
        m = ApiModelInfo(id="m1", display_name="M1")
        # frozen dataclass — попытка изменить вызывает FrozenInstanceError
        try:
            m.id = "other"
            assert False, "expected FrozenInstanceError"
        except dataclasses.FrozenInstanceError:
            pass


class TestApiProviderDefinition:
    def test_get_model_info_by_id(self):
        d = ApiProviderDefinition(
            id="x", name="X", type="openai_compatible", base_url="u",
            models=[ApiModelInfo(id="m1", display_name="M1")],
        )
        info = d.get_model_info("m1")
        assert info is not None
        assert info.id == "m1"

    def test_get_model_info_by_display_name(self):
        d = ApiProviderDefinition(
            id="x", name="X", type="openai_compatible", base_url="u",
            models=[ApiModelInfo(id="m1", display_name="My Model")],
        )
        info = d.get_model_info("My Model")
        assert info is not None
        assert info.id == "m1"

    def test_get_model_info_not_found(self):
        d = ApiProviderDefinition(
            id="x", name="X", type="openai_compatible", base_url="u",
            models=[ApiModelInfo(id="m1", display_name="M1")],
        )
        assert d.get_model_info("nope") is None

    def test_default_factories(self):
        d = ApiProviderDefinition(id="x", name="X", type="openai_compatible", base_url="u")
        assert d.models == []
        assert d.default_headers == {}
        assert d.extra == {}