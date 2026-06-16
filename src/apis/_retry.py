"""Silent retry helper for API throttling."""

import asyncio
import re

from logger import logger
from tools._html_unescape import maybe_unescape

_THROTTLE_CODE = 429
_RETRY_DELAYS = (1.0, 2.0, 3.0, 10.0, 15.0, 30.0, 60.0)
_MAX_RETRIES = len(_RETRY_DELAYS) + 1
_MAX_DELAY = _RETRY_DELAYS[-1]
# Пол на паузу между ретраями. Прокси (onlysq) присылает Retry-After: 0 →
# раньше это давало 8 ретраев за ~2мс (видно в логах "retry in 0.0s attempt 7/8"):
# попытки сгорали впустую, сервер не успевал остыть → запрос всё равно падал.
# Любую посчитанную паузу поднимаем минимум до этого значения.
_MIN_RETRY_DELAY = 1.5

_THROTTLE_KEYWORDS = (
    "rate limit",
    "rate-limit",
    "ratelimit",
    "too many requests",
    "quota",
    "throttle",
    "throttled",
    "overloaded",
)
_RETRY_AFTER_RE = re.compile(r"retry[- ]?after[\"'\s:=]+(\d+(?:\.\d+)?)", re.IGNORECASE)


# Статусы, которые ОДНОЗНАЧНО транзиентны (сервер просит подождать/недоступен).
# Текстовое сопоставление по _THROTTLE_KEYWORDS применяем ТОЛЬКО как fallback,
# когда статус неизвестен — иначе нерелевантная ошибка со словом "quota"
# в сообщении (напр. валидационная) вызовет ложный ретрай.
_RETRYABLE_STATUSES = frozenset({_THROTTLE_CODE, 500, 502, 503, 504, 529})

def is_throttled(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if isinstance(status, int):
        # Известен явный статус — доверяем только ему, текст не смотрим.
        return status in _RETRYABLE_STATUSES
    # Статус неизвестен (httpx TransportError и пр.) — fallback на ключевые слова.
    s_lower = str(exc).lower()
    if any(k in s_lower for k in _THROTTLE_KEYWORDS):
        return True
    return False


def _retry_after_seconds(exc: Exception) -> float | None:
    headers = getattr(exc, "response_headers", None) or getattr(exc, "headers", None)
    if isinstance(headers, dict):
        for k, v in headers.items():
            if str(k).lower() == "retry-after":
                try:
                    return float(v)
                except (TypeError, ValueError):
                    break
    m = _RETRY_AFTER_RE.search(str(exc))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _backoff_delay(attempt: int, exc: Exception) -> float:
    hint = _retry_after_seconds(exc)
    if hint is not None:
        # Уважаем Retry-After, но не ниже пола: hint=0 от прокси не должен
        # превращаться в мгновенный ретрай-впустую.
        return min(max(hint, _MIN_RETRY_DELAY), _MAX_DELAY)
    idx = min(attempt, len(_RETRY_DELAYS) - 1)
    return max(_RETRY_DELAYS[idx], _MIN_RETRY_DELAY)


async def with_throttle_retry(coro_factory, on_retry=None):
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_factory()
        except Exception as e:
            if is_throttled(e) and attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, e)
                logger.warning(
                    f"API throttled, retry in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
                )
                if on_retry:
                    on_retry()
                await asyncio.sleep(delay)
                continue
            raise
    # Защита от неявного возврата None (например, если _MAX_RETRIES <= 0):
    # вызывающий код ожидает результат корутины, иначе словит NoneType-дереф.
    raise RuntimeError("with_throttle_retry: retries exhausted without result")


def _merge_stream_text(current: str, piece: str) -> str:
    if not piece:
        return current
    if piece == current:
        return current
    if current and piece.startswith(current):
        return piece
    return current + piece


async def stream_with_throttle_retry(astream_factory, on_chunk, on_retry=None, on_tool_chunk=None, on_reasoning_chunk=None):
    """Streams text content with throttle retry.

    on_chunk(full_text) — called on each text update.
    on_tool_chunk(chunks) — called with raw tool_call_chunks for native tools API.
    on_reasoning_chunk(full_reasoning) — called on each reasoning_content update.
    Returns final AIMessageChunk (accumulated) so caller can extract tool_calls.
    """
    from apis.messages import AIMessageChunk
    # Между ретраями on_chunk получает ПОЛНЫЙ текст с нуля. Чтобы при повторе
    # после частичного стрима не «откатить» UI назад и не продублировать вывод,
    # эмитим on_chunk только когда накопленный текст длиннее уже отданного.
    emitted_text_len = 0
    for attempt in range(_MAX_RETRIES):
        full_text = ""
        full_reasoning = ""
        final_chunk = None
        try:
            async for chunk in astream_factory():
                try:
                    if final_chunk is None:
                        final_chunk = chunk
                    else:
                        try:
                            final_chunk = final_chunk + chunk
                        except Exception as merge_e:
                            logger.warning(
                                "stream chunk merge failed: %s: %s",
                                type(merge_e).__name__, merge_e,
                            )
                            final_chunk = chunk
                except KeyError as ke:
                    logger.error(
                        "stream chunk KeyError on key %r — chunk=%r",
                        ke.args[0] if ke.args else None,
                        getattr(chunk, "__dict__", chunk),
                        exc_info=True,
                    )
                    continue

                tc_chunks = getattr(chunk, "tool_call_chunks", None)
                if tc_chunks and on_tool_chunk:
                    # Прокси (OnlySQ и др.) html-эскейпят аргументы tool_calls
                    # точно так же, как и текстовый content. Декодируем
                    # на лету, иначе json.loads(args) упадёт на "...".
                    decoded_chunks = []
                    for ch in tc_chunks:
                        if isinstance(ch, dict):
                            args = ch.get("args")
                            if isinstance(args, str) and args:
                                new_args = maybe_unescape(args)
                                if new_args is not args:
                                    ch = {**ch, "args": new_args}
                        decoded_chunks.append(ch)
                    on_tool_chunk(decoded_chunks)

                if on_reasoning_chunk:
                    r_piece = ""
                    add_kw = getattr(chunk, "additional_kwargs", None) or {}
                    if isinstance(add_kw, dict):
                        r_piece = add_kw.get("reasoning_content") or ""
                    if r_piece:
                        full_reasoning = _merge_stream_text(full_reasoning, r_piece)
                        on_reasoning_chunk(full_reasoning)

                content = chunk.content if hasattr(chunk, "content") else str(chunk)
                if isinstance(content, list):
                    parts = []
                    for part in content:
                        if isinstance(part, str):
                            parts.append(part)
                        elif isinstance(part, dict):
                            text = part.get("text")
                            if isinstance(text, str):
                                parts.append(text)
                    content = "".join(parts)
                elif not isinstance(content, str):
                    content = str(content)
                if content:
                    content = maybe_unescape(content)
                    full_text = _merge_stream_text(full_text, content)
                    if len(full_text) > emitted_text_len:
                        emitted_text_len = len(full_text)
                        on_chunk(full_text)
            if final_chunk is None:
                final_chunk = AIMessageChunk(content=full_text)
            return final_chunk
        except Exception as e:
            if is_throttled(e) and attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, e)
                logger.warning(
                    f"API stream throttled, retry in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
                )
                if on_retry:
                    on_retry()
                await asyncio.sleep(delay)
                continue
            raise
