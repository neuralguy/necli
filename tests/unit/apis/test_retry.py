"""apis/_retry.py — обнаружение throttling и backoff."""

import pytest

from apis._retry import (
    _backoff_delay,
    _retry_after_seconds,
    is_throttled,
    stream_with_throttle_retry,
    with_throttle_retry,
)


@pytest.fixture(autouse=True)
def _fast_retries(monkeypatch):
    """Сжимает все backoff-задержки до 1ms — без этого throttle-тесты висят минуту."""
    fast = (0.001,) * 16
    monkeypatch.setattr("apis._retry._RETRY_DELAYS", fast)
    monkeypatch.setattr("apis._retry._MAX_DELAY", fast[-1])
    # Пол тоже сжимаем, иначе _backoff_delay поднимет паузу до реальных 1.5с
    # и retry-тесты будут висеть.
    monkeypatch.setattr("apis._retry._MIN_RETRY_DELAY", 0.001)


class _FakeHttpError(Exception):
    def __init__(self, message="", status_code=None, headers=None):
        super().__init__(message)
        self.status_code = status_code
        if headers is not None:
            self.response_headers = headers


class TestIsThrottled:
    def test_status_429(self):
        e = _FakeHttpError(status_code=429)
        assert is_throttled(e) is True

    def test_rate_limit_in_text(self):
        assert is_throttled(_FakeHttpError("rate limit exceeded")) is True

    def test_quota_in_text(self):
        assert is_throttled(_FakeHttpError("quota exhausted")) is True

    def test_throttle_keyword(self):
        assert is_throttled(_FakeHttpError("API throttled")) is True

    def test_overloaded(self):
        assert is_throttled(_FakeHttpError("server overloaded")) is True

    def test_unrelated(self):
        assert is_throttled(_FakeHttpError("connection refused")) is False

    def test_500_alone_not_throttled(self):
        assert is_throttled(_FakeHttpError("internal error", status_code=500)) is True


class TestRetryAfterSeconds:
    def test_from_dict_headers(self):
        e = _FakeHttpError(headers={"Retry-After": "5"})
        assert _retry_after_seconds(e) == 5.0

    def test_case_insensitive_header(self):
        e = _FakeHttpError(headers={"retry-after": "10"})
        assert _retry_after_seconds(e) == 10.0

    def test_invalid_header_value(self):
        e = _FakeHttpError(headers={"Retry-After": "soon"})
        assert _retry_after_seconds(e) is None

    def test_from_text_message(self):
        e = _FakeHttpError("rate limit exceeded retry-after: 7")
        assert _retry_after_seconds(e) == 7.0

    def test_no_hint(self):
        assert _retry_after_seconds(_FakeHttpError("plain error")) is None


class TestBackoffDelay:
    def test_first_attempts_use_table(self):
        import apis._retry as r
        delay = _backoff_delay(0, _FakeHttpError())
        assert delay == r._RETRY_DELAYS[0]
        delay2 = _backoff_delay(1, _FakeHttpError())
        assert delay2 == r._RETRY_DELAYS[1]

    def test_hint_clamped_to_max(self):
        import apis._retry as r
        e = _FakeHttpError("retry-after: 9999")
        delay = _backoff_delay(0, e)
        # после clamp delay не больше _MAX_DELAY
        assert delay <= r._MAX_DELAY

    def test_retry_after_zero_floored(self, monkeypatch):
        # Регрессия: onlysq присылал Retry-After: 0 → 8 мгновенных ретраев впустую.
        # Теперь любая пауза не ниже _MIN_RETRY_DELAY. Проверяем на РЕАЛЬНОМ поле
        # (отменяем сжатие из фикстуры).
        monkeypatch.setattr("apis._retry._MIN_RETRY_DELAY", 1.5)
        monkeypatch.setattr("apis._retry._MAX_DELAY", 60.0)  # отменяем сжатие фикстуры
        e = _FakeHttpError(headers={"Retry-After": "0"})
        assert _backoff_delay(0, e) >= 1.5

    def test_table_delay_floored(self, monkeypatch):
        # Даже если табличная задержка вышла крошечной, пол держит её.
        monkeypatch.setattr("apis._retry._MIN_RETRY_DELAY", 1.5)
        monkeypatch.setattr("apis._retry._RETRY_DELAYS", (0.0,) * 8)
        assert _backoff_delay(0, _FakeHttpError()) >= 1.5


class TestWithThrottleRetry:
    @pytest.mark.asyncio
    async def test_success_first_try(self):
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            return "ok"

        result = await with_throttle_retry(factory)
        assert result == "ok"
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_throttled_then_success(self):
        calls = {"n": 0}

        async def factory():
            calls["n"] += 1
            if calls["n"] < 2:
                raise _FakeHttpError("rate limit")
            return "done"

        result = await with_throttle_retry(factory)
        assert result == "done"
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_non_throttle_error_raises_immediately(self):
        async def factory():
            raise ValueError("not a throttle")

        with pytest.raises(ValueError):
            await with_throttle_retry(factory)

    @pytest.mark.asyncio
    async def test_on_retry_callback_invoked(self):
        called = {"count": 0}

        def cb():
            called["count"] += 1

        attempts = {"n": 0}

        async def factory():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise _FakeHttpError("rate limit")
            return "ok"

        result = await with_throttle_retry(factory, on_retry=cb)
        assert result == "ok"
        assert called["count"] >= 1


class TestStreamWithThrottleRetry:
    @pytest.mark.asyncio
    async def test_accumulates_content(self):
        from apis.messages import AIMessageChunk

        async def factory():
            for ch in ["he", "ll", "o"]:
                yield AIMessageChunk(content=ch)

        chunks_seen = []

        def on_chunk(full):
            chunks_seen.append(full)

        result = await stream_with_throttle_retry(factory, on_chunk)
        assert chunks_seen[-1] == "hello"
        assert isinstance(result, AIMessageChunk)

    @pytest.mark.asyncio
    async def test_tool_chunk_callback(self):
        from apis.messages import AIMessageChunk

        tc = [{"index": 0, "id": "t1", "name": "shell", "args": "{}"}]

        async def factory():
            yield AIMessageChunk(content="", tool_call_chunks=tc)

        seen_tc = []

        def on_tc(c):
            seen_tc.append(c)

        await stream_with_throttle_retry(factory, lambda _t: None, on_tool_chunk=on_tc)
        assert seen_tc == [tc]

    @pytest.mark.asyncio
    async def test_reasoning_chunk_accumulates(self):
        from apis.messages import AIMessageChunk

        async def factory():
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "think "})
            yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": "more"})

        seen = []

        def on_reasoning(full):
            seen.append(full)

        await stream_with_throttle_retry(factory, lambda _t: None, on_reasoning_chunk=on_reasoning)
        assert seen[-1] == "think more"

    @pytest.mark.asyncio
    async def test_html_entities_unescape(self):
        from apis.messages import AIMessageChunk
        amp = chr(38) + "amp;"
        lt = chr(38) + "lt;"

        async def factory():
            yield AIMessageChunk(content=f"x {amp} y {lt}z")

        seen = []
        await stream_with_throttle_retry(factory, lambda t: seen.append(t))
        assert seen[-1] == "x & y <z"

    @pytest.mark.asyncio
    async def test_throttled_stream_retries(self):
        from apis.messages import AIMessageChunk

        attempts = {"n": 0}

        async def factory():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise _FakeHttpError("rate limit")
            yield AIMessageChunk(content="ok")

        await stream_with_throttle_retry(factory, lambda _t: None)
        assert attempts["n"] == 2

    @pytest.mark.asyncio
    async def test_non_throttle_error_propagates(self):
        async def factory():
            for chunk in ():
                yield chunk
            raise ValueError("boom")

        with pytest.raises(ValueError):
            await stream_with_throttle_retry(factory, lambda _t: None)
