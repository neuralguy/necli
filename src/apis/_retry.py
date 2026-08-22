"""Silent retry helper for API throttling."""

import asyncio
import inspect
import math
import re

import httpx

from logger import logger
from tools._html_unescape import maybe_unescape

_THROTTLE_CODE = 429
_RETRY_DELAYS = (2.0, 3.0, 5.0, 10.0, 15.0)
_MAX_RETRIES = len(_RETRY_DELAYS) + 1
_MAX_DELAY = _RETRY_DELAYS[-1]
# Пол на паузу между ретраями. Прокси (onlysq) присылает Retry-After: 0 →
# раньше это давало 8 ретраев за ~2мс (видно в логах "retry in 0.0s attempt 7/8"):
# попытки сгорали впустую, сервер не успевал остыть → запрос всё равно падал.
# Любую посчитанную паузу поднимаем минимум до этого значения.
_MIN_RETRY_DELAY = 2.0
_EMPTY_IDLE_RETRIES = 1
_PARTIAL_IDLE_RETRIES = 1


def _stream_idle_timeout() -> float:
    from config.settings import get

    value = get("stream_idle_timeout", 180)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 180.0
    timeout = float(value)
    if not math.isfinite(timeout) or timeout < 1 or timeout > 3600:
        return 180.0
    return timeout


class StreamIdleTimeout(TimeoutError):
    """Провайдер не прислал ни одного stream-чанка за idle-интервал."""


class StreamIncompleteError(ConnectionError):
    """Транспорт завершил stream без терминального события провайдера."""


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

_TRANSIENT_PROVIDER_KEYWORDS = (
    "peer closed connection without sending complete message body",
    "incomplete chunked read",
    "remoteprotocolerror",
    "readerror",
    "protocolerror",
    "connection reset",
    "connection aborted",
    "connection closed",
    "server disconnected",
    "stream closed",
    "no live api keys available",
    "please try again later",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
)
_TRANSIENT_EXCEPTION_NAMES = (
    "clientconnectionerror",
    "clientconnectorerror",
    "connectionclosed",
    "connectionerror",
    "connecterror",
    "connecttimeout",
    "incompleteread",
    "pooltimeout",
    "readtimeout",
    "serverdisconnected",
    "writetimeout",
)
_RETRY_AFTER_RE = re.compile(r"retry[- ]?after[\"'\s:=]+(\d+(?:\.\d+)?)", re.IGNORECASE)
_HTTP_STATUS_RE = re.compile(r"\b(?:api error|http)\s*(\d{3})\b", re.IGNORECASE)


# Статусы, которые ОДНОЗНАЧНО транзиентны (сервер просит подождать/недоступен).
# Текстовое сопоставление по _THROTTLE_KEYWORDS применяем ТОЛЬКО как fallback,
# когда статус неизвестен — иначе нерелевантная ошибка со словом "quota"
# в сообщении (напр. валидационная) вызовет ложный ретрай.
_RETRYABLE_STATUSES = frozenset(
    {_THROTTLE_CODE, 408, 425, 500, 502, 503, 504, 520, 522, 524, 529}
)


def is_throttled(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    if isinstance(status, int):
        # Известен явный статус — доверяем только ему, текст не смотрим.
        return status in _RETRYABLE_STATUSES
    if isinstance(
        exc,
        (
            asyncio.TimeoutError,
            ConnectionError,
            EOFError,
            httpx.TransportError,
        ),
    ):
        return True
    # Статус неизвестен (httpx TransportError и пр.) — fallback на ключевые слова.
    s_lower = str(exc).lower()
    exc_name = " ".join(cls.__name__.lower() for cls in type(exc).__mro__)
    compact_name = re.sub(r"[^a-z0-9]", "", exc_name)
    status_match = _HTTP_STATUS_RE.search(s_lower)
    if status_match and int(status_match.group(1)) in _RETRYABLE_STATUSES:
        return True
    return (
        any(k in s_lower for k in _THROTTLE_KEYWORDS)
        or any(k in compact_name for k in _TRANSIENT_EXCEPTION_NAMES)
        or any(
            k in s_lower
            or k in exc_name
            or re.sub(r"[^a-z0-9]", "", k) in compact_name
            for k in _TRANSIENT_PROVIDER_KEYWORDS
        )
    )


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


async def with_throttle_retry(coro_factory, on_retry=None, on_retry_attempt=None):
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_factory()
        except Exception as e:
            if is_throttled(e) and attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, e)
                from logger import warning

                warning(
                    "api.request.retry", attempt=attempt + 1, reason=str(e), delay=delay
                )
                logger.warning(
                    f"API throttled, retry in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
                )
                if on_retry_attempt:
                    on_retry_attempt(attempt + 1, _MAX_RETRIES)
                if on_retry:
                    on_retry()
                await asyncio.sleep(delay)
                continue
            from logger import error

            error("api.request.error", exception=str(e), attempts=attempt + 1)
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


async def stream_with_throttle_retry(
    astream_factory,
    on_chunk,
    on_retry=None,
    on_retry_attempt=None,
    on_tool_chunk=None,
    on_reasoning_chunk=None,
):
    """Streams text content with throttle retry.

    on_chunk(full_text) — called on each text update.
    on_tool_chunk(chunks) — called with raw tool_call_chunks for native tools API.
    on_reasoning_chunk(full_reasoning) — called on each reasoning_content update.
    Returns final AIMessageChunk (accumulated) so caller can extract tool_calls.

    Важно: native tool args НЕ сворачиваются через ``final_chunk + chunk`` на
    каждом SSE-чанке. Старый путь каждый раз копировал всю уже накопленную JSON
    строку и заново пытался json.loads() полного partial args внутри
    AIMessageChunk.__add__, что давало квадратичную стоимость на больших
    patch_file/create_file вызовах. Здесь храним args как список delta-fragments
    и материализуем JSON ровно один раз в конце стрима.
    """
    from apis.messages import AIMessageChunk, _tc_chunks_to_tool_calls

    def _accumulate_tool_chunks(
        acc: dict[int, dict],
        order: list[int],
        chunks: list | None,
    ) -> None:
        for ch in chunks or []:
            if not isinstance(ch, dict):
                continue
            idx = ch.get("index", 0)
            if not isinstance(idx, int):
                idx = 0
            if idx not in acc:
                acc[idx] = {
                    "id": None,
                    "name": None,
                    "parts": [],
                    "last_piece": "",
                    "cumulative": False,
                }
                order.append(idx)
            slot = acc[idx]
            if ch.get("id") and not slot["id"]:
                slot["id"] = ch["id"]
            if ch.get("name") and not slot["name"]:
                slot["name"] = ch["name"]

            args_piece = ch.get("args")
            if isinstance(args_piece, dict):
                import json

                args_piece = json.dumps(args_piece, ensure_ascii=False)
            if not isinstance(args_piece, str) or not args_piece:
                continue

            last_piece = slot["last_piece"]
            # Некоторые OpenAI-compatible прокси присылают не delta, а весь
            # накопленный args. Детектируем это по ростущему prefix и не
            # складываем O(N²) повторяющиеся копии.
            if (
                last_piece
                and last_piece.lstrip().startswith("{")
                and len(args_piece) >= len(last_piece)
                and args_piece.startswith(last_piece)
            ):
                slot["parts"] = [args_piece]
                slot["cumulative"] = True
            elif slot["cumulative"] and slot["parts"]:
                # После cumulative-последовательности провайдер теоретически
                # может продолжить обычными delta: сохраняем уже полный prefix
                # первым fragment и дописываем suffix.
                slot["parts"].append(args_piece)
            else:
                slot["parts"].append(args_piece)
            slot["last_piece"] = args_piece

    def _finalize_tool_chunks(acc: dict[int, dict], order: list[int]) -> list[dict]:
        out: list[dict] = []
        for idx in order:
            slot = acc[idx]
            out.append(
                {
                    "index": idx,
                    "id": slot["id"],
                    "name": slot["name"],
                    "args": "".join(slot["parts"]),
                }
            )
        return out

    def _result_chunk(
        *,
        full_text: str,
        full_reasoning: str,
        tool_acc: dict[int, dict],
        tool_order: list[int],
        latest_direct_tool_calls: list,
        latest_usage: dict,
        merged_additional_kwargs: dict,
        merged_response_metadata: dict,
        saw_chunk: bool,
    ):
        import json

        final_tc_chunks = _finalize_tool_chunks(tool_acc, tool_order)
        if merged_response_metadata.get("stream_incomplete"):
            completed_chunks = []
            for tool_chunk in final_tc_chunks:
                try:
                    parsed_args = json.loads(tool_chunk.get("args") or "")
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(parsed_args, dict):
                    completed_chunks.append(tool_chunk)
            final_tc_chunks = completed_chunks
        final_tool_calls = _tc_chunks_to_tool_calls(final_tc_chunks)
        if not final_tool_calls and latest_direct_tool_calls:
            final_tool_calls = latest_direct_tool_calls
        if full_reasoning:
            merged_additional_kwargs["reasoning_content"] = full_reasoning
        if not saw_chunk:
            return AIMessageChunk(content=full_text)
        return AIMessageChunk(
            content=full_text,
            tool_calls=final_tool_calls,
            tool_call_chunks=final_tc_chunks,
            usage_metadata=latest_usage,
            additional_kwargs=merged_additional_kwargs,
            response_metadata=merged_response_metadata,
        )

    async def _notify_retry(exc: Exception, current_attempt: int) -> None:
        delay = _backoff_delay(current_attempt, exc)
        from logger import warning

        warning(
            "api.request.retry",
            attempt=current_attempt + 1,
            reason=str(exc),
            delay=delay,
        )
        logger.warning(
            "API stream interrupted, retry in {:.1f}s (attempt {}/{}): {}",
            delay,
            current_attempt + 1,
            _MAX_RETRIES,
            exc,
        )
        if on_retry_attempt:
            on_retry_attempt(current_attempt + 1, _MAX_RETRIES)
        if on_retry:
            on_retry()
        await asyncio.sleep(delay)

    # Между ретраями on_chunk получает ПОЛНЫЙ текст с нуля. Чтобы при повторе
    # после частичного стрима не «откатить» UI назад и не продублировать вывод,
    # эмитим on_chunk только когда накопленный текст длиннее уже отданного.
    emitted_text_len = 0
    attempt = 0
    empty_idle_retries = 0
    partial_idle_retries = 0
    best_partial = None
    while attempt < _MAX_RETRIES:
        full_text = ""
        full_reasoning = ""
        tool_acc: dict[int, dict] = {}
        tool_order: list[int] = []
        merged_response_metadata: dict = {}
        merged_additional_kwargs: dict = {}
        latest_usage: dict = {}
        latest_direct_tool_calls: list = []
        saw_chunk = False
        idle_timeout = _stream_idle_timeout()
        stream = astream_factory()
        iterator = stream.__aiter__()
        stream_idle = False
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        iterator.__anext__(), timeout=idle_timeout
                    )
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    stream_idle = True
                    break
                saw_chunk = True
                # Unescape tool_call_chunks args ДО аккумуляции, чтобы
                # on_tool_chunk и финальный tool_call видели одинаковые args.
                tc_chunks = getattr(chunk, "tool_call_chunks", None)
                if tc_chunks:
                    for ch in tc_chunks:
                        if isinstance(ch, dict):
                            args = ch.get("args")
                            if isinstance(args, str) and args:
                                new_args = maybe_unescape(args)
                                if new_args is not args:
                                    ch["args"] = new_args
                    _accumulate_tool_chunks(tool_acc, tool_order, list(tc_chunks))
                direct_tool_calls = getattr(chunk, "tool_calls", None) or []
                if direct_tool_calls:
                    latest_direct_tool_calls = list(direct_tool_calls)

                if tc_chunks and on_tool_chunk:
                    on_tool_chunk(list(tc_chunks))

                add_kw = getattr(chunk, "additional_kwargs", None) or {}
                if isinstance(add_kw, dict):
                    for k, v in add_kw.items():
                        if k != "reasoning_content":
                            merged_additional_kwargs[k] = v
                    r_piece = add_kw.get("reasoning_content") or ""
                    if r_piece:
                        full_reasoning = _merge_stream_text(full_reasoning, r_piece)
                        if on_reasoning_chunk:
                            on_reasoning_chunk(full_reasoning)

                response_meta = getattr(chunk, "response_metadata", None) or {}
                if isinstance(response_meta, dict):
                    merged_response_metadata.update(response_meta)
                usage = getattr(chunk, "usage_metadata", None) or {}
                if usage:
                    latest_usage = usage

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
                        callback_result = on_chunk(full_text)
                        if inspect.isawaitable(callback_result):
                            await callback_result

            if stream_idle:
                aclose = getattr(iterator, "aclose", None)
                if callable(aclose):
                    await aclose()
                if not saw_chunk:
                    if empty_idle_retries < _EMPTY_IDLE_RETRIES:
                        empty_idle_retries += 1
                        logger.warning(
                            "API stream idle for {:.1f}s before first chunk; retrying once",
                            idle_timeout,
                        )
                        continue
                    raise StreamIdleTimeout(
                        f"API stream stalled: no chunks for "
                        f"{idle_timeout:.0f}s after retry"
                    )
                merged_response_metadata["stream_incomplete"] = True
                logger.warning(
                    "API stream idle for {:.1f}s after partial response",
                    idle_timeout,
                )
            result = _result_chunk(
                full_text=full_text,
                full_reasoning=full_reasoning,
                tool_acc=tool_acc,
                tool_order=tool_order,
                latest_direct_tool_calls=latest_direct_tool_calls,
                latest_usage=latest_usage,
                merged_additional_kwargs=merged_additional_kwargs,
                merged_response_metadata=merged_response_metadata,
                saw_chunk=saw_chunk,
            )
            if not merged_response_metadata.get("stream_incomplete"):
                return result

            if best_partial is None or len(full_text) > len(best_partial.content or ""):
                best_partial = result
            can_retry = attempt < _MAX_RETRIES - 1
            if stream_idle:
                can_retry = can_retry and partial_idle_retries < _PARTIAL_IDLE_RETRIES
                partial_idle_retries += 1
            if not can_retry:
                return best_partial

            incomplete = StreamIncompleteError(
                "API stream ended without a terminal event"
            )
            await _notify_retry(incomplete, attempt)
            attempt += 1
            continue
        except Exception as e:
            if isinstance(e, StreamIdleTimeout):
                from logger import error

                error("api.request.error", exception=str(e), attempts=attempt + 1)
                raise
            retryable = is_throttled(e)
            if retryable and saw_chunk:
                merged_response_metadata["stream_incomplete"] = True
                partial = _result_chunk(
                    full_text=full_text,
                    full_reasoning=full_reasoning,
                    tool_acc=tool_acc,
                    tool_order=tool_order,
                    latest_direct_tool_calls=latest_direct_tool_calls,
                    latest_usage=latest_usage,
                    merged_additional_kwargs=merged_additional_kwargs,
                    merged_response_metadata=merged_response_metadata,
                    saw_chunk=True,
                )
                if best_partial is None or len(partial.content or "") > len(
                    best_partial.content or ""
                ):
                    best_partial = partial
            if retryable and attempt < _MAX_RETRIES - 1:
                await _notify_retry(e, attempt)
                attempt += 1
                continue
            if retryable and best_partial is not None:
                logger.warning(
                    "API stream retries exhausted; returning the longest partial response"
                )
                return best_partial
            from logger import error

            error("api.request.error", exception=str(e), attempts=attempt + 1)
            raise
