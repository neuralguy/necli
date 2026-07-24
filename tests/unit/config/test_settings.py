"""config/settings.py — персистентный config.json."""

from config.settings import _DEFAULT_CONFIG, get, get_all, reset, set_value


class TestGetDefault:
    def test_unknown_key_with_default(self, isolated_data):
        assert get("totally_unknown_key_xyz", "fallback") == "fallback"

    def test_unknown_key_no_default(self, isolated_data):
        assert get("totally_unknown_key_xyz") is None

    def test_known_default(self, isolated_data):
        assert get("response_timeout") == _DEFAULT_CONFIG["response_timeout"]

    def test_native_tool_format_is_enabled_by_default(self, isolated_data):
        assert get("tool_format_force_native") is True


class TestSetValue:
    def test_persists(self, isolated_data):
        set_value("custom_key", "value1")
        assert get("custom_key", "") == "value1"

    def test_persists_across_reload(self, isolated_data):
        from config import settings as _s
        set_value("k", "v")
        _s._config_cache = None
        assert get("k", "") == "v"

    def test_overwrite(self, isolated_data):
        set_value("k", "a")
        set_value("k", "b")
        assert get("k", "") == "b"


class TestTypeMismatch:
    def test_default_type_protects(self, isolated_data):
        set_value("model", 42)  # int вместо str
        # запрос со str-default → должен вернуть default (защита от type-mismatch)
        assert get("model", "fallback_str") == "fallback_str"


class TestGetAll:
    def test_includes_defaults(self, isolated_data):
        data = get_all()
        for k in _DEFAULT_CONFIG:
            assert k in data

    def test_includes_custom(self, isolated_data):
        set_value("custom", 1)
        assert get_all()["custom"] == 1


class TestReset:
    def test_resets_to_defaults(self, isolated_data):
        set_value("model", "weird-model")
        reset()
        assert get("model", "") == _DEFAULT_CONFIG["model"]


class TestCorruptFile:
    def test_corrupt_falls_back_to_defaults(self, isolated_data):
        from config import settings as _s
        (isolated_data / "config.json").write_text("not valid json")
        _s._config_cache = None
        assert get("model", "") == _DEFAULT_CONFIG["model"]
