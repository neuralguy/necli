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
from collections.abc import AsyncIterator
from typing import Any

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


def _format_api_error(provider_name: str, status_code: int, body: str, content_type: str = "") -> str:
    text = (body or "").strip()
    low_text = text.lower()
    low_prefix = low_text[:500]
    low_content_type = (content_type or "").lower()
    is_html = "text/html" in low_content_type or low_prefix.startswith(("<!doctype html", "<html"))
    is_cloudflare = "cloudflare" in low_text or "cf-error" in low_text

    if is_html:
        if is_cloudflare:
            detail = "Cloudflare returned an HTML error page"
            ray_marker = "Cloudflare Ray ID:"
            if ray_marker in text:
                ray_tail = text.split(ray_marker, 1)[1]
                ray_id = ray_tail.split("</", 1)[0].strip()
                ray_id = " ".join(ray_id.replace("<strong class=\"font-semibold\">", "").replace("</strong>", "").split())
                if ray_id:
                    detail += f" (Ray ID: {ray_id})"
            if 500 <= status_code <= 599:
                detail += "; the upstream host is failing, try again later"
        else:
            detail = "server returned HTML instead of JSON/SSE; check base_url/path"
        return f"{provider_name} API Error {status_code}: {detail}"

    if len(text) > 1000:
        text = text[:1000].rstrip() + "..."
    return f"{provider_name} API Error {status_code}: {text}"


class _BoundProvider:
    """Обёртка над провайдером с забинженными tools.

    Подменяет ainvoke/astream — добавляет tools в каждый вызов.
    Атрибут streaming проксируется на underlying provider, чтобы внешний
    код мог переключать режим (см. agent_adapter fallback).
    """

    def __init__(self, provider: BaseProvider, tools: list[dict], tool_choice: str = "auto"):
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

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        return await self._provider.ainvoke(
            messages, tools=self._tools, tool_choice=self._tool_choice, **kwargs,
        )

    def astream(self, messages: list[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        return self._provider.astream(
            messages, tools=self._tools, tool_choice=self._tool_choice, **kwargs,
        )

    def bind_tools(self, tools, tool_choice: str = "auto") -> _BoundProvider:
        return _BoundProvider(self._provider, list(tools), tool_choice)


class BaseProvider:
    """Базовый класс для HTTP LLM-провайдеров (OpenAI-совместимый формат)."""

    # HTTP-коды для повтора запроса на транспортном уровне (httpx). Это НЕ то же,
    # что agent/messages.py:is_api_proxy_error — там текстовый детект уже
    # полученного proxy-ответа. Здесь набор шире (включает 429/504).
    # 504 (Gateway Timeout) и 429 (rate limit) тоже стоит ретраить.
    _RETRYABLE_STATUS_CODES: tuple[int, ...] = (429, 500, 502, 503, 504, 520, 524, 529)
    # HTTP-коды, при которых имеет смысл переключить ключ/креденшл (исчерпан
    # лимит или баланс текущего ключа), а не просто ретраить. 429 — обычный
    # rate limit; 402 — исчерпан лимит плана/баланса ("Top up balance").
    _CREDENTIAL_ROTATE_STATUS_CODES: tuple[int, ...] = (402, 429)
    _BASE_RETRY_DELAY: float = 2.0
    _MAX_RETRY_DELAY: float = 8.0

    def __init__(
        self,
        model: str = "",
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: int = 120,
        max_retries: int = 3,
        streaming: bool = True,
        reasoning_effort: str | None = None,
        thinking: bool | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.max_retries = max_retries
        self.streaming = streaming
        self.reasoning_effort = reasoning_effort
        self.thinking = thinking

        # Переопределяются наследниками / фабриками
        self._api_url: str = ""
        self._provider_name: str = "Provider"
        self._proxy: str = ""
        self._api_credentials: list[dict[str, Any]] = []
        self._credential_index: int = 0
        self._prompt_cache_mode: str = "auto"

    # ── Утилиты ──

    @staticmethod
    def _calc_backoff(attempt: int, base: float = 2.0, maximum: float = 8.0) -> float:
        return min(base * (2 ** attempt), maximum)

    def _calc_timeout(self, params: dict[str, Any], base: int = 20) -> int:
        messages = params.get("messages", [])
        try:
            total_chars = len(json.dumps(messages, default=str))
        except (TypeError, ValueError):
            total_chars = sum(len(str(m)) for m in messages)
        return min(max(base + total_chars // 1000, self.timeout), 300)

    # ── Tool binding (заменяет LangChain bind_tools) ──

    def bind_tools(self, tools: list[dict], tool_choice: str = "auto") -> _BoundProvider:
        return _BoundProvider(self, list(tools), tool_choice)

    # ── Override в наследниках ──

    def _get_api_key(self) -> str:
        if not self._api_credentials:
            return ""
        return self._api_credentials[self._credential_index % len(self._api_credentials)].get("key", "")

    def _get_proxy(self) -> str:
        if self._api_credentials:
            proxy = self._api_credentials[self._credential_index % len(self._api_credentials)].get("proxy", "")
            if proxy:
                return proxy
        return self._proxy

    def _credential_count(self) -> int:
        return max(1, len(self._api_credentials))

    def _main_credential_index(self) -> int:
        for index, credential in enumerate(self._api_credentials):
            if credential.get("main"):
                return index
        return 0

    def _reset_api_credential_index(self) -> None:
        if self._api_credentials:
            self._credential_index = self._main_credential_index()

    def _rotate_api_credential(self, reason: str) -> None:
        if len(self._api_credentials) <= 1:
            return
        old_index = self._credential_index % len(self._api_credentials)
        self._credential_index = (old_index + 1) % len(self._api_credentials)
        logger.warning(
            f"{self._provider_name} rotating API key | reason={reason} | "
            f"key={self._credential_index + 1}/{len(self._api_credentials)}"
        )

    def _all_credentials_failed_error(self, status_code: int, last_error: Exception | None) -> ValueError:
        return ValueError(
            f"{self._provider_name} API Error {status_code}: all "
            f"{self._credential_count()} configured API key(s) failed: {last_error}"
        )

    def _get_url(self) -> str:
        return self._api_url

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._get_api_key()}",
            "Content-Type": "application/json",
        }

    def _build_params(self, **kwargs: Any) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self.model,
        }
        temperature = kwargs.get("temperature", self.temperature)
        if temperature is not None:
            params["temperature"] = temperature
        if (max_tokens := kwargs.get("max_tokens", self.max_tokens)) is not None:
            params["max_tokens"] = max_tokens

        effort = kwargs.get("reasoning_effort", self.reasoning_effort)
        if effort:
            params["reasoning_effort"] = effort
        thinking = kwargs.get("thinking", self.thinking)
        if thinking is not None:
            params["chat_template_kwargs"] = {"enable_thinking": thinking}
        return params

    def _supports_anthropic_cache_control(self) -> bool:
        """True for OpenAI-compatible gateways that accept Anthropic cache_control.

        Native OpenAI-compatible models either cache automatically or reject unknown
        content-block fields. Anthropic-on-OpenRouter style routes accept
        `cache_control` on message content blocks, so opt in only when the provider
        config says so or the model/provider clearly points at Claude.
        """
        mode = (self._prompt_cache_mode or "auto").lower()
        if mode in {"off", "false", "none", "disabled"}:
            return False
        if mode in {"anthropic", "anthropic_cache_control", "cache_control"}:
            return True
        if mode in {"on", "true"}:
            return "claude" in (self.model or "").lower() or "anthropic/" in (self.model or "").lower()
        model = (self.model or "").lower()
        return "claude" in model or "anthropic/" in model

    @staticmethod
    def _as_text_blocks(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for part in content:
                if isinstance(part, str):
                    if part:
                        blocks.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    if part.get("type") == "text":
                        blocks.append({"type": "text", "text": part.get("text", "")})
                    else:
                        blocks.append(dict(part))
            return blocks
        return [{"type": "text", "text": "" if content is None else str(content)}]

    def _apply_openai_compatible_cache_control(self, params: dict[str, Any]) -> None:
        """Adds Anthropic-style cache breakpoints for compatible gateways.

        Placement mirrors AnthropicProvider: stable system/history prefix first,
        volatile latest user/tool turn last. The OpenAI API itself has automatic
        prompt caching and should not receive these extra fields, so this is gated
        by _supports_anthropic_cache_control().
        """
        if not self._supports_anthropic_cache_control():
            return
        msgs = params.get("messages")
        if not isinstance(msgs, list) or not msgs:
            return

        marked = 0
        for msg in msgs:
            if not isinstance(msg, dict) or msg.get("role") != "system":
                continue
            blocks = self._as_text_blocks(msg.get("content"))
            if blocks:
                blocks[-1]["cache_control"] = {"type": "ephemeral"}
                msg["content"] = blocks
                marked += 1
                break

        if len(msgs) >= 2 and marked < 4:
            target = msgs[-2]
            if isinstance(target, dict):
                blocks = self._as_text_blocks(target.get("content"))
                for blk in reversed(blocks):
                    if isinstance(blk, dict):
                        blk["cache_control"] = {"type": "ephemeral"}
                        target["content"] = blocks
                        break

    # ── Главный API ──

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        self._reset_api_credential_index()
        params = self._build_params(**kwargs)
        params["messages"] = self._convert_messages(messages)
        if tools := kwargs.get("tools"):
            params["tools"] = tools
            params["tool_choice"] = kwargs.get("tool_choice", "auto")
        self._apply_openai_compatible_cache_control(params)
        return await self._http_post(params)

    async def astream(
        self, messages: list[BaseMessage], **kwargs,
    ) -> AsyncIterator[AIMessageChunk]:
        self._reset_api_credential_index()
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
        self._apply_openai_compatible_cache_control(params)

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

        attempt = 0
        rate_limit_rotations = 0
        while attempt < self.max_retries:
            yielded_any = False
            try:
                async for chunk in self._astream_attempt(params):
                    yielded_any = True
                    yield chunk
                return
            except _RetryableStreamError as e:
                last_error = e
                if e.status_code in self._CREDENTIAL_ROTATE_STATUS_CODES and not yielded_any:
                    if rate_limit_rotations < self._credential_count() - 1:
                        rate_limit_rotations += 1
                        self._rotate_api_credential(f"HTTP {e.status_code}")
                        continue
                    raise self._all_credentials_failed_error(e.status_code, last_error) from e
                await _retry_or_reraise(e, f"HTTP {e.status_code}", partial_aborts=True)
                attempt += 1
                continue
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                last_error = TimeoutError(f"Stream timeout: {e}")
                await _retry_or_reraise(last_error, "timeout", partial_aborts=False)
                attempt += 1
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ProtocolError) as e:
                # Сервер оборвал SSE-стрим (peer closed / incomplete chunked read).
                last_error = e
                await _retry_or_reraise(e, f"dropped ({type(e).__name__}: {e})", partial_aborts=True)
                attempt += 1

        raise ValueError(
            f"{self._provider_name} stream error after {self.max_retries} attempts: {last_error}"
        )

    async def _astream_attempt(self, params: dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        proxy = self._get_proxy() or None
        dynamic_timeout = self._calc_timeout(params)
        full_reasoning = ""
        line_buffer = ""

        client_kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(dynamic_timeout, connect=30.0),
            "limits": httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0),
        }
        if proxy:
            client_kwargs["proxy"] = proxy

        async with httpx.AsyncClient(**client_kwargs) as client, client.stream(
            "POST",
            self._get_url(),
            json=params,
            headers=self._get_headers(),
        ) as resp:
            if (
                resp.status_code in self._RETRYABLE_STATUS_CODES
                or resp.status_code in self._CREDENTIAL_ROTATE_STATUS_CODES
            ):
                error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                raise _RetryableStreamError(
                    resp.status_code,
                    _format_api_error(
                        self._provider_name,
                        resp.status_code,
                        error_text,
                        resp.headers.get("content-type") or "",
                    ),
                )
            if resp.status_code != 200:
                error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                if _is_transient_401(resp.status_code, error_text):
                    raise _RetryableStreamError(
                        resp.status_code,
                        f"{self._provider_name} transient 401 (upstream): {error_text}",
                    )
                raise ValueError(
                    _format_api_error(
                        self._provider_name,
                        resp.status_code,
                        error_text,
                        resp.headers.get("content-type") or "",
                    )
                )

            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/html" in content_type:
                error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                raise ValueError(
                    _format_api_error(
                        self._provider_name,
                        resp.status_code,
                        error_text,
                        content_type,
                    )
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

                    if "error" in chunk and isinstance(chunk["error"], dict | str):
                        err = chunk["error"]
                        if isinstance(err, dict):
                            msg = err.get("message", str(err))
                            code = err.get("code")
                        else:
                            msg = str(err)
                            code = None
                        try:
                            code = int(code) if code is not None else None
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
    def _convert_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        input_details = usage.get("prompt_tokens_details") or {}
        cached_tokens = 0
        if isinstance(input_details, dict):
            cached_tokens = int(input_details.get("cached_tokens") or 0)
        result = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_token_details": input_details,
            "output_token_details": usage.get("completion_tokens_details") or {},
        }
        if cached_tokens:
            result["cache_read_input_tokens"] = cached_tokens
        return result

    # ── Convert messages → OpenAI-compatible dicts ──

    def _convert_messages(self, messages: list[BaseMessage]) -> list[dict[str, Any]]:
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
                item: dict[str, Any] = {"role": role, "content": content}
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

    def _parse_streaming_tool_calls(self, raw: Any) -> list[dict[str, Any]]:
        if not raw:
            return []
        result: list[dict[str, Any]] = []
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

    def _parse_tool_calls(self, raw_tool_calls: Any) -> list[dict]:
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

    def _parse_response(self, data: dict[str, Any]) -> AIMessage:
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

    async def _http_post(self, params: dict[str, Any]) -> AIMessage:
        name = self._provider_name
        url = self._get_url()
        dynamic_timeout = self._calc_timeout(params)
        last_error: Exception | None = None
        attempt = 0
        rate_limit_rotations = 0

        while attempt < self.max_retries:
            headers = self._get_headers()
            proxy = self._get_proxy() or None
            client_kwargs: dict[str, Any] = {
                "timeout": httpx.Timeout(dynamic_timeout, connect=30.0),
                "limits": httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0),
            }
            if proxy:
                client_kwargs["proxy"] = proxy

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=params, headers=headers)
                if resp.status_code == 200:
                    break
                if resp.status_code in self._CREDENTIAL_ROTATE_STATUS_CODES:
                    error_text = resp.text
                    last_error = ValueError(
                        _format_api_error(
                            name,
                            resp.status_code,
                            error_text,
                            resp.headers.get("content-type") or "",
                        )
                    )
                    if rate_limit_rotations < self._credential_count() - 1:
                        rate_limit_rotations += 1
                        self._rotate_api_credential(f"HTTP {resp.status_code}")
                        continue
                    raise self._all_credentials_failed_error(resp.status_code, last_error)
                if resp.status_code in self._RETRYABLE_STATUS_CODES:
                    error_text = resp.text
                    last_error = ValueError(
                        _format_api_error(
                            name,
                            resp.status_code,
                            error_text,
                            resp.headers.get("content-type") or "",
                        )
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
                raise ValueError(
                    _format_api_error(
                        name,
                        resp.status_code,
                        error_text,
                        resp.headers.get("content-type") or "",
                    )
                )
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

        content_type = (resp.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            raise ValueError(
                _format_api_error(
                    name,
                    resp.status_code,
                    resp.text,
                    content_type,
                )
            )

        # Парсинг вне retry-блока: его исключения (JSONDecodeError/KeyError)
        # не должны ретраиться — это баги, а не сетевые ошибки.
        return self._parse_response(resp.json())


__all__ = ["BaseProvider"]
