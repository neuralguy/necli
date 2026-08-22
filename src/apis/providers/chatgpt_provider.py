"""ChatGPT subscription provider using the Codex Responses endpoint."""

from __future__ import annotations

import asyncio
import codecs
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import websockets
from websockets.asyncio.client import ClientConnection
from websockets.exceptions import InvalidStatus

from apis.base import BaseProvider, _format_api_error, _RetryableStreamError
from apis.chatgpt_auth import CHATGPT_RESPONSES_URL, get_chatgpt_access
from apis.messages import AIMessage, AIMessageChunk
from apis.models import ApiProviderDefinition
from logger import logger


class _ResponsesWebSocket:
    def __init__(self, connection: ClientConnection, timeout: float) -> None:
        self._connection = connection
        self._timeout = timeout
        self._lock = asyncio.Lock()

    async def stream(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        async with self._lock:
            await asyncio.wait_for(
                self._connection.send(json.dumps(payload)), timeout=self._timeout
            )
            while True:
                raw = await asyncio.wait_for(self._connection.recv(), timeout=self._timeout)
                if not isinstance(raw, str):
                    raise ValueError("ChatGPT websocket returned a binary event")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                yield event
                if event.get("type") in {
                    "response.completed",
                    "response.incomplete",
                    "response.failed",
                    "error",
                }:
                    return

    async def close(self) -> None:
        await self._connection.close()


class ChatGPTProvider(BaseProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = str(uuid.uuid4())
        self._websocket_enabled = False
        self._websocket: _ResponsesWebSocket | None = None
        self._websocket_disabled = False
        self._websocket_previous_response_id: str | None = None
        self._websocket_previous_input: list[dict[str, Any]] | None = None
        self._websocket_previous_output: list[dict[str, Any]] | None = None
        self._websocket_previous_properties: str | None = None

    @staticmethod
    def _response_tools(tools: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            function = tool.get("function")
            if tool.get("type") == "function" and isinstance(function, dict):
                item = {"type": "function", **function}
                item.setdefault("strict", False)
                result.append(item)
            else:
                result.append(dict(tool))
        return result

    @staticmethod
    def _content_parts(content: Any, *, output: bool = False) -> list[dict[str, Any]]:
        text_type = "output_text" if output else "input_text"
        if not isinstance(content, list):
            return [{"type": text_type, "text": "" if content is None else str(content)}]
        parts: list[dict[str, Any]] = []
        for part in content:
            if isinstance(part, str):
                if part:
                    parts.append({"type": text_type, "text": part})
                continue
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url" and not output:
                image = part.get("image_url")
                if isinstance(image, dict):
                    url = image.get("url")
                    detail = image.get("detail")
                else:
                    url = image
                    detail = part.get("detail")
                if url:
                    item: dict[str, Any] = {"type": "input_image", "image_url": url}
                    if detail:
                        item["detail"] = detail
                    parts.append(item)
                continue
            if "text" in part:
                parts.append({"type": text_type, "text": str(part.get("text") or "")})
        return parts

    def _response_input(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        open_call_ids: set[str] = set()
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "tool":
                call_id = str(message.get("tool_call_id") or "")
                output = str(message.get("content") or "")
                if call_id and call_id in open_call_ids:
                    result.append(
                        {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": output,
                        }
                    )
                    open_call_ids.remove(call_id)
                else:
                    logger.warning(
                        "ChatGPT OAuth: preserving orphan tool output as user text (call_id={})",
                        call_id or "<empty>",
                    )
                    result.append(
                        {
                            "type": "message",
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": (
                                        f"[Tool result for {call_id or 'unknown call'}]\n{output}"
                                    ),
                                }
                            ],
                        }
                    )
                continue
            response_role = "developer" if role == "system" else role
            calls = message.get("tool_calls") or []
            parts = self._content_parts(message.get("content"), output=response_role == "assistant")
            has_content = any(
                part.get("text") or part.get("image_url")
                for part in parts
                if isinstance(part, dict)
            )
            if has_content or not calls:
                result.append({"type": "message", "role": response_role, "content": parts})
            for call in calls:
                function = call.get("function") or {}
                call_id = str(call.get("id") or "")
                result.append(
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
                if call_id:
                    open_call_ids.add(call_id)
        return result

    def _responses_payload(self, params: dict[str, Any], *, stream: bool) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": self._response_input(params.get("messages") or []),
            "store": False,
            "stream": stream,
            "include": ["reasoning.encrypted_content"],
        }
        tools = self._response_tools(params.get("tools"))
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = params.get("tool_choice", "auto")
            payload["parallel_tool_calls"] = True
        effort = params.get("reasoning_effort")
        if effort:
            payload["reasoning"] = {"effort": effort, "summary": "auto"}
        if params.get("max_tokens") is not None:
            payload["max_output_tokens"] = params["max_tokens"]
        return payload

    async def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        token, account_id = await get_chatgpt_access(force_refresh=force_refresh)
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Originator": "necli",
            "User-Agent": "necli/1.0",
            "Session-Id": self._session_id,
        }
        if account_id:
            headers["ChatGPT-Account-Id"] = account_id
        return headers

    def _client_kwargs(self, params: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "timeout": httpx.Timeout(self._calc_timeout(params), connect=30.0),
            "limits": httpx.Limits(
                max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0
            ),
        }
        if self._proxy:
            kwargs["proxy"] = self._proxy
        return kwargs

    @staticmethod
    def _usage(usage: Any) -> dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        details = usage.get("input_tokens_details") or {}
        result = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
            "input_token_details": details,
            "output_token_details": usage.get("output_tokens_details") or {},
        }
        if isinstance(details, dict) and details.get("cached_tokens"):
            result["cache_read_input_tokens"] = details["cached_tokens"]
        return result

    async def _open_stream(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> httpx.Response:
        for refresh in (False, True):
            request = client.build_request(
                "POST",
                CHATGPT_RESPONSES_URL,
                json=payload,
                headers=await self._headers(force_refresh=refresh),
            )
            response = await client.send(request, stream=True)
            if response.status_code != 401 or refresh:
                return response
            await response.aclose()
        raise AssertionError("unreachable")

    @staticmethod
    def _websocket_url() -> str:
        if CHATGPT_RESPONSES_URL.startswith("https://"):
            return "wss://" + CHATGPT_RESPONSES_URL.removeprefix("https://")
        if CHATGPT_RESPONSES_URL.startswith("http://"):
            return "ws://" + CHATGPT_RESPONSES_URL.removeprefix("http://")
        raise ValueError("ChatGPT Responses URL must use HTTP or HTTPS")

    async def _connect_websocket(self, session_key: str = "default") -> _ResponsesWebSocket:
        del session_key
        timeout = float(self.timeout or 300)
        for refresh in (False, True):
            headers = await self._headers(force_refresh=refresh)
            headers.pop("Accept", None)
            headers.pop("Content-Type", None)
            user_agent = headers.pop("User-Agent", None)
            try:
                connection = await websockets.connect(
                    self._websocket_url(),
                    additional_headers=headers,
                    user_agent_header=user_agent,
                    proxy=self._proxy or True,
                    open_timeout=min(timeout, 30.0),
                    ping_interval=20.0,
                    ping_timeout=20.0,
                    close_timeout=5.0,
                    max_size=None,
                )
                return _ResponsesWebSocket(connection, timeout)
            except InvalidStatus as exc:
                if exc.response.status_code != 401 or refresh:
                    raise
        raise AssertionError("unreachable")

    @staticmethod
    def _websocket_properties(payload: dict[str, Any]) -> str:
        properties = {
            key: value
            for key, value in payload.items()
            if key not in {"input", "previous_response_id", "type"}
        }
        return json.dumps(properties, sort_keys=True, separators=(",", ":"))

    def _websocket_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = {**payload, "type": "response.create"}
        current_input = payload.get("input") or []
        properties = self._websocket_properties(request)
        previous_input = self._websocket_previous_input
        can_reuse = (
            self._websocket_previous_response_id is not None
            and previous_input is not None
            and self._websocket_previous_output is not None
            and properties == self._websocket_previous_properties
            and current_input[: len(previous_input)] == previous_input
        )
        if can_reuse:
            incremental = list(current_input[len(previous_input) :])
            previous_output = self._websocket_previous_output or []
            if incremental[: len(previous_output)] == previous_output:
                next_input = incremental[len(previous_output) :]
                # ChatGPT's Codex websocket occasionally loses the function-call
                # association behind previous_response_id. Sending only the output
                # then fails with "No tool call found ...". Tool continuations are
                # small and correctness-sensitive, so send their complete history.
                if not any(
                    item.get("type") == "function_call_output"
                    for item in next_input
                    if isinstance(item, dict)
                ):
                    request["input"] = next_input
                    request["previous_response_id"] = self._websocket_previous_response_id
        return request

    @staticmethod
    def _canonical_output_item(item: Any) -> dict[str, Any] | None:
        if not isinstance(item, dict):
            return None
        item_type = item.get("type")
        if item_type == "message" and item.get("role") == "assistant":
            content = [
                {"type": "output_text", "text": str(part.get("text") or "")}
                for part in item.get("content") or []
                if isinstance(part, dict) and part.get("type") == "output_text"
            ]
            return {"type": "message", "role": "assistant", "content": content}
        if item_type == "function_call":
            return {
                "type": "function_call",
                "call_id": str(item.get("call_id") or ""),
                "name": str(item.get("name") or ""),
                "arguments": str(item.get("arguments") or "{}"),
            }
        return None

    async def _close_websocket(self) -> None:
        websocket, self._websocket = self._websocket, None
        self._websocket_previous_response_id = None
        self._websocket_previous_input = None
        self._websocket_previous_output = None
        self._websocket_previous_properties = None
        if websocket is not None:
            await websocket.close()

    async def _websocket_events(self, payload: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        if self._websocket is None:
            self._websocket = await self._connect_websocket()
        request = self._websocket_payload(payload)
        terminal = False
        output_text: list[str] = []
        output_items: list[dict[str, Any]] = []
        try:
            async for event in self._websocket.stream(request):
                event_type = event.get("type")
                if event_type == "response.output_text.delta":
                    output_text.append(str(event.get("delta") or ""))
                elif event_type == "response.output_item.done":
                    item = self._canonical_output_item(event.get("item"))
                    if item is not None:
                        output_items.append(item)
                if event_type == "response.completed":
                    response = event.get("response") or {}
                    response_id = response.get("id")
                    if isinstance(response_id, str) and response_id:
                        completed_items = [
                            item
                            for raw_item in response.get("output") or []
                            if (item := self._canonical_output_item(raw_item)) is not None
                        ]
                        if completed_items:
                            output_items = completed_items
                        elif output_text and not any(
                            item.get("type") == "message" for item in output_items
                        ):
                            output_items.insert(
                                0,
                                {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "".join(output_text),
                                        }
                                    ],
                                },
                            )
                        self._websocket_previous_response_id = response_id
                        self._websocket_previous_input = list(
                            payload.get("input") or []
                        )
                        self._websocket_previous_output = output_items
                        self._websocket_previous_properties = (
                            self._websocket_properties(request)
                        )
                    terminal = True
                elif event_type == "response.incomplete":
                    terminal = True
                yield event
        finally:
            if not terminal:
                await self._close_websocket()
        if not terminal:
            raise ConnectionError("ChatGPT websocket closed before a terminal event")

    async def _stream_response_events(
        self, events: AsyncIterator[dict[str, Any]]
    ) -> AsyncIterator[AIMessageChunk]:
        argument_items: set[str] = set()
        async for event in events:
            event_type = str(event.get("type") or "")
            if event_type == "response.output_text.delta":
                yield AIMessageChunk(content=str(event.get("delta") or ""))
            elif event_type in {
                "response.reasoning_summary_text.delta",
                "response.reasoning_text.delta",
            }:
                delta = str(event.get("delta") or "")
                yield AIMessageChunk(content="", additional_kwargs={"reasoning_content": delta})
            elif event_type == "response.output_item.added":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    yield AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "index": int(event.get("output_index") or 0),
                                "id": item.get("call_id") or item.get("id"),
                                "name": item.get("name"),
                                "args": "",
                            }
                        ],
                    )
            elif event_type == "response.function_call_arguments.delta":
                item_id = str(event.get("item_id") or event.get("call_id") or "")
                argument_items.add(item_id)
                yield AIMessageChunk(
                    content="",
                    tool_call_chunks=[
                        {
                            "index": int(event.get("output_index") or 0),
                            "id": event.get("call_id"),
                            "name": None,
                            "args": str(event.get("delta") or ""),
                        }
                    ],
                )
            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                item_id = str(item.get("id") or item.get("call_id") or "")
                if item.get("type") == "function_call" and item_id not in argument_items:
                    yield AIMessageChunk(
                        content="",
                        tool_call_chunks=[
                            {
                                "index": int(event.get("output_index") or 0),
                                "id": item.get("call_id") or item.get("id"),
                                "name": item.get("name"),
                                "args": str(item.get("arguments") or "{}"),
                            }
                        ],
                    )
            elif event_type == "response.completed":
                completed = event.get("response") or {}
                from apis.chatgpt_usage import schedule_chatgpt_usage_refresh

                schedule_chatgpt_usage_refresh(proxy=self._proxy)
                yield AIMessageChunk(
                    content="",
                    usage_metadata=self._usage(completed.get("usage")),
                    response_metadata={
                        "model_name": completed.get("model", self.model),
                        "finish_reason": "stop",
                        "stream_complete": True,
                    },
                )
                return
            elif event_type == "response.incomplete":
                incomplete = event.get("response") or {}
                details = incomplete.get("incomplete_details") or {}
                reason = details.get("reason") or "max_output_tokens"
                from apis.chatgpt_usage import schedule_chatgpt_usage_refresh

                schedule_chatgpt_usage_refresh(proxy=self._proxy)
                yield AIMessageChunk(
                    content="",
                    usage_metadata=self._usage(incomplete.get("usage")),
                    response_metadata={
                        "model_name": incomplete.get("model", self.model),
                        "finish_reason": reason,
                        "stream_complete": True,
                    },
                )
                return
            elif event_type in {"response.failed", "error"}:
                error = event.get("error") or (event.get("response") or {}).get("error")
                if isinstance(error, dict):
                    raw_status = error.get("status_code") or error.get("code")
                    try:
                        status = int(raw_status)
                    except (TypeError, ValueError):
                        status = None
                    error_type = str(error.get("type") or "").lower()
                    if status in self._RETRYABLE_STATUS_CODES or error_type in {
                        "api_error",
                        "overloaded_error",
                        "rate_limit_error",
                        "server_error",
                    }:
                        raise _RetryableStreamError(
                            status or (429 if "rate_limit" in error_type else 500),
                            f"{self._provider_name} API Error: {error}",
                        )
                raise ValueError(f"{self._provider_name} API Error: {error}")

        yield AIMessageChunk(
            content="",
            response_metadata={
                "finish_reason": None,
                "stream_complete": False,
                "stream_incomplete": True,
            },
        )

    async def _astream_attempt(self, params: dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        if self._websocket_enabled and not self._websocket_disabled:
            logger.debug("ChatGPT OAuth transport: WebSocket")
            payload = self._responses_payload(params, stream=True)
            yielded_any = False
            try:
                async for chunk in self._stream_response_events(self._websocket_events(payload)):
                    yielded_any = True
                    yield chunk
                return
            except Exception as exc:
                self._websocket_disabled = True
                await self._close_websocket()
                if yielded_any:
                    logger.warning(
                        "ChatGPT websocket failed after partial response: {}", exc
                    )
                    raise
                logger.warning("ChatGPT websocket unavailable, using SSE: {}", exc)
        logger.debug("ChatGPT OAuth transport: SSE")
        async for chunk in self._astream_sse_attempt(params):
            yield chunk

    async def _astream_sse_attempt(self, params: dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        payload = self._responses_payload(params, stream=True)
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        async with httpx.AsyncClient(**self._client_kwargs(params)) as client:
            response = await self._open_stream(client, payload)
            try:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    if response.status_code in self._RETRYABLE_STATUS_CODES:
                        raise _RetryableStreamError(
                            response.status_code,
                            _format_api_error(
                                self._provider_name,
                                response.status_code,
                                body,
                                response.headers.get("content-type") or "",
                            ),
                        )
                    raise ValueError(
                        _format_api_error(
                            self._provider_name,
                            response.status_code,
                            body,
                            response.headers.get("content-type") or "",
                        )
                    )

                async def events() -> AsyncIterator[dict[str, Any]]:
                    nonlocal buffer
                    async for raw in response.aiter_bytes():
                        buffer += decoder.decode(raw)
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data or data == "[DONE]":
                                continue
                            try:
                                event = json.loads(data)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(event, dict):
                                yield event

                async for chunk in self._stream_response_events(events()):
                    yield chunk
            finally:
                await response.aclose()

    def _parse_responses_result(self, data: dict[str, Any]) -> AIMessage:
        content: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("type") == "output_text":
                        content.append(str(part.get("text") or ""))
            elif item.get("type") == "function_call":
                try:
                    arguments = json.loads(str(item.get("arguments") or "{}"))
                except json.JSONDecodeError:
                    arguments = {}
                tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id") or "",
                        "name": item.get("name") or "",
                        "args": arguments,
                        "type": "tool_call",
                    }
                )
        return AIMessage(
            content="".join(content),
            tool_calls=tool_calls,
            usage_metadata=self._usage(data.get("usage")),
            response_metadata={
                "model_name": data.get("model", self.model),
                "finish_reason": "stop",
            },
        )

    async def _http_post(self, params: dict[str, Any]) -> AIMessage:
        payload = self._responses_payload(params, stream=False)
        async with httpx.AsyncClient(**self._client_kwargs(params)) as client:
            response: httpx.Response | None = None
            for refresh in (False, True):
                response = await client.post(
                    CHATGPT_RESPONSES_URL,
                    json=payload,
                    headers=await self._headers(force_refresh=refresh),
                )
                if response.status_code != 401 or refresh:
                    break
            assert response is not None
            if response.status_code != 200:
                raise ValueError(
                    _format_api_error(
                        self._provider_name,
                        response.status_code,
                        response.text,
                        response.headers.get("content-type") or "",
                    )
                )
            result = self._parse_responses_result(response.json())
            from apis.chatgpt_usage import schedule_chatgpt_usage_refresh

            schedule_chatgpt_usage_refresh(proxy=self._proxy)
            return result


def chatgpt_models() -> list[dict[str, Any]]:
    return [
        {
            "id": model_id,
            "display_name": display_name,
            "context_window": 1_050_000,
            "input_price": input_price,
            "output_price": output_price,
        }
        for model_id, display_name, input_price, output_price in (
            ("gpt-5.6-sol", "GPT-5.6 Sol", 5.0, 30.0),
            ("gpt-5.6-terra", "GPT-5.6 Terra", 2.0, 12.0),
            ("gpt-5.6-luna", "GPT-5.6 Luna", 0.2, 1.2),
        )
    ]


def create_chatgpt_provider(
    definition: ApiProviderDefinition, model_id: str, **kwargs: Any
) -> ChatGPTProvider:
    model_info = definition.get_model_info(model_id)
    provider = ChatGPTProvider(
        model=model_info.id if model_info else model_id,
        temperature=None,
        max_tokens=kwargs.get("max_tokens"),
        timeout=definition.timeout or 300,
        max_retries=definition.max_retries or 3,
        reasoning_effort=kwargs.get("reasoning_effort"),
        thinking=kwargs.get("thinking"),
    )
    provider._provider_name = definition.name
    provider._provider_id = definition.id
    provider._proxy = definition.proxy
    provider._websocket_enabled = definition.extra.get("websocket") is True
    return provider


__all__ = ["ChatGPTProvider", "chatgpt_models", "create_chatgpt_provider"]
