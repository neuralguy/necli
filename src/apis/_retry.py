"""Silent retry helper for API throttling."""

import asyncio
import inspect
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
    "stream error",
    "no live api keys available",
    "please try again later",
    "temporarily unavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
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
    exc_name = type(exc).__name__.lower()
    return any(k in s_lower for k in _THROTTLE_KEYWORDS) or any(
        k in s_lower or k in exc_name for k in _TRANSIENT_PROVIDER_KEYWORDS
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


async def with_throttle_retry(coro_factory, on_retry=None):
    for attempt in range(_MAX_RETRIES):
        try:
            return await coro_factory()
        except Exception as e:
            if is_throttled(e) and attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, e)
                from logger import warning

                warning("api.request.retry", attempt=attempt + 1, reason=str(e), delay=delay)
                logger.warning(
                    f"API throttled, retry in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
                )
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
    astream_factory, on_chunk, on_retry=None, on_tool_chunk=None, on_reasoning_chunk=None
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

    # Между ретраями on_chunk получает ПОЛНЫЙ текст с нуля. Чтобы при повторе
    # после частичного стрима не «откатить» UI назад и не продублировать вывод,
    # эмитим on_chunk только когда накопленный текст длиннее уже отданного.
    emitted_text_len = 0
    for attempt in range(_MAX_RETRIES):
        full_text = ""
        full_reasoning = ""
        tool_acc: dict[int, dict] = {}
        tool_order: list[int] = []
        merged_response_metadata: dict = {}
        merged_additional_kwargs: dict = {}
        latest_usage: dict = {}
        latest_direct_tool_calls: list = []
        saw_chunk = False
        try:
            async for chunk in astream_factory():
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

            final_tc_chunks = _finalize_tool_chunks(tool_acc, tool_order)
            final_tool_calls = _tc_chunks_to_tool_calls(final_tc_chunks)
            if not final_tool_calls and latest_direct_tool_calls:
                final_tool_calls = latest_direct_tool_calls
            if full_reasoning:
                merged_additional_kwargs["reasoning_content"] = full_reasoning
            return (
                AIMessageChunk(
                    content=full_text,
                    tool_calls=final_tool_calls,
                    tool_call_chunks=final_tc_chunks,
                    usage_metadata=latest_usage,
                    additional_kwargs=merged_additional_kwargs,
                    response_metadata=merged_response_metadata,
                )
                if saw_chunk
                else AIMessageChunk(content=full_text)
            )
        except Exception as e:
            if is_throttled(e) and attempt < _MAX_RETRIES - 1:
                delay = _backoff_delay(attempt, e)
                from logger import warning

                warning("api.request.retry", attempt=attempt + 1, reason=str(e), delay=delay)
                logger.warning(
                    f"API stream throttled, retry in {delay:.1f}s (attempt {attempt + 1}/{_MAX_RETRIES}): {e}"
                )
                if on_retry:
                    on_retry()
                await asyncio.sleep(delay)
                continue
            from logger import error

            error("api.request.error", exception=str(e), attempts=attempt + 1)
            raise
