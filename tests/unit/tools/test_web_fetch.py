import pytest

import tools.web_fetch as wf
from tools.web_fetch import (
    _cache_get,
    _cache_put,
    _fetch_cache,
    _fetch_pages,
    execute_web_fetch,
)

# ---------------- thread-safety & index coercion ----------------

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
    assert len(_fetch_cache) <= wf._CACHE_MAX_ENTRIES

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
    monkeypatch.setattr(wf, "_CACHE_TTL", 10)
    base = 1000.0
    monkeypatch.setattr(wf.time, "time", lambda: base)
    _cache_put("http://x", "v")
    monkeypatch.setattr(wf.time, "time", lambda: base + 11)
    assert _cache_get("http://x") is None
    assert "http://x" not in _fetch_cache

def test_cache_eviction(monkeypatch):
    monkeypatch.setattr(wf, "_CACHE_MAX_ENTRIES", 3)
    for i in range(5):
        _cache_put(f"http://{i}", str(i))
    assert len(_fetch_cache) == 3
    assert "http://0" not in _fetch_cache
    assert "http://4" in _fetch_cache

def test_cache_lru_move_to_end(monkeypatch):
    monkeypatch.setattr(wf, "_CACHE_MAX_ENTRIES", 3)
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
    monkeypatch.setattr(wf, "_fetch_page", lambda u: f"content::{u}")
    assert _fetch_pages(["http://a"]) == {"http://a": "content::http://a"}

def test_fetch_pages_multiple(monkeypatch):
    monkeypatch.setattr(wf, "_fetch_page", lambda u: u.upper())
    out = _fetch_pages(["http://a", "http://b"])
    assert out == {"http://a": "HTTP://A", "http://b": "HTTP://B"}

# ---------------- execute_web_fetch ----------------

def test_execute_fetch_no_urls(make_tool_call):
    call = make_tool_call("web_fetch", args={})
    res = execute_web_fetch(call)
    assert res.status == "error"
    assert res.exit_code == 1
    assert "No urls" in res.output

def test_execute_fetch_ok(make_tool_call, monkeypatch):
    monkeypatch.setattr(wf, "_fetch_pages", lambda urls, raw=False: {"http://a": "page A body"})
    call = make_tool_call("web_fetch", args={"urls": ["http://a"]})
    res = execute_web_fetch(call)
    assert res.status == "ok"
    assert res.exit_code == 0
    assert "=== http://a ===" in res.output
    assert "page A body" in res.output
    assert "1 url(s)" in res.command

def test_execute_fetch_multiple_urls(make_tool_call, monkeypatch):
    monkeypatch.setattr(
        wf, "_fetch_pages",
        lambda urls, raw=False: {u: f"content:{u}" for u in urls},
    )
    call = make_tool_call("web_fetch", args={"urls": ["http://a", "http://b"]})
    res = execute_web_fetch(call)
    assert res.status == "ok"
    assert "=== http://a ===" in res.output
    assert "=== http://b ===" in res.output
    assert "2 url(s)" in res.command

def test_execute_fetch_empty_content(make_tool_call, monkeypatch):
    monkeypatch.setattr(wf, "_fetch_pages", lambda urls, raw=False: {"http://a": None})
    call = make_tool_call("web_fetch", args={"urls": ["http://a"]})
    res = execute_web_fetch(call)
    assert "[empty or fetch failed]" in res.output

def test_execute_fetch_truncation(make_tool_call, monkeypatch):
    monkeypatch.setattr(wf, "_MAX_CONTENT_LENGTH", 10)
    big = "x" * 50
    monkeypatch.setattr(wf, "_fetch_pages", lambda urls, raw=False: {"http://a": big})
    call = make_tool_call("web_fetch", args={"urls": ["http://a"]})
    res = execute_web_fetch(call)
    assert "truncated, 50 chars total" in res.output

def test_execute_fetch_raw_mode(make_tool_call, monkeypatch):
    monkeypatch.setattr(wf, "_fetch_pages", lambda urls, raw=True: {"http://a": "<html>raw</html>"})
    call = make_tool_call("web_fetch", args={"urls": ["http://a"], "raw": True})
    res = execute_web_fetch(call)
    assert res.status == "ok"
    assert "<html>raw</html>" in res.output
    assert "raw" in res.command

def test_execute_fetch_urls_string(make_tool_call, monkeypatch):
    """urls as comma-separated string (backward compat in handler)."""
    monkeypatch.setattr(
        wf, "_fetch_pages",
        lambda urls, raw=False: {u: f"content:{u}" for u in urls},
    )
    call = make_tool_call("web_fetch", args={"urls": "http://a, http://b"})
    res = execute_web_fetch(call)
    assert res.status == "ok"
    assert "=== http://a ===" in res.output
    assert "=== http://b ===" in res.output
