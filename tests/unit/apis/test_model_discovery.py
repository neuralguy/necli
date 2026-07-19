"""apis/model_discovery.py — парсинг/нормализация списков моделей.

Все HTTP замоканы (httpx.AsyncClient), реальных сетевых вызовов НЕТ.
"""


import pytest

import apis.model_discovery as md


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload

class _FakeClient:
    """Заглушка httpx.AsyncClient: возвращает заранее заданный ответ на .get()."""

    def __init__(self, response=None, raise_exc=None, capture=None):
        self._response = response
        self._raise = raise_exc
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None):
        if self._capture is not None:
            self._capture["url"] = url
            self._capture["headers"] = headers
        if self._raise is not None:
            raise self._raise
        return self._response

def _patch_client(monkeypatch, response=None, raise_exc=None, capture=None):
    def _factory(*args, **kwargs):
        return _FakeClient(response=response, raise_exc=raise_exc, capture=capture)
    monkeypatch.setattr(md.httpx, "AsyncClient", _factory)

class TestFetchOpenAICompatible:
    @pytest.mark.asyncio
    async def test_basic_openai_format(self, monkeypatch):
        payload = {"data": [
            {"id": "gpt-x", "context_length": 200000},
            {"id": "gpt-y"},
        ]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert len(result) == 2
        assert result[0]["id"] == "gpt-x"
        assert result[0]["context_window"] == 200000
        assert result[1]["context_window"] == 128_000  # default

    @pytest.mark.asyncio
    async def test_list_root_instead_of_data(self, monkeypatch):
        payload = [{"id": "m1"}, {"id": "m2"}]
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert [m["id"] for m in result] == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_id_fallback_to_model_and_name(self, monkeypatch):
        payload = {"data": [
            {"model": "via-model"},
            {"name": "via-name"},
        ]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        ids = [m["id"] for m in result]
        assert "via-model" in ids
        assert "via-name" in ids

    @pytest.mark.asyncio
    async def test_skips_items_without_id(self, monkeypatch):
        payload = {"data": [{"id": "ok"}, {"foo": "bar"}, "not-a-dict"]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert [m["id"] for m in result] == ["ok"]

    @pytest.mark.asyncio
    async def test_openrouter_pricing_per_token_converted(self, monkeypatch):
        # prompt/completion < 1 → per-token → конвертируется в per-1M
        payload = {"data": [{
            "id": "or-model",
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
        }]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["input_price"] == pytest.approx(1.0)
        assert result[0]["output_price"] == pytest.approx(2.0)

    @pytest.mark.asyncio
    async def test_pricing_already_per_million_kept(self, monkeypatch):
        payload = {"data": [{
            "id": "m",
            "pricing": {"prompt": "5", "completion": "10"},
        }]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["input_price"] == 5.0
        assert result[0]["output_price"] == 10.0

    @pytest.mark.asyncio
    async def test_invalid_pricing_does_not_crash(self, monkeypatch):
        payload = {"data": [{
            "id": "m",
            "pricing": {"prompt": "abc", "completion": None},
        }]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["input_price"] == 0.0

    @pytest.mark.asyncio
    async def test_context_from_top_provider(self, monkeypatch):
        payload = {"data": [{"id": "m", "top_provider": {"context_length": 64000}}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["context_window"] == 64000

    @pytest.mark.asyncio
    async def test_display_name_from_name(self, monkeypatch):
        payload = {"data": [{"id": "m1", "name": "Pretty Name"}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["display_name"] == "Pretty Name"

    @pytest.mark.asyncio
    async def test_display_name_falls_back_to_id(self, monkeypatch):
        payload = {"data": [{"id": "bare-id"}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_openai_compatible("https://api.test", {}, 30)
        assert result[0]["display_name"] == "bare-id"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse(status_code=401, text="nope"))
        with pytest.raises(ValueError, match="HTTP 401"):
            await md._fetch_openai_compatible("https://api.test", {}, 30)

    @pytest.mark.asyncio
    async def test_unexpected_format_raises(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse(payload={"data": "not-a-list"}))
        with pytest.raises(ValueError, match="Unexpected response format"):
            await md._fetch_openai_compatible("https://api.test", {}, 30)

    @pytest.mark.asyncio
    async def test_url_built_with_models_suffix(self, monkeypatch):
        capture = {}
        _patch_client(monkeypatch, _FakeResponse(payload={"data": []}), capture=capture)
        await md._fetch_openai_compatible("https://api.test/v1/", {"X": "1"}, 30)
        assert capture["url"] == "https://api.test/v1/models"
        assert capture["headers"] == {"X": "1"}

class TestFetchOllama:
    @pytest.mark.asyncio
    async def test_basic(self, monkeypatch):
        payload = {"models": [
            {"name": "llama3", "details": {"context_length": 8192}, "size": 4 * 1024 ** 3},
        ]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_ollama("http://localhost:11434", 30)
        assert result[0]["id"] == "llama3"
        assert result[0]["context_window"] == 8192
        assert "4.0GB" in result[0]["display_name"]
        assert result[0]["input_price"] == 0.0

    @pytest.mark.asyncio
    async def test_default_context_and_no_size_hint(self, monkeypatch):
        payload = {"models": [{"name": "tiny"}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_ollama("http://localhost:11434", 30)
        assert result[0]["context_window"] == 32768
        assert result[0]["display_name"] == "tiny"  # no hint appended

    @pytest.mark.asyncio
    async def test_name_fallback_to_model_key(self, monkeypatch):
        payload = {"models": [{"model": "via-model-key"}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_ollama("http://localhost:11434", 30)
        assert result[0]["id"] == "via-model-key"

    @pytest.mark.asyncio
    async def test_skips_bad_entries(self, monkeypatch):
        payload = {"models": ["bad", {"foo": "bar"}, {"name": "good"}]}
        _patch_client(monkeypatch, _FakeResponse(payload=payload))
        result = await md._fetch_ollama("http://localhost:11434", 30)
        assert [m["id"] for m in result] == ["good"]

    @pytest.mark.asyncio
    async def test_url_strips_v1_suffix(self, monkeypatch):
        capture = {}
        _patch_client(monkeypatch, _FakeResponse(payload={"models": []}), capture=capture)
        await md._fetch_ollama("http://localhost:11434/v1", 30)
        assert capture["url"] == "http://localhost:11434/api/tags"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, monkeypatch):
        _patch_client(monkeypatch, _FakeResponse(status_code=500, text="boom"))
        with pytest.raises(ValueError, match="HTTP 500"):
            await md._fetch_ollama("http://localhost:11434", 30)

class TestIsLocalUrl:
    def test_localhost(self):
        assert md._is_local_url("http://localhost:1234") is True

    def test_loopback_ip(self):
        assert md._is_local_url("http://127.0.0.1:8080") is True

    def test_all_interfaces(self):
        assert md._is_local_url("http://0.0.0.0:9000") is True

    def test_remote(self):
        assert md._is_local_url("https://api.openai.com") is False
