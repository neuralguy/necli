import sys
import types

from tools.web_search import execute_web_search

# ---------------- helpers ----------------

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

# ---------------- execute_web_search: no queries ----------------

def test_execute_no_queries(make_tool_call):
    call = make_tool_call("web_search", args={})
    res = execute_web_search(call)
    assert res.status == "error"
    assert res.exit_code == 1
    assert "No queries" in res.output

def test_execute_empty_queries(make_tool_call):
    call = make_tool_call("web_search", args={"queries": []})
    res = execute_web_search(call)
    assert res.status == "error"
    assert "No queries" in res.output or "No non-empty" in res.output

def test_execute_whitespace_queries(make_tool_call):
    call = make_tool_call("web_search", args={"queries": ["  ", ""]})
    res = execute_web_search(call)
    assert res.status == "error"
    assert "No non-empty" in res.output

# ---------------- execute_web_search: search mode (mocked DDGS) ----------------

def test_search_basic(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [
            {"title": "T1", "href": "http://1", "body": "snippet1"},
            {"title": "T2", "href": "http://2", "body": "snippet2"},
        ],
    )
    call = make_tool_call("web_search", args={"queries": ["hello"]})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert "[Query 1: hello]" in res.output
    assert "[0] T1" in res.output
    assert "http://1" in res.output
    assert "snippet1" in res.output
    assert "[1] T2" in res.output

def test_search_multiple_queries(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [
            {"title": "T1", "href": "http://1", "body": "snippet1"},
            {"title": "T2", "href": "http://2", "body": "snippet2"},
        ],
    )
    call = make_tool_call("web_search", args={"queries": ["first query", "second query"]})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert "[Query 1: first query]" in res.output
    assert "[Query 2: second query]" in res.output
    # Each query got the same results (mocked)
    assert res.output.count("[0] T1") == 2

def test_search_no_results(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, [])
    call = make_tool_call("web_search", args={"queries": ["nothing"]})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert "No results found." in res.output

def test_search_exception(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, None, raise_exc=RuntimeError("boom"))
    call = make_tool_call("web_search", args={"queries": ["fail"]})
    res = execute_web_search(call)
    assert res.status == "ok"  # per-query errors don't fail the whole call
    assert "Search failed: boom" in res.output

def test_search_one_fails_one_ok(make_tool_call, monkeypatch):
    call_count = [0]

    fake_mod = types.ModuleType("ddgs")

    class FakeDDGS:
        def text(self, query, max_results=5):
            call_count[0] += 1
            if query == "fail":
                raise RuntimeError("boom")
            return [{"title": "OK", "href": "http://ok", "body": "works"}]

    fake_mod.DDGS = FakeDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_mod)
    call = make_tool_call("web_search", args={"queries": ["fail", "ok"]})
    res = execute_web_search(call)
    assert res.status == "ok"
    assert "Search failed: boom" in res.output
    assert "[0] OK" in res.output

def test_search_truncates_to_5_queries(make_tool_call, monkeypatch):
    _install_fake_ddgs(monkeypatch, [{"title": "T", "href": "http://t", "body": "b"}])
    queries = [str(i) for i in range(7)]
    call = make_tool_call("web_search", args={"queries": queries})
    res = execute_web_search(call)
    assert res.status == "ok"

def test_search_link_fallback_field(make_tool_call, monkeypatch):
    _install_fake_ddgs(
        monkeypatch,
        [{"title": "T", "link": "http://viaLink", "body": "b"}],
    )
    call = make_tool_call("web_search", args={"queries": ["q"]})
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
    call = make_tool_call("web_search", args={"queries": ["q"], "max_results": "3"})
    execute_web_search(call)
    assert captured["max_results"] == 3
