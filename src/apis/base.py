"""Базовый HTTP-провайдер на httpx (без LangChain).

Реализует OpenAI-совместимый клиент со стримингом, native tool calls,
retry и backoff. Используется напрямую для custom провайдеров; openai_provider
тоже наследуется от него.

Интерфейс совместим с тем, что используется в проекте:
  - async ainvoke(messages, **kwargs) -> AIMessage
  - async astream(messages, **kwargs) -> AsyncIterator[AIMessageChunk]
  - bind_tools(tools, tool_choice=...) -> "bound" обёртка с теми же методами
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from apis.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
)
from logger import logger


class _RetryableStreamError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


def _is_transient_401(status_code: int, body: str) -> bool:
    """OnlySQ-прокси иногда отдаёт ложный 401 для Perplexity-моделей
    (sonar/pplx-*): upstream возвращает invalid_api_key, хотя ключ верный.
    Такой 401 транзиентный — ретраим. Реальный 401 нашего провайдера
    отличается текстом (нет упоминания perplexity)."""
    if status_code != 401:
        return False
    low = (body or "").lower()
    return "perplexity" in low or "invalid_api_key" in low or "invalid api key" in low


class _BoundProvider:
    """Обёртка над провайдером с забинженными tools.

    Подменяет ainvoke/astream — добавляет tools в каждый вызов.
    Атрибут streaming проксируется на underlying provider, чтобы внешний
    код мог переключать режим (см. agent_adapter fallback).
    """

    def __init__(self, provider: "BaseProvider", tools: List[dict], tool_choice: str = "auto"):
        self._provider = provider
        self._tools = tools
        self._tool_choice = tool_choice

    def __getattr__(self, item):
        return getattr(self._provider, item)

    @property
    def streaming(self) -> bool:
        return getattr(self._provider, "streaming", True)

    @streaming.setter
    def streaming(self, value: bool) -> None:
        self._provider.streaming = value

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        return await self._provider.ainvoke(
            messages, tools=self._tools, tool_choice=self._tool_choice, **kwargs,
        )

    def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        return self._provider.astream(
            messages, tools=self._tools, tool_choice=self._tool_choice, **kwargs,
        )

    def bind_tools(self, tools, tool_choice: str = "auto") -> "_BoundProvider":
        return _BoundProvider(self._provider, list(tools), tool_choice)


class BaseProvider:
    """Базовый класс для HTTP LLM-провайдеров (OpenAI-совместимый формат)."""

    # HTTP-коды для повтора запроса на транспортном уровне (httpx). Это НЕ то же,
    # что agent/messages.py:is_api_proxy_error — там текстовый детект уже
    # полученного proxy-ответа. Здесь набор шире (включает 429/504).
    # 504 (Gateway Timeout) и 429 (rate limit) тоже стоит ретраить.
    _RETRYABLE_STATUS_CODES: set[int] = {429, 502, 503, 504, 524}
    _BASE_RETRY_DELAY: float = 2.0
    _MAX_RETRY_DELAY: float = 8.0

    def __init__(
        self,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        timeout: int = 120,
        max_retries: int = 3,
        streaming: bool = True,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.streaming = streaming

        # Переопределяются наследниками / фабриками
        self._api_url: str = ""
        self._provider_name: str = "Provider"
        self._proxy: str = ""

    # ── Утилиты ──

    @staticmethod
    def _calc_backoff(attempt: int, base: float = 2.0, maximum: float = 8.0) -> float:
        return min(base * (2 ** attempt), maximum)

    def _calc_timeout(self, params: Dict[str, Any], base: int = 20) -> int:
        messages = params.get("messages", [])
        try:
            total_chars = len(json.dumps(messages, default=str))
        except (TypeError, ValueError):
            total_chars = sum(len(str(m)) for m in messages)
        return min(max(base + total_chars // 1000, self.timeout), 300)

    # ── Tool binding (заменяет LangChain bind_tools) ──

    def bind_tools(self, tools: List[dict], tool_choice: str = "auto") -> _BoundProvider:
        return _BoundProvider(self, list(tools), tool_choice)

    # ── Override в наследниках ──

    def _get_api_key(self) -> str:
        return ""

    def _get_url(self) -> str:
        return self._api_url

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_api_key()}",
            "Content-Type": "application/json",
        }

    def _build_params(self, **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "model": self.model,
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if (max_tokens := kwargs.get("max_tokens", self.max_tokens)) is not None:
            params["max_tokens"] = max_tokens
        return params

    # ── Главный API ──

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        params = self._build_params(**kwargs)
        params["messages"] = self._convert_messages(messages)
        if tools := kwargs.get("tools"):
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")
        return await self._http_post(params)

    async def astream(
        self, messages: List[BaseMessage], **kwargs,
    ) -> AsyncIterator[AIMessageChunk]:
        params = self._build_params(**kwargs)
        params["messages"] = self._convert_messages(messages)
        params["stream"] = True
        # OpenAI-совместимые провайдеры (включая onlysq) НЕ шлют usage в стриме
        # без этого флага → CLI не получает реальные prompt_tokens и откатывается
        # на эвристику (cl100k_base), которая в разы недосчитывает (700k→9k).
        params["stream_options"] = {"include_usage": True}
        if stop := kwargs.get("stop"):
            params["stop"] = stop
        if tools := kwargs.get("tools"):
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")

        last_error: Exception | None = None

        async def _retry_or_reraise(exc: Exception, reason: str, *, partial_aborts: bool) -> None:
            # Единая обработка ретраябельных ошибок стрима. partial_aborts=True
            # означает, что при уже отданной части ответа повтор продублирует
            # контент → пробрасываем наверх. Иначе спим перед следующей попыткой.
            if partial_aborts and yielded_any:
                logger.warning(
                    f"{self._provider_name} stream {reason} after partial yield, not retrying"
                )
                raise exc
            if attempt < self.max_retries - 1:
                delay = self._calc_backoff(attempt)
                logger.warning(
                    f"{self._provider_name} stream {reason} | "
                    f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                )
                await asyncio.sleep(delay)

        for attempt in range(self.max_retries):
            yielded_any = False
            try:
                async for chunk in self._astream_attempt(params):
                    yielded_any = True
                    yield chunk
                return
            except _RetryableStreamError as e:
                last_error = e
                await _retry_or_reraise(e, f"HTTP {e.status_code}", partial_aborts=True)
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                last_error = TimeoutError(f"Stream timeout: {e}")
                await _retry_or_reraise(last_error, "timeout", partial_aborts=False)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ProtocolError) as e:
                # Сервер оборвал SSE-стрим (peer closed / incomplete chunked read).
                last_error = e
                await _retry_or_reraise(e, f"dropped ({type(e).__name__}: {e})", partial_aborts=True)

        raise ValueError(
            f"{self._provider_name} stream error after {self.max_retries} attempts: {last_error}"
        )

    async def _astream_attempt(self, params: Dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout(params)
        full_reasoning = ""
        line_buffer = ""

        client_kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(dynamic_timeout, connect=30.0),
            "limits": httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0),
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream(
                "POST",
                self._get_url(),
                json=params,
                headers=self._get_headers(),
            ) as resp:
                if resp.status_code in self._RETRYABLE_STATUS_CODES:
                    error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                    raise _RetryableStreamError(
                        resp.status_code,
                        f"{self._provider_name} API Error {resp.status_code}: {error_text}",
                    )
                if resp.status_code != 200:
                    error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                    if _is_transient_401(resp.status_code, error_text):
                        raise _RetryableStreamError(
                            resp.status_code,
                            f"{self._provider_name} transient 401 (upstream): {error_text}",
                        )
                    raise ValueError(
                        f"{self._provider_name} API Error {resp.status_code}: {error_text}"
                    )

                async for raw_bytes in resp.aiter_bytes():
                    line_buffer += raw_bytes.decode("utf-8", errors="ignore")

                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if data_str == "[DONE]":
                            return

                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if "error" in chunk and isinstance(chunk["error"], (dict, str)):
                            err = chunk["error"]
                            if isinstance(err, dict):
                                msg = err.get("message", str(err))
                                code = err.get("code")
                            else:
                                msg = str(err)
                                code = None
                            try:
                                code = int(code)
                            except (TypeError, ValueError):
                                code = None
                            # Ложный 401 от прокси (Perplexity/invalid_api_key)
                            # детектим по тексту — поле code приходит в разном
                            # виде (int/str/None), полагаться на него нельзя.
                            if _is_transient_401(code or 401, msg):
                                raise _RetryableStreamError(
                                    401,
                                    f"{self._provider_name} transient 401 (upstream): {msg}",
                                )
                            raise ValueError(f"{self._provider_name} stream error: {msg}")

                        choices = chunk.get("choices", [])
                        if not choices:
                            # некоторые провайдеры шлют usage отдельным чанком
                            usage = chunk.get("usage")
                            if usage:
                                yield AIMessageChunk(
                                    content="",
                                    usage_metadata=self._convert_usage(usage),
                                )
                            continue

                        delta = choices[0].get("delta", {})
                        content = delta.get("content") or ""
                        reasoning = delta.get("reasoning_content") or ""
                        if reasoning:
                            full_reasoning += reasoning

                        tool_call_chunks = self._parse_streaming_tool_calls(delta.get("tool_calls"))

                        usage = chunk.get("usage")
                        usage_metadata = self._convert_usage(usage) if usage else {}

                        # ВАЖНО: reasoning отдаём ТОЛЬКО как per-delta кусок в
                        # additional_kwargs. При слиянии чанков (final_chunk + chunk)
                        # langchain конкатенирует строковые additional_kwargs →
                        # получается полный текст рассуждения. Дублировать его ещё и
                        # кумулятивно в response_metadata нельзя: при слиянии чанков
                        # кумулятивные значения складывались бы повторно (double-count).
                        yield AIMessageChunk(
                            content=content,
                            tool_call_chunks=tool_call_chunks,
                            additional_kwargs=({"reasoning_content": reasoning} if reasoning else {}),
                            response_metadata={
                                "finish_reason": choices[0].get("finish_reason"),
                            },
                            usage_metadata=usage_metadata,
                        )

    @staticmethod
    def _convert_usage(usage: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        return {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_token_details": usage.get("prompt_tokens_details") or {},
            "output_token_details": usage.get("completion_tokens_details") or {},
        }

    # ── Convert messages → OpenAI-compatible dicts ──

    def _convert_messages(self, messages: List[BaseMessage]) -> List[Dict[str, Any]]:
        result = []
        for msg in messages:
            role = msg.role or "user"
            content = msg.content

            if content is None:
                content = ""
            elif isinstance(content, list):
                has_image = any(
                    isinstance(p, dict) and p.get("type") == "image_url"
                    for p in content
                )
                if has_image:
                    normalized: list[Any] = []
                    for part in content:
                        if isinstance(part, str):
                            if part:
                                normalized.append({"type": "text", "text": part})
                        elif isinstance(part, dict):
                            normalized.append(part)
                    content = normalized
                else:
                    parts = []
                    for part in content:
                        if isinstance(part, str):
                            parts.append(part)
                        elif isinstance(part, dict) and "text" in part:
                            parts.append(part["text"])
                    content = "".join(parts)

            if isinstance(content, list):
                item: Dict[str, Any] = {"role": role, "content": content}
            else:
                item = {"role": role, "content": str(content)}

            if isinstance(msg, AIMessage):
                reasoning = ""
                add_kw = getattr(msg, "additional_kwargs", None) or {}
                if isinstance(add_kw, dict):
                    reasoning = add_kw.get("reasoning_content") or ""
                if not reasoning:
                    resp_meta = getattr(msg, "response_metadata", None) or {}
                    if isinstance(resp_meta, dict):
                        reasoning = resp_meta.get("reasoning_content") or ""
                if reasoning:
                    item["reasoning_content"] = reasoning

                if msg.tool_calls:
                    item["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"]),
                            },
                        }
                        for tc in msg.tool_calls
                    ]

            if isinstance(msg, ToolMessage):
                item["tool_call_id"] = msg.tool_call_id

            result.append(item)
        return result

    def _parse_streaming_tool_calls(self, raw: Any) -> List[Dict[str, Any]]:
        if not raw:
            return []
        result: List[Dict[str, Any]] = []
        for tc in raw:
            if not isinstance(tc, dict):
                continue
            func = tc.get("function") or {}
            result.append({
                "name": func.get("name") or None,
                "args": func.get("arguments") or "",
                "id": tc.get("id") or None,
                "index": tc.get("index", 0),
            })
        return result

    def _parse_tool_calls(self, raw_tool_calls: Any) -> List[Dict]:
        result = []
        for tc in raw_tool_calls or []:
            if isinstance(tc, dict):
                tc_id = tc.get("id", "")
                func = tc.get("function", {})
                func_name = func.get("name", "")
                func_args_str = func.get("arguments", "{}")
            else:
                continue
            try:
                args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
            except json.JSONDecodeError:
                args = {}
            result.append({"name": func_name, "args": args, "id": tc_id, "type": "tool_call"})
        return result

    def _parse_response(self, data: Dict[str, Any]) -> AIMessage:
        try:
            if "choices" not in data or not data["choices"]:
                raise ValueError("No choices in response")

            choice = data["choices"][0]
            message = choice.get("message", {})
            content = message.get("content", "") or ""

            raw_tool_calls = message.get("tool_calls")
            tool_calls = self._parse_tool_calls(raw_tool_calls) if raw_tool_calls else []

            usage = data.get("usage", {})
            usage_metadata = self._convert_usage(usage)
            response_metadata = {
                "model_name": data.get("model", self.model),
                "finish_reason": choice.get("finish_reason", "stop") or "stop",
                "reasoning_content": message.get("reasoning_content", "") or "",
            }

            return AIMessage(
                content=content,
                tool_calls=tool_calls,
                usage_metadata=usage_metadata,
                response_metadata=response_metadata,
                additional_kwargs=({"reasoning_content": response_metadata["reasoning_content"]}
                                   if response_metadata["reasoning_content"] else {}),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.error(
                f"Parse error {self._provider_name} | {type(e).__name__}: {e} | raw={str(data)[:500]}"
            )
            raise ValueError(
                f"{self._provider_name}: failed to parse response: {type(e).__name__}: {e}"
            ) from e

    async def _http_post(self, params: Dict[str, Any]) -> AIMessage:
        name = self._provider_name
        url = self._get_url()
        headers = self._get_headers()
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout(params)
        last_error: Exception | None = None
        attempt = 0

        client_kwargs: Dict[str, Any] = {
            "timeout": httpx.Timeout(dynamic_timeout, connect=30.0),
            "limits": httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0),
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        while attempt < self.max_retries:
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=params, headers=headers)
                if resp.status_code == 200:
                    break
                if resp.status_code in self._RETRYABLE_STATUS_CODES:
                    error_text = resp.text
                    last_error = ValueError(
                        f"{name} API Error {resp.status_code}: {error_text}"
                    )
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{name} HTTP {resp.status_code} | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                error_text = resp.text
                if _is_transient_401(resp.status_code, error_text):
                    last_error = ValueError(
                        f"{name} transient 401 (upstream): {error_text}"
                    )
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{name} transient 401 | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise ValueError(f"{name} API Error {resp.status_code}: {error_text}")
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                delay = self._calc_backoff(attempt)
                logger.warning(
                    f"{name} timeout | attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s | {e}"
                )
                last_error = TimeoutError(f"Request timeout: {e}")
                attempt += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
            except httpx.TransportError as e:
                delay = self._calc_backoff(attempt)
                logger.warning(
                    f"{name} transport error | attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s | {e}"
                )
                last_error = e
                attempt += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
        else:
            raise ValueError(
                f"{name} API Error after {self.max_retries} attempts: {last_error}"
            )

        # Парсинг вне retry-блока: его исключения (JSONDecodeError/KeyError)
        # не должны ретраиться — это баги, а не сетевые ошибки.
        return self._parse_response(resp.json())


__all__ = ["BaseProvider"]