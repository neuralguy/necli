import sys
import types

import pytest

import tools.web_search as ws
from tools.web_search import (
    _cache_get,
    _cache_put,
    _coerce_indices,
    _do_url_fetch,
    _fetch_cache,
    _fetch_pages,
    execute_web_search,
)

# ---------------- thread-safety & index coercion (added) ----------------

def test_cache_concurrent_mutation_thread_safe():
    import threading as _threading

    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(200):
                _cache_put(f"http://t{n}-{i}", str(i))
                _cache_get(f"http://t{n}-{i}")
        except BaseException as exc:
            errors.append(exc)

    threads = [_threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert len(_fetch_cache) <= ws._CACHE_MAX_ENTRIES

def test_coerce_indices_ints():
    assert _coerce_indices([0, 2, 5]) == {0, 2, 5}

def test_coerce_indices_strings():
    assert _coerce_indices(["0", "1", "3"]) == {0, 1, 3}

def test_coerce_indices_mixed_and_single():
    assert _coerce_indices(["0", 2]) == {0, 2}
    assert _coerce_indices(3) == {3}
    assert _coerce_indices("4") == {4}

def test_coerce_indices_invalid_ignored():
    assert _coerce_indices(["x", None, [], "1.5", 2]) == {2}

def test_coerce_indices_booleans_ignored():
    assert _coerce_indices([True, False, 1]) == {1}

def test_coerce_indices_empty_and_none():
    assert _coerce_indices([]) == set()
    assert _coerce_indices(None) == set()

@pytest.fixture(autouse=True)
def clear_cache():
    _fetch_cache.clear()
    yield
    _fetch_cache.clear()

# ---------------- cache ----------------

def test_cache_put_get_roundtrip():
    _cache_put("http://a", "text-a")
    assert _cache_get("http://a") == "text-a"

def test_cache_miss_returns_none():
    assert _cache_get("http://missing") is None

def test_cache_expiry(monkeypatch):
    monkeypatch.setattr(ws, "_CACHE_TTL", 10)
    base = 1000.0
    monkeypatch.setattr(ws.time, "time", lambda: base)
    _cache_put("http://x", "v")
    monkeypatch.setattr(ws.time, "time", lambda: base + 11)
    assert _cache_get("http://x") is None
    assert "http://x" not in _fetch_cache

def test_cache_eviction(monkeypatch):
    monkeypatch.setattr(ws, "_CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        _cache_put(f"http://{i}", str(i))
    assert len(_fetch_cache) == 3
    assert "http://0" not in _fetch_cache
    assert "http://4" in _fetch_cache

def test_cache_lru_move_to_end(monkeypatch):
    monkeypatch.setattr(ws, "_CACHE_MAX_ENTRIES", 3)
    _cache_put("http://0", "0")
    _cache_put("http://1", "1")
    _cache_put("http://2", "2")
    # touch 0 so it becomes most-recent
    assert _cache_get("http://0") == "0"
    _cache_put("http://3", "3")  # should evict 1 (oldest), not 0
    assert "http://1" not in _fetch_cache
    assert "http://0" in _fetch_cache

# ---------------- _fetch_pages ----------------

def test_fetch_pages_empty():
    assert _fetch_pages([]) == {}

def test_fetch_pages_single(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_page", lambda u: f"content::{u}")
    assert _fetch_pages(["http://a"]) == {"http://a": "content::http://a"}

def test_fetch_pages_multiple(monkeypatch):
    monkeypatch.setattr(ws, "_fetch_page", lambda u: u.upper())
    out = _fetch_pages(["http://a", "http://b"])
    assert out == {"http://a": "HTTP://A", "http://b": "HTTP://B"}

# ---------------- _do_url_fetch ----------------

def test_do_url_fetch_no_urls(make_tool_call):
    call = make_tool_call("web_search", args={})
    res = _do_url_fetch(call, [])
    assert res.status == "error"
    assert res.exit_code == 1
    assert "url" in res.output.lower()

def test_do_url_fetch_ok(make_tool_call, monkeypatch):
    monkeypatch.setattr(ws, "_fetch_pages", lambda urls, raw=False: {"http://a": "page A body"})
    call = make_tool_call("web_search", args={})
    res = _do_url_fetch(call, ["http://a"])
    assert res.status == "ok"
    assert res.exit_code == 0
    assert "=== http://a ===" in res.output
    assert "page A body" in res.output
    assert "1 url(s)" in res.command

def test_do_url_fetch_empty_content(make_tool_call, monkeypatch):
    monkeypatch.setattr(ws, "_fetch_pages", lambda urls, raw=False: {"http://a": None})
    call = make_tool_call("web_search", args={})
    res = _do_url_fetch(call, ["http://a"])
    assert "[empty or fetch failed]" in res.output

def test_do_url_fetch_truncation(make_tool_call, monkeypatch):
    monkeypatch.setattr(ws, "_MAX_CONTENT_LENGTH", 10)
    big = "x" * 50
    monkeypatch.setattr(ws, "_fetch_pages", lambda urls, raw=False: {"http://a": big})
    call = make_tool_call("web_search", args={})
    res = _do_url_fetch(call, ["http://a"])
    assert "truncated, 50 chars total" in res.output

# ---------------- execute_web_search: url mode ----------------

def test_execute_url_mode_single(make_tool_call, monkeypatch):
    captured = {}

    def fake_do(call, urls, raw=False):
        captured["urls"] = urls
        from tools.models import ToolResult
        return ToolResult(name="web_search", status="ok", output="done", command="web_fetch")

    monkeypatch.setattr(ws, "_do_url_fetch", fake_do)
    call = make_tool_call("web_search", args={"url": "http://one"})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert captured["urls"] == ["http://one"]

def test_execute_url_mode_urls_string(make_tool_call, monkeypatch):
    captured = {}

    def fake_do(call, urls, raw=False):
        captured["urls"] = urls
        from tools.models import ToolResult
        return ToolResult(name="web_search", status="ok", output="done", command="web_fetch")

    monkeypatch.setattr(ws, "_do_url_fetch", fake_do)
    call = make_tool_call("web_search", args={"urls": "http://a, http://b"})
    execute_web_search(call)
    assert captured["urls"] == ["http://a", "http://b"]

def test_execute_url_mode_combined(make_tool_call, monkeypatch):
    captured = {}

    def fake_do(call, urls, raw=False):
        captured["urls"] = urls
        from tools.models import ToolResult
        return ToolResult(name="web_search", status="ok", output="done", command="web_fetch")

    monkeypatch.setattr(ws, "_do_url_fetch", fake_do)
    call = make_tool_call("web_search", args={"url": "http://x", "urls": ["http://y"]})
    execute_web_search(call)
    assert captured["urls"] == ["http://x", "http://y"]

# ---------------- execute_web_search: no query ----------------

def test_execute_no_query(make_tool_call):
    call = make_tool_call("web_search", args={}, command=" ")
    res = execute_web_search(call)
    assert res.status == "error"
    assert res.exit_code == 1
    assert "No query" in res.output

# ---------------- execute_web_search: search mode (mocked DDGS) ----------------

def _install_fake_ddgs(monkeypatch, results, raise_exc=None):
    """Устанавливает фейковый модуль ddgs с DDGS().text(...)."""
    fake_mod = types.ModuleType("ddgs")

    class FakeDDGS:
        def text(self, query, max_results=5):
            if raise_exc is not None:
                raise raise_exc
            return results

    fake_mod.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)

def test_search_basic(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [
            {"title": "T1", "href": "http://1", "body": "snippet1"},
            {"title": "T2", "href": "http://2", "body": "snippet2"},
        ],
    )
    call = make_tool_call("web_search", args={"query": "hello"})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert res.command == "hello"
    assert "[0] T1" in res.output
    assert "http://1" in res.output
    assert "snippet1" in res.output
    assert "[1] T2" in res.output

def test_search_query_from_command(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, [{"title": "X", "href": "http://x", "body": "b"}])
    call = make_tool_call("web_search", args={}, command="fallback query")
    res = execute_web_search(call)
    assert res.status == "ok"
    assert res.command == "fallback query"

def test_search_no_results(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, [])
    call = make_tool_call("web_search", args={"query": "nothing"})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert res.output == "No results found."

def test_search_exception(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, None, raise_exc=RuntimeError("boom"))
    call = make_tool_call("web_search", args={"query": "fail"})
    res = execute_web_search(call)
    assert res.status == "error"
    assert res.exit_code == 1
    assert "boom" in res.output

def test_search_with_fetch_content(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [{"title": "T1", "href": "http://1", "body": "snip"}],
    )
    monkeypatch.setattr(ws, "_fetch_pages", lambda urls: {"http://1": "FULL PAGE TEXT"})
    call = make_tool_call("web_search", args={"query": "q", "fetch": True})
    res = execute_web_search(call)
    assert "--- Page content ---" in res.output
    assert "FULL PAGE TEXT" in res.output

def test_search_fetch_indices(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [
            {"title": "T0", "href": "http://0", "body": "b0"},
            {"title": "T1", "href": "http://1", "body": "b1"},
        ],
    )
    monkeypatch.setattr(ws, "_fetch_pages", lambda urls: {"http://1": "PAGE1"})
    call = make_tool_call("web_search", args={"query": "q", "fetch_indices": [1]})
    res = execute_web_search(call)
    assert "PAGE1" in res.output
    # index 0 should not have page content
    assert res.output.count("--- Page content ---") == 1

def test_search_link_fallback_field(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [{"title": "T", "link": "http://viaLink", "body": "b"}],
    )
    call = make_tool_call("web_search", args={"query": "q"})
    res = execute_web_search(call)
    assert "http://viaLink" in res.output

def test_search_max_results_int_coercion(make_tool_call, monkeypatch):
    captured = {}

    fake_mod = types.ModuleType("ddgs")

    class FakeDDGS:
        def text(self, query, max_results=5):
            captured["max_results"] = max_results
            return [{"title": "T", "href": "http://1", "body": "b"}]

    fake_mod.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    call = make_tool_call("web_search", args={"query": "q", "max_results": "3"})
    execute_web_search(call)
    assert captured["max_results"] == 3
