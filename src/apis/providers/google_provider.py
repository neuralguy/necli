"""Google Gemini провайдер на httpx (без langchain-google-genai).

Формат: POST https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse&key=...
  - system_instruction отдельным полем
  - contents: [{"role": "user|model", "parts": [...]}]
  - tools: [{"function_declarations": [...]}]
  - functionCall / functionResponse через parts
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx

from apis.base import BaseProvider, _RetryableStreamError
from apis.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from apis.models import ApiProviderDefinition
from apis.config import get_api_key
from logger import logger

class GoogleGeminiProvider(BaseProvider):
    """HTTP-провайдер для Google Gemini generateContent API."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._definition_id: str = ""
        self._base_url: str = "https://generativelanguage.googleapis.com"

    def _get_api_key(self) -> str:
        return get_api_key(self._definition_id)

    def _endpoint(self, stream: bool) -> str:
        method = "streamGenerateContent" if stream else "generateContent"
        base = self._base_url.rstrip("/")
        url = f"{base}/v1beta/models/{self.model}:{method}"
        if stream:
            url += "?alt=sse"
        return url

    def _get_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self._get_api_key(),
        }

    # ── Conversion ──

    @staticmethod
    def _content_to_parts(content: Any) -> list[dict]:
        if isinstance(content, str):
            return [{"text": content}] if content else []
        if isinstance(content, list):
            parts: list[dict] = []
            for p in content:
                if isinstance(p, str) and p:
                    parts.append({"text": p})
                elif isinstance(p, dict):
                    if p.get("type") == "text":
                        parts.append({"text": p.get("text", "")})
                    elif p.get("type") == "image_url":
                        url = (p.get("image_url") or {}).get("url", "")
                        if url.startswith("data:"):
                            try:
                                header, b64 = url.split(",", 1)
                                media = header.split(";")[0].replace("data:", "") or "image/png"
                                parts.append({
                                    "inline_data": {"mime_type": media, "data": b64},
                                })
                            except ValueError:
                                continue
            return parts
        return [{"text": str(content)}] if content else []

    def _convert_messages_gemini(
        self, messages: List[BaseMessage],
    ) -> tuple[Optional[dict], List[Dict[str, Any]]]:
        """Возвращает (system_instruction, contents)."""
        system_parts: list[str] = []
        contents: list[dict] = []

        for msg in messages:
            if isinstance(msg, SystemMessage):
                if isinstance(msg.content, str):
                    system_parts.append(msg.content)
                continue

            if isinstance(msg, HumanMessage):
                parts = self._content_to_parts(msg.content)
                if parts:
                    contents.append({"role": "user", "parts": parts})
                continue

            if isinstance(msg, AIMessage):
                parts: list[dict] = []
                content = msg.content
                if isinstance(content, str) and content:
                    parts.append({"text": content})
                elif isinstance(content, list):
                    parts.extend(self._content_to_parts(content))
                for tc in msg.tool_calls or []:
                    parts.append({
                        "functionCall": {
                            "name": tc.get("name") or "",
                            "args": tc.get("args") or {},
                        },
                    })
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue

            if isinstance(msg, ToolMessage):
                try:
                    response_obj: Any = json.loads(msg.content) if isinstance(msg.content, str) else msg.content
                except json.JSONDecodeError:
                    response_obj = {"result": msg.content}
                if not isinstance(response_obj, dict):
                    response_obj = {"result": response_obj}
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": msg.name or "tool",
                            "response": response_obj,
                        },
                    }],
                })
                continue

            # fallback
            contents.append({"role": "user", "parts": self._content_to_parts(msg.content)})

        system_instruction = None
        if system_parts:
            system_instruction = {"parts": [{"text": "\n\n".join(system_parts)}]}
        return system_instruction, contents

    @staticmethod
    def _convert_tools_gemini(tools: List[dict]) -> List[dict]:
        declarations = []
        for t in tools or []:
            fn = t.get("function") or t
            name = fn.get("name")
            if not name:
                continue
            decl = {
                "name": name,
                "description": fn.get("description", "") or "",
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
            declarations.append(decl)
        if not declarations:
            return []
        return [{"function_declarations": declarations}]

    def _build_generation_config(self, **kwargs: Any) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {
            "temperature": kwargs.get("temperature", self.temperature),
        }
        mt = kwargs.get("max_tokens", self.max_tokens)
        if mt:
            cfg["maxOutputTokens"] = int(mt)
        return cfg

    @staticmethod
    def _convert_usage_gemini(meta: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(meta, dict):
            return {}
        # promptTokenCount у Gemini уже включает cachedContentTokenCount —
        # отдельно прибавлять не нужно.
        inp = int(meta.get("promptTokenCount") or 0)
        outp = int(meta.get("candidatesTokenCount") or 0)
        total = int(meta.get("totalTokenCount") or (inp + outp))
        return {
            "input_tokens": inp,
            "output_tokens": outp,
            "total_tokens": total,
        }

    # ── Public ──

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        sys_inst, contents = self._convert_messages_gemini(messages)
        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": self._build_generation_config(**kwargs),
        }
        if sys_inst:
            body["systemInstruction"] = sys_inst
        if tools := kwargs.get("tools"):
            t = self._convert_tools_gemini(tools)
            if t:
                body["tools"] = t

        data = await self._http_post_raw(body, stream=False)
        return self._parse_gemini_response(data)

    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        sys_inst, contents = self._convert_messages_gemini(messages)
        body: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": self._build_generation_config(**kwargs),
        }
        if sys_inst:
            body["systemInstruction"] = sys_inst
        if tools := kwargs.get("tools"):
            t = self._convert_tools_gemini(tools)
            if t:
                body["tools"] = t

        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            yielded_any = False
            try:
                async for chunk in self._astream_gemini(body):
                    yielded_any = True
                    yield chunk
                return
            except _RetryableStreamError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{self._provider_name} stream HTTP {e.status_code} | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                last_error = TimeoutError(f"Stream timeout: {e}")
                if attempt < self.max_retries - 1:
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{self._provider_name} stream timeout | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ProtocolError) as e:
                # Сервер оборвал SSE-стрим. Если уже наpyield'или часть —
                # повтор приведёт к дублированию, поэтому пробрасываем выше.
                if yielded_any:
                    logger.warning(
                        f"{self._provider_name} stream dropped mid-response, partial yielded: "
                        f"{type(e).__name__}: {e}"
                    )
                    raise
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{self._provider_name} stream dropped pre-yield | "
                        f"{type(e).__name__}: {e} | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)

        raise ValueError(
            f"{self._provider_name} stream error after {self.max_retries} attempts: {last_error}"
        )

    async def _astream_gemini(self, body: Dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout({"messages": body.get("contents", [])})
        client_kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(dynamic_timeout, connect=30.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        url = self._endpoint(stream=True)
        tc_index = 0  # счётчик function calls в стриме

        client_kwargs.setdefault("limits", httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0))
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream(
                "POST", url, json=body, headers=self._get_headers(),
            ) as resp:
                if resp.status_code in self._RETRYABLE_STATUS_CODES:
                    error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                    raise _RetryableStreamError(
                        resp.status_code,
                        f"{self._provider_name} API Error {resp.status_code}: {error_text}",
                    )
                if resp.status_code != 200:
                    error_text = (await resp.aread()).decode("utf-8", errors="ignore")
                    raise ValueError(
                        f"{self._provider_name} API Error {resp.status_code}: {error_text}"
                    )

                line_buffer = ""
                async for raw_bytes in resp.aiter_bytes():
                    line_buffer += raw_bytes.decode("utf-8", errors="ignore")
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:"):].strip()
                        if not data_str:
                            continue
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        candidates = event.get("candidates") or []
                        if candidates:
                            cand = candidates[0]
                            content = cand.get("content") or {}
                            for part in content.get("parts") or []:
                                if not isinstance(part, dict):
                                    continue
                                if "text" in part:
                                    text = part.get("text") or ""
                                    if text:
                                        yield AIMessageChunk(content=text)
                                elif "functionCall" in part:
                                    fc = part.get("functionCall") or {}
                                    name = fc.get("name") or ""
                                    args_obj = fc.get("args") or {}
                                    yield AIMessageChunk(
                                        content="",
                                        tool_call_chunks=[{
                                            "index": tc_index,
                                            "id": f"call_gemini_{tc_index}",
                                            "name": name,
                                            "args": json.dumps(args_obj) if args_obj else "{}",
                                        }],
                                    )
                                    tc_index += 1

                        usage = event.get("usageMetadata")
                        if usage:
                            yield AIMessageChunk(
                                content="",
                                usage_metadata=self._convert_usage_gemini(usage),
                            )

    async def _http_post_raw(self, body: Dict[str, Any], stream: bool = False) -> Dict[str, Any]:
        name = self._provider_name
        url = self._endpoint(stream=stream)
        headers = self._get_headers()
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout({"messages": body.get("contents", [])})
        last_error: Exception | None = None
        attempt = 0

        client_kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(dynamic_timeout, connect=30.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        while attempt < self.max_retries:
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=body, headers=headers)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except (json.JSONDecodeError, ValueError) as e:
                        last_error = e
                        delay = self._calc_backoff(attempt)
                        logger.warning(
                            f"{name} malformed JSON | "
                            f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s | {e}"
                        )
                        attempt += 1
                        if attempt < self.max_retries:
                            await asyncio.sleep(delay)
                            continue
                        break
                if resp.status_code in self._RETRYABLE_STATUS_CODES:
                    last_error = ValueError(
                        f"{name} API Error {resp.status_code}: {resp.text}"
                    )
                    delay = self._calc_backoff(attempt)
                    logger.warning(
                        f"{name} HTTP {resp.status_code} | "
                        f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    attempt += 1
                    continue
                raise ValueError(f"{name} API Error {resp.status_code}: {resp.text}")
            except (asyncio.TimeoutError, httpx.TimeoutException) as e:
                delay = self._calc_backoff(attempt)
                logger.warning(
                    f"{name} timeout | attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s | {e}"
                )
                last_error = TimeoutError(f"Request timeout: {e}")
                attempt += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)
            except ValueError:
                raise
            except httpx.TransportError as e:
                delay = self._calc_backoff(attempt)
                logger.warning(
                    f"{name} transport error | attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s | {e}"
                )
                last_error = e
                attempt += 1
                if attempt < self.max_retries:
                    await asyncio.sleep(delay)

        raise ValueError(
            f"{name} API Error after {self.max_retries} attempts: {last_error}"
        )

    def _parse_gemini_response(self, data: Dict[str, Any]) -> AIMessage:
        candidates = data.get("candidates") or []
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        finish_reason = "stop"

        if candidates:
            cand = candidates[0]
            finish_reason = cand.get("finishReason", "stop") or "stop"
            content = cand.get("content") or {}
            for i, part in enumerate(content.get("parts") or []):
                if not isinstance(part, dict):
                    continue
                if "text" in part:
                    text_parts.append(part.get("text") or "")
                elif "functionCall" in part:
                    fc = part.get("functionCall") or {}
                    tool_calls.append({
                        "id": f"call_gemini_{i}",
                        "name": fc.get("name") or "",
                        "args": fc.get("args") or {},
                        "type": "tool_call",
                    })

        usage_metadata = self._convert_usage_gemini(data.get("usageMetadata") or {})
        return AIMessage(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            response_metadata={
                "model_name": data.get("modelVersion", self.model),
                "finish_reason": finish_reason,
            },
        )

def create_google_provider(
    definition: ApiProviderDefinition,
    model_id: str,
    **kwargs: Any,
) -> GoogleGeminiProvider:
    api_key = get_api_key(definition.id)
    if not api_key and definition.requires_auth:
        raise ValueError(
            f"API key not set for provider '{definition.id}'. "
            "Use /api → provider → Set key."
        )

    model_info = definition.get_model_info(model_id)
    actual_model = model_info.id if model_info else model_id

    provider = GoogleGeminiProvider(
        model=actual_model,
        temperature=kwargs.get("temperature", 0.7),
        max_tokens=kwargs.get("max_tokens"),
        timeout=definition.timeout or 300,
        max_retries=definition.max_retries or 3,
    )
    provider._provider_name = definition.name
    provider._definition_id = definition.id
    provider._proxy = definition.proxy
    provider._base_url = (definition.base_url or "https://generativelanguage.googleapis.com").rstrip("/")

    logger.debug(f"Created Google provider: {definition.name} / {actual_model}")
    return provider

__all__ = ["GoogleGeminiProvider", "create_google_provider"]