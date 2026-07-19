"""agent/result_cache.py — кэш длинных tool outputs."""

from agent import result_cache


class TestStore:
    def setup_method(self):
        result_cache._cache.clear()

    def test_returns_id(self):
        rid = result_cache.store("hello")
        assert isinstance(rid, str)
        assert len(rid) == 10

    def test_idempotent(self):
        rid1 = result_cache.store("same content")
        rid2 = result_cache.store("same content")
        assert rid1 == rid2

    def test_different_content_different_id(self):
        a = result_cache.store("text A")
        b = result_cache.store("text B")
        assert a != b


class TestGet:
    def setup_method(self):
        result_cache._cache.clear()

    def test_roundtrip(self):
        rid = result_cache.store("payload data here")
        assert result_cache.get(rid) == "payload data here"

    def test_missing(self):
        assert result_cache.get("not_a_real_id") is None

    def test_move_to_end_on_get(self):
        a = result_cache.store("A")
        b = result_cache.store("B")
        # b — самый свежий
        result_cache.get(a)  # перетягивает a в конец
        keys = list(result_cache._cache.keys())
        assert keys[-1] == a
        assert keys[0] == b


class TestEviction:
    def setup_method(self):
        result_cache._cache.clear()

    def test_fifo_when_exceeding_max(self):
        # MAX_ENTRIES=200 — заполняем 201 уникальной записью
        for i in range(result_cache._MAX_ENTRIES + 5):
            result_cache.store(f"item_{i}")
        assert result_cache.size() == result_cache._MAX_ENTRIES
        # Первая запись должна быть вытеснена
        assert result_cache.get(result_cache._make_id("item_0")) is None
        # Последние записи доступны
        last = result_cache._MAX_ENTRIES + 4
        assert result_cache.get(result_cache._make_id(f"item_{last}")) is not None


class TestSize:
    def setup_method(self):
        result_cache._cache.clear()

    def test_empty(self):
        assert result_cache.size() == 0

    def test_after_stores(self):
        result_cache.store("a")
        result_cache.store("b")
        assert result_cache.size() == 2
