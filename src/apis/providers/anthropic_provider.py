"""Anthropic провайдер на httpx (без langchain-anthropic).

Формат: POST https://api.anthropic.com/v1/messages
  - system передаётся отдельным полем
  - messages: [{"role": "user|assistant", "content": [...]}]
  - tool_use / tool_result через специальные content blocks
  - стриминг через SSE с разными типами event
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Dict, List, Optional

import httpx
try:
    import aiohttp as _aiohttp
    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

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


_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(BaseProvider):
    """HTTP-провайдер для Anthropic messages API."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._definition_id: str = ""
        self._base_url: str = "https://api.anthropic.com"
        self._extra_headers: Dict[str, str] = {}
        self._append_query: str = ""
        self._session_id_header: str = ""
        self._billing_header: str = ""  # вставляется первым блоком в system
        self._inject_metadata: dict = {}  # мёрджится в params["metadata"]
        self._use_aiohttp: bool = False  # обход httpx TLS fingerprint блокировки
        # Некоторые прокси (aerolink и пр.) ВЫБРАСЫВАЮТ клиентский `system` и
        # подставляют собственный (каноничный промт Claude Code). До модели
        # доходят только `messages`/`tools`. Для таких провайдеров системник
        # надо доставлять первым user-сообщением, а не полем `system`.
        self._system_as_first_message: bool = False

    def _get_api_key(self) -> str:
        return get_api_key(self._definition_id)

    def _get_url(self) -> str:
        url = f"{self._base_url.rstrip('/')}/v1/messages"
        if self._append_query:
            url = f"{url}?{self._append_query}"
        return url

    def _get_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "x-api-key": self._get_api_key(),
            "anthropic-version": _ANTHROPIC_VERSION,
            "anthropic-beta": "prompt-caching-2024-07-31",
            "Content-Type": "application/json",
        }
        if self._extra_headers:
            headers.update(self._extra_headers)
        if self._session_id_header:
            headers[self._session_id_header] = str(uuid.uuid4())
        return headers

    # ── Conversion ──

    def _convert_messages_anthropic(
        self, messages: List[BaseMessage],
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Возвращает (system_prompt, messages_list) в формате Anthropic."""
        system_parts: list[str] = []
        out: list[dict] = []

        for msg in messages:
            if isinstance(msg, SystemMessage):
                if isinstance(msg.content, str):
                    system_parts.append(msg.content)
                continue

            if isinstance(msg, HumanMessage):
                out.append({"role": "user", "content": self._to_content_blocks(msg.content)})
                continue

            if isinstance(msg, AIMessage):
                blocks: list[dict] = []
                content = msg.content
                if isinstance(content, str) and content:
                    blocks.append({"type": "text", "text": content})
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, str) and part:
                            blocks.append({"type": "text", "text": part})
                        elif isinstance(part, dict) and part.get("type") == "text":
                            blocks.append({"type": "text", "text": part.get("text", "")})
                for tc in msg.tool_calls or []:
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": tc.get("name") or "",
                        "input": tc.get("args") or {},
                    })
                if not blocks:
                    blocks.append({"type": "text", "text": ""})
                out.append({"role": "assistant", "content": blocks})
                continue

            if isinstance(msg, ToolMessage):
                content_str = msg.content if isinstance(msg.content, str) else json.dumps(msg.content)
                out.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.tool_call_id,
                        "content": content_str,
                    }],
                })
                continue

            # fallback — как user text
            out.append({"role": "user", "content": [{"type": "text", "text": str(msg.content)}]})

        system_prompt = "\n\n".join(p for p in system_parts if p) or None
        return system_prompt, out

    @staticmethod
    def _to_content_blocks(content: Any) -> list[dict]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if isinstance(content, list):
            blocks: list[dict] = []
            for part in content:
                if isinstance(part, str) and part:
                    blocks.append({"type": "text", "text": part})
                elif isinstance(part, dict):
                    ptype = part.get("type")
                    if ptype == "text":
                        blocks.append({"type": "text", "text": part.get("text", "")})
                    elif ptype == "image_url":
                        url = (part.get("image_url") or {}).get("url", "")
                        if url.startswith("data:"):
                            try:
                                header, b64 = url.split(",", 1)
                                media = header.split(";")[0].replace("data:", "") or "image/png"
                                blocks.append({
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": media,
                                        "data": b64,
                                    },
                                })
                            except ValueError:
                                continue
                        else:
                            blocks.append({
                                "type": "image",
                                "source": {"type": "url", "url": url},
                            })
            return blocks or [{"type": "text", "text": ""}]
        return [{"type": "text", "text": str(content)}]

    @staticmethod
    def _convert_tools_to_anthropic(tools: List[dict]) -> List[dict]:
        """OpenAI tool schema -> Anthropic tool schema."""
        out = []
        for t in tools or []:
            fn = t.get("function") or t
            name = fn.get("name") or ""
            if not name:
                continue
            out.append({
                "name": name,
                "description": fn.get("description", "") or "",
                "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
            })
        return out

    @staticmethod
    def _map_tool_choice(choice: Any) -> Optional[dict]:
        """OpenAI-стиль tool_choice -> Anthropic. None == не слать tool_choice."""
        if choice == "auto":
            return {"type": "auto"}
        if choice in ("any", "required"):
            return {"type": "any"}
        if isinstance(choice, str) and choice not in ("auto", "any", "none", "required"):
            return {"type": "tool", "name": choice}
        return None

    def _inject_extra_metadata(self, params: Dict[str, Any]) -> None:
        """Мёрджит _inject_metadata в params['metadata'] (для провайдеров типа aerolink).

        Если в inject_metadata есть ключ 'user_id' содержащий JSON со строкой
        'session_id' == "" — подставляет новый UUID (сессия должна быть непустой).
        """
        if not self._inject_metadata:
            return
        extra = dict(self._inject_metadata)
        # Динамически заполняем session_id если пустой
        user_id_str = extra.get("user_id", "")
        if user_id_str:
            try:
                uid = json.loads(user_id_str)
                if isinstance(uid, dict) and not uid.get("session_id"):
                    uid["session_id"] = str(uuid.uuid4())
                    extra["user_id"] = json.dumps(uid)
            except (json.JSONDecodeError, TypeError):
                pass
        existing = params.get("metadata") or {}
        params["metadata"] = {**extra, **existing}

    def _inject_billing_header(self, params: Dict[str, Any]) -> None:
        """Вставляет billing header первым блоком в system (для провайдеров типа aerolink)."""
        if not self._billing_header:
            return
        billing_block = {"type": "text", "text": self._billing_header}
        system = params.get("system")
        if system is None:
            params["system"] = [billing_block]
        elif isinstance(system, str):
            params["system"] = [billing_block, {"type": "text", "text": system}]
        elif isinstance(system, list):
            params["system"] = [billing_block] + system

    # Префикс для системника, доставляемого первым user-сообщением (для
    # провайдеров с _system_as_first_message). Оборачиваем в тег, чтобы модель
    # отличала операционные инструкции от обычного запроса пользователя.
    _SYSTEM_AS_MSG_PREFIX = (
        "[Session configuration — provided by the necli runtime, not by the end "
        "user. The following describes the tools, output conventions and working "
        "context for this session. Please operate according to it. There is no "
        "need to repeat or quote this block back to the user.]\n\n"
    )

    def _apply_system_as_message(
        self, system_prompt: Optional[str], msgs: List[Dict[str, Any]],
    ) -> tuple[Optional[str], List[Dict[str, Any]]]:
        """Перекладывает системник из поля `system` в первое user-сообщение.

        Возвращает (new_system, new_msgs). new_system всегда None — поле `system`
        отдаётся под billing-блок (см. _inject_billing_header). Чтобы не нарушить
        чередование ролей Anthropic, текст вклеивается ОТДЕЛЬНЫМ text-блоком в
        начало первого user-сообщения (а не новым сообщением)."""
        if not system_prompt:
            return system_prompt, msgs
        block = {"type": "text", "text": self._SYSTEM_AS_MSG_PREFIX + system_prompt}
        msgs = list(msgs)
        if msgs and msgs[0].get("role") == "user":
            content = msgs[0].get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}] if content else []
            elif isinstance(content, list):
                content = list(content)
            else:
                content = []
            msgs[0] = {**msgs[0], "content": [block] + content}
        else:
            # Первого user-сообщения нет (редко) — вставляем новое спереди.
            msgs.insert(0, {"role": "user", "content": [block]})
        return None, msgs

    def _build_params_anthropic(self, **kwargs: Any) -> Dict[str, Any]:
        max_tokens = kwargs.get("max_tokens", self.max_tokens) or 4096
        params: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        return params

    @staticmethod
    def _apply_cache_control(params: Dict[str, Any]) -> None:
        """Расставляет cache_control breakpoints для prompt caching.

        Anthropic кэширует префикс запроса до каждой точки. Стабильная часть —
        это tools + system + ранняя история, которые не меняются между
        итерациями агентного цикла. Ставим до 4 (лимит API) ephemeral-точек:
        последний tool, конец system, и последний content-блок предпоследнего
        сообщения (граница «зафиксированная история | новый ход»)."""
        mark = {"type": "ephemeral"}

        tools = params.get("tools")
        if isinstance(tools, list) and tools:
            tools[-1]["cache_control"] = mark

        system = params.get("system")
        if isinstance(system, str) and system:
            params["system"] = [{"type": "text", "text": system, "cache_control": mark}]
        elif isinstance(system, list) and system:
            last = system[-1]
            if isinstance(last, dict):
                last["cache_control"] = mark

        msgs = params.get("messages")
        if isinstance(msgs, list) and len(msgs) >= 2:
            target = msgs[-2]
            if isinstance(target, dict):
                blocks = target.get("content")
                if isinstance(blocks, str) and blocks:
                    # Строковый content нормализуем в список блоков, иначе
                    # breakpoint на границе истории терялся бы (хуже cache-hit).
                    blocks = [{"type": "text", "text": blocks}]
                    target["content"] = blocks
                if isinstance(blocks, list) and blocks:
                    # Берём ПОСЛЕДНИЙ dict-блок (сканируем с конца): последний
                    # элемент не всегда dict (например tool_result-структуры),
                    # а раньше в таком случае breakpoint молча не ставился.
                    for blk in reversed(blocks):
                        if isinstance(blk, dict):
                            blk["cache_control"] = mark
                            break

    @staticmethod
    def _convert_usage_anthropic(usage: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(usage, dict):
            return {}
        # Anthropic input_tokens НЕ включает cache_read/cache_creation —
        # это отдельные счётчики. Чтобы посчитать полную стоимость без
        # кэш-скидок, складываем всё в input_tokens.
        inp = int(usage.get("input_tokens") or 0)
        inp += int(usage.get("cache_read_input_tokens") or 0)
        inp += int(usage.get("cache_creation_input_tokens") or 0)
        outp = int(usage.get("output_tokens") or 0)
        return {
            "input_tokens": inp,
            "output_tokens": outp,
            "total_tokens": inp + outp,
        }

    # ── Public API (override) ──

    async def ainvoke(self, messages: List[BaseMessage], **kwargs) -> AIMessage:
        params = self._build_params_anthropic(**kwargs)
        system_prompt, msgs = self._convert_messages_anthropic(messages)
        if self._system_as_first_message:
            system_prompt, msgs = self._apply_system_as_message(system_prompt, msgs)
        if system_prompt:
            params["system"] = system_prompt
        params["messages"] = msgs

        if tools := kwargs.get("tools"):
            choice = kwargs.get("tool_choice", "auto")
            if choice != "none":
                params["tools"] = self._convert_tools_to_anthropic(tools)
                tc = self._map_tool_choice(choice)
                if tc is not None:
                    params["tool_choice"] = tc

        self._inject_extra_metadata(params)
        self._inject_billing_header(params)
        self._apply_cache_control(params)
        data = await self._http_post_raw(params)
        return self._parse_anthropic_response(data)

    async def astream(self, messages: List[BaseMessage], **kwargs) -> AsyncIterator[AIMessageChunk]:
        params = self._build_params_anthropic(**kwargs)
        system_prompt, msgs = self._convert_messages_anthropic(messages)
        if self._system_as_first_message:
            system_prompt, msgs = self._apply_system_as_message(system_prompt, msgs)
        if system_prompt:
            params["system"] = system_prompt
        params["messages"] = msgs
        params["stream"] = True
        if tools := kwargs.get("tools"):
            choice = kwargs.get("tool_choice", "auto")
            if choice != "none":
                params["tools"] = self._convert_tools_to_anthropic(tools)
                tc = self._map_tool_choice(choice)
                if tc is not None:
                    params["tool_choice"] = tc

        self._inject_extra_metadata(params)
        self._inject_billing_header(params)
        self._apply_cache_control(params)
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            yielded_any = False
            try:
                async for chunk in self._astream_anthropic(params):
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

    async def _astream_anthropic(self, params: Dict[str, Any]) -> AsyncIterator[AIMessageChunk]:
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout(params)
        client_kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(dynamic_timeout, connect=30.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        # state: index → {"name": str, "id": str, "args": str}
        current_blocks: dict[int, dict] = {}
        usage_acc: dict = {}

        if self._use_aiohttp and _AIOHTTP_AVAILABLE:
            async for chunk in self._aiohttp_sse_parse(params, current_blocks, usage_acc):
                yield chunk
            return

        client_kwargs.setdefault("limits", httpx.Limits(max_connections=5, max_keepalive_connections=2, keepalive_expiry=5.0))
        async with httpx.AsyncClient(**client_kwargs) as client:
            async with client.stream(
                "POST", self._get_url(), json=params, headers=self._get_headers(),
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

                        etype = event.get("type")
                        if etype == "error":
                            # Anthropic шлёт `event: error` отдельным SSE-событием
                            # (overloaded_error, api_error и т.п.). Раньше оно
                            # молча игнорировалось → стрим «зависал» пустым.
                            err = event.get("error") or {}
                            err_type = err.get("type") or "error"
                            err_msg = err.get("message") or str(event)
                            raise ValueError(
                                f"{self._provider_name} stream error [{err_type}]: {err_msg}"
                            )
                        if etype == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            if usage:
                                usage_acc.update(usage)
                            continue

                        if etype == "content_block_start":
                            idx = event.get("index", 0)
                            block = event.get("content_block") or {}
                            btype = block.get("type")
                            if btype == "tool_use":
                                current_blocks[idx] = {
                                    "name": block.get("name") or "",
                                    "id": block.get("id") or "",
                                    "args": "",
                                }
                                yield AIMessageChunk(
                                    content="",
                                    tool_call_chunks=[{
                                        "index": idx,
                                        "id": current_blocks[idx]["id"],
                                        "name": current_blocks[idx]["name"],
                                        "args": "",
                                    }],
                                )
                            elif btype not in ("text",):
                                # thinking/redacted_thinking и прочие будущие типы
                                # блоков обрабатываются в content_block_delta, но
                                # незнакомый тип здесь означает, что мы можем
                                # потерять его дельты — сигналим в лог.
                                logger.warning(
                                    f"{self._provider_name} unknown content_block type "
                                    f"{btype!r} at index {idx} — block content may be dropped"
                                )
                            continue

                        if etype == "content_block_delta":
                            idx = event.get("index", 0)
                            delta = event.get("delta") or {}
                            dtype = delta.get("type")
                            if dtype == "text_delta":
                                text = delta.get("text", "") or ""
                                if text:
                                    yield AIMessageChunk(content=text)
                            elif dtype == "input_json_delta":
                                args_piece = delta.get("partial_json", "") or ""
                                if idx in current_blocks:
                                    current_blocks[idx]["args"] += args_piece
                                if args_piece:
                                    yield AIMessageChunk(
                                        content="",
                                        tool_call_chunks=[{
                                            "index": idx,
                                            "id": current_blocks.get(idx, {}).get("id"),
                                            "name": current_blocks.get(idx, {}).get("name"),
                                            "args": args_piece,
                                        }],
                                    )
                            elif dtype == "thinking_delta":
                                # Some Anthropic extended thinking — кладём в reasoning_content
                                thinking = delta.get("thinking", "") or ""
                                if thinking:
                                    yield AIMessageChunk(
                                        content="",
                                        additional_kwargs={"reasoning_content": thinking},
                                    )
                            continue

                        if etype == "message_delta":
                            usage = event.get("usage") or {}
                            if usage:
                                # output_tokens обновляется в message_delta
                                for k, v in usage.items():
                                    usage_acc[k] = v
                            continue

                        if etype == "message_stop":
                            if usage_acc:
                                yield AIMessageChunk(
                                    content="",
                                    usage_metadata=self._convert_usage_anthropic(usage_acc),
                                )
                            return

    async def _aiohttp_sse_parse(
        self, params: Dict[str, Any],
        current_blocks: dict, usage_acc: dict,
    ) -> AsyncIterator[AIMessageChunk]:
        """SSE стрим через aiohttp — парсит события идентично _astream_anthropic."""
        import aiohttp
        url = self._get_url()
        headers = self._get_headers()
        timeout = aiohttp.ClientTimeout(total=self._calc_timeout(params), connect=30)
        proxy = self._proxy or None
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=params, headers=headers, proxy=proxy) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"{self._provider_name} HTTP {resp.status}: {text[:400]}")
                line_buffer = ""
                async for raw_bytes in resp.content:
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
                        etype = event.get("type")
                        if etype == "content_block_delta":
                            idx = event.get("index", 0)
                            delta = event.get("delta") or {}
                            dtype = delta.get("type")
                            if dtype == "text_delta":
                                text_val = delta.get("text") or ""
                                if text_val:
                                    yield AIMessageChunk(content=text_val)
                            elif dtype == "input_json_delta":
                                if idx in current_blocks:
                                    args_chunk = delta.get("partial_json") or ""
                                    current_blocks[idx]["args"] += args_chunk
                                    yield AIMessageChunk(
                                        content="",
                                        tool_call_chunks=[{"index": idx, "id": None, "name": None, "args": args_chunk}],
                                    )
                        elif etype == "message_start":
                            usage = (event.get("message") or {}).get("usage") or {}
                            if usage:
                                usage_acc.update(usage)
                        elif etype == "content_block_start":
                            idx = event.get("index", 0)
                            block = event.get("content_block") or {}
                            if block.get("type") == "tool_use":
                                current_blocks[idx] = {"name": block.get("name") or "", "id": block.get("id") or "", "args": ""}
                                yield AIMessageChunk(content="", tool_call_chunks=[{"index": idx, "id": current_blocks[idx]["id"], "name": current_blocks[idx]["name"], "args": ""}])
                        elif etype == "message_delta":
                            usage = event.get("usage") or {}
                            for k, v in usage.items():
                                usage_acc[k] = v
                        elif etype == "message_stop":
                            if usage_acc:
                                yield AIMessageChunk(content="", usage_metadata=self._convert_usage_anthropic(usage_acc))
                            return

    async def _http_post_raw(self, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._use_aiohttp and _AIOHTTP_AVAILABLE:
            return await self._aiohttp_post_raw(params)
        name = self._provider_name
        url = self._get_url()
        headers = self._get_headers()
        proxy = self._proxy or None
        dynamic_timeout = self._calc_timeout(params)
        last_error: Exception | None = None
        attempt = 0

        client_kwargs: Dict[str, Any] = {"timeout": httpx.Timeout(dynamic_timeout, connect=30.0)}
        if proxy:
            client_kwargs["proxy"] = proxy

        while attempt < self.max_retries:
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    resp = await client.post(url, json=params, headers=headers)
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except (json.JSONDecodeError, ValueError) as je:
                        # Битое/неполное тело при HTTP 200 — это НЕ наш
                        # «бизнес» ValueError ниже (который мы намеренно
                        # пробрасываем без ретрая). Лечим как транзиентный сбой.
                        last_error = ValueError(
                            f"{name} API Error: malformed JSON body: {je}"
                        )
                        delay = self._calc_backoff(attempt)
                        logger.warning(
                            f"{name} malformed JSON body | "
                            f"attempt={attempt + 1}/{self.max_retries} | retry in {delay:.1f}s"
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
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

    async def _aiohttp_post_raw(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Альтернативный POST через aiohttp (обход httpx TLS fingerprint блокировки)."""
        import aiohttp
        url = self._get_url()
        headers = self._get_headers()
        timeout = aiohttp.ClientTimeout(total=self._calc_timeout(params), connect=30)
        proxy = self._proxy or None
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=params, headers=headers, proxy=proxy) as resp:
                        text = await resp.text()
                        if resp.status == 200:
                            return json.loads(text)
                        if resp.status in self._RETRYABLE_STATUS_CODES:
                            last_error = ValueError(f"{self._provider_name} HTTP {resp.status}: {text[:200]}")
                            await asyncio.sleep(self._calc_backoff(attempt))
                            continue
                        raise ValueError(f"{self._provider_name} HTTP {resp.status}: {text[:400]}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                last_error = e
                await asyncio.sleep(self._calc_backoff(attempt))
        raise ValueError(f"{self._provider_name} API Error after {self.max_retries} attempts: {last_error}")

    async def _aiohttp_stream(self, params: Dict[str, Any]) -> AsyncIterator[str]:
        """SSE стрим через aiohttp."""
        import aiohttp
        url = self._get_url()
        headers = self._get_headers()
        timeout = aiohttp.ClientTimeout(total=self._calc_timeout(params), connect=30)
        proxy = self._proxy or None
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=params, headers=headers, proxy=proxy) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise ValueError(f"{self._provider_name} HTTP {resp.status}: {text[:400]}")
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
                    yield line

    def _parse_anthropic_response(self, data: Dict[str, Any]) -> AIMessage:
        content_blocks = data.get("content") or []
        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text") or "")
            elif btype == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or "",
                    "name": block.get("name") or "",
                    "args": block.get("input") or {},
                    "type": "tool_call",
                })

        usage_metadata = self._convert_usage_anthropic(data.get("usage") or {})
        return AIMessage(
            content="".join(text_parts),
            tool_calls=tool_calls,
            usage_metadata=usage_metadata,
            response_metadata={
                "model_name": data.get("model", self.model),
                "finish_reason": data.get("stop_reason", "stop") or "stop",
            },
        )


def create_anthropic_provider(
    definition: ApiProviderDefinition,
    model_id: str,
    **kwargs: Any,
) -> AnthropicProvider:
    api_key = get_api_key(definition.id)
    if not api_key and definition.requires_auth:
        raise ValueError(
            f"API key not set for provider '{definition.id}'. "
            "Use /api → provider → Set key."
        )

    model_info = definition.get_model_info(model_id)
    actual_model = model_info.id if model_info else model_id

    provider = AnthropicProvider(
        model=actual_model,
        temperature=kwargs.get("temperature", 0.7),
        max_tokens=kwargs.get("max_tokens", 4096),
        timeout=definition.timeout or 300,
        max_retries=definition.max_retries or 3,
    )
    provider._provider_name = definition.name
    provider._definition_id = definition.id
    provider._proxy = definition.proxy
    provider._base_url = (definition.base_url or "https://api.anthropic.com").rstrip("/")
    provider._extra_headers = dict(definition.default_headers or {})
    extra = definition.extra or {}
    provider._append_query = extra.get("append_query", "")
    provider._session_id_header = extra.get("session_id_header", "")
    provider._billing_header = extra.get("billing_header", "")
    provider._inject_metadata = extra.get("inject_metadata", {})
    provider._use_aiohttp = bool(extra.get("use_aiohttp", False))
    provider._system_as_first_message = bool(extra.get("system_as_first_message", False))

    logger.debug(f"Created Anthropic provider: {definition.name} / {actual_model}")
    return provider


__all__ = ["AnthropicProvider", "create_anthropic_provider"]