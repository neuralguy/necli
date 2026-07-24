import io

import pytest

import tools.image_search as imgs
from tools.image_search import (
    _cache_get,
    _cache_put,
    _norm,
    _safe_name,
    _search_cache,
    execute_image_search,
)
from tools.models import ToolCall


@pytest.fixture(autouse=True)
def clear_cache():
    _search_cache.clear()
    yield
    _search_cache.clear()


def _call(**args):
    return ToolCall(command="", tool_name="image_search", args=args)


def _png_bytes():
    """Минимальный валидный 1x1 PNG через Pillow."""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1, 1), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


# ---------------- cache ----------------
def test_cache_roundtrip():
    _cache_put("k", [{"image": "x"}])
    assert _cache_get("k") == [{"image": "x"}]


def test_cache_miss():
    assert _cache_get("nope") is None


def test_cache_expiry(monkeypatch):
    monkeypatch.setattr(imgs, "_CACHE_TTL", 10)
    monkeypatch.setattr(imgs.time, "time", lambda: 1000.0)
    _cache_put("k", [{"image": "x"}])
    monkeypatch.setattr(imgs.time, "time", lambda: 1100.0)
    assert _cache_get("k") is None


def test_cache_eviction(monkeypatch):
    monkeypatch.setattr(imgs, "_CACHE_MAX_ENTRIES", 2)
    for i in range(4):
        _cache_put(f"k{i}", [{"image": str(i)}])
    assert len(_search_cache) == 2
    assert "k0" not in _search_cache
    assert "k3" in _search_cache


# ---------------- _norm ----------------
def test_norm_parses_ints_and_strips():
    r = _norm(image="  http://a.jpg ", width="800", height="bad", title=" t ")
    assert r["image"] == "http://a.jpg"
    assert r["width"] == 800
    assert r["height"] is None
    assert r["title"] == "t"


# ---------------- _safe_name ----------------
def test_safe_name_from_content_type():
    assert _safe_name(3, "http://x/p", "image/png").endswith(".png")
    assert _safe_name(3, "http://x/p", "image/png").startswith("image_03")


def test_safe_name_from_url_ext():
    assert _safe_name(0, "http://x/cat.webp?q=1", "").endswith(".webp")


def test_safe_name_fallback_jpg():
    assert _safe_name(0, "http://x/noext", "application/octet-stream").endswith(".jpg")


# ---------------- search flow ----------------
def test_no_query_errors():
    res = execute_image_search(_call())
    assert res.status == "error"
    assert "queries" in res.output.lower()


def test_search_lists_results(monkeypatch):
    fake_results = [
        _norm(image="http://a.jpg", title="A", width=800, height=600,
              source="a.com", provider="ddg", page="http://a.com/p", thumbnail="http://a.t"),
    ]
    monkeypatch.setattr(imgs, "_search_and_download",
                        lambda q, m, a, d: (fake_results, [], []))
    res = execute_image_search(_call(queries=["cats"]))
    assert res.status == "ok"
    assert "[0]" in res.output
    assert "http://a.jpg" in res.output
    assert "800x600" in res.output
    assert "via ddg" in res.output
    assert res.image_paths is None


def test_search_no_results(monkeypatch):
    monkeypatch.setattr(imgs, "_search_and_download",
                        lambda q, m, a, d: ([], [], []))
    res = execute_image_search(_call(queries=["zxqw"]))
    assert res.status == "ok"
    assert "Found 0 image(s)" in res.output


def test_results_without_image_url_filtered(monkeypatch, tmp_workdir):
    fake_results = [_norm(image=""), _norm(image="http://ok.png")]
    monkeypatch.setattr(imgs, "_search_ddg", lambda q, m, a: fake_results)

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(_png_bytes()))

    res = execute_image_search(_call(queries=["x"]))
    # http://ok.png выводится; пустой image отфильтрован
    assert "http://ok.png" in res.output
    # только один валидный результат (пустой отфильтрован)
    assert res.output.count("http://ok.png") == 1


def test_max_results_clamped(monkeypatch):
    captured = {}

    def fake_search(query, max_results, args, dest_dir):
        captured["max"] = max_results
        return ([], [], [])

    monkeypatch.setattr(imgs, "_search_and_download", fake_search)
    execute_image_search(_call(queries=["x"], max_results=999))
    assert captured["max"] == 50


def test_multiple_queries(monkeypatch):
    calls = []

    def fake_search(query, max_results, args, dest_dir):
        calls.append(query)
        return ([_norm(image="http://img.jpg", title=query)], [], [])

    monkeypatch.setattr(imgs, "_search_and_download", fake_search)
    res = execute_image_search(_call(queries=["cats", "dogs"]))
    assert len(calls) == 2
    assert calls == ["cats", "dogs"]
    assert "[Query 1:" in res.output
    assert "[Query 2:" in res.output


# ---------------- download + validation ----------------
class _FakeStream:
    def __init__(self, data, ct="image/png"):
        self._data = data
        self.headers = {"content-type": ct}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield self._data


def test_download_valid_image(monkeypatch, tmp_workdir):
    png = _png_bytes()
    fake_results = [_norm(image="http://a/cat.png", title="cat")]
    monkeypatch.setattr(imgs, "_search_ddg", lambda q, m, a: fake_results)

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(png))

    res = execute_image_search(_call(queries=["cat"]))
    assert res.status == "ok"
    assert res.image_paths is not None and len(res.image_paths) == 1
    saved = res.image_paths[0]
    assert saved.exists()
    assert saved.read_bytes() == png
    assert "saved" in res.output
    assert "downloaded 1" in res.output


def test_download_rejects_non_image(monkeypatch, tmp_workdir):
    fake_results = [_norm(image="http://a/fake.png")]
    monkeypatch.setattr(imgs, "_search_ddg", lambda q, m, a: fake_results)

    import httpx

    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _FakeStream(b"<html>not an image</html>"))

    res = execute_image_search(_call(queries=["x"]))
    assert res.status == "ok"
    assert res.image_paths is None  # ничего валидного не скачано
    assert "FAILED" in res.output
    assert "downloaded 0" in res.output
    assert "1 failed" in res.output
