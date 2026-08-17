"""ChatGPT subscription provider using the Codex Responses endpoint."""

from __future__ import annotations

import codecs
import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from apis.base import BaseProvider, _format_api_error
from apis.chatgpt_auth import CHATGPT_RESPONSES_URL, get_chatgpt_access
from apis.messages import AIMessage, AIMessageChunk
from apis.models import ApiProviderDefinition


class ChatGPTProvider(BaseProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._session_id = str(uuid.uuid4())

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
        for message in messages:
            role = str(message.get("role") or "user")
            if role == "tool":
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": str(message.get("tool_call_id") or ""),
                        "output": str(message.get("content") or ""),
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
                result.append(
                    {
                        "type": "function_call",
                        "call_id": str(call.get("id") or ""),
                        "name": str(function.get("name") or ""),
                        "arguments": str(function.get("arguments") or "{}"),
                    }
                )
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

    async def _astream_attempt(self, params: dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        payload = self._responses_payload(params, stream=True)
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        argument_items: set[str] = set()
        async with httpx.AsyncClient(**self._client_kwargs(params)) as client:
            response = await self._open_stream(client, payload)
            try:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    raise ValueError(
                        _format_api_error(
                            self._provider_name,
                            response.status_code,
                            body,
                            response.headers.get("content-type") or "",
                        )
                    )
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
                        event_type = str(event.get("type") or "")
                        if event_type == "response.output_text.delta":
                            yield AIMessageChunk(content=str(event.get("delta") or ""))
                        elif event_type in {
                            "response.reasoning_summary_text.delta",
                            "response.reasoning_text.delta",
                        }:
                            delta = str(event.get("delta") or "")
                            yield AIMessageChunk(
                                content="", additional_kwargs={"reasoning_content": delta}
                            )
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
                            if (
                                item.get("type") == "function_call"
                                and item_id not in argument_items
                            ):
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
                        elif event_type in {"response.failed", "error"}:
                            error = event.get("error") or (event.get("response") or {}).get("error")
                            raise ValueError(f"{self._provider_name} API Error: {error}")
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
    return provider


__all__ = ["ChatGPTProvider", "chatgpt_models", "create_chatgpt_provider"]
