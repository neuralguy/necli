"""Адаптер для интеграции API-провайдеров с агентным циклом necli.

Прямые httpx-вызовы API провайдеров без браузерных интеграций,
сохраняя совместимость с существующим agent loop.

В API-режиме используются НАТИВНЫЕ tool calls через function calling:
  - инструменты передаются через параметр tools при bind_tools
  - модель возвращает AIMessage с tool_calls
  - результаты возвращаются как ToolMessage в истории

Для совместимости с UI/парсером агентного цикла tool_calls во время
стриминга конвертируются в текстовые :::call ... call::: блоки.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from apis.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage
from apis.base import BaseProvider

from apis.registry import get_provider
from apis._retry import with_throttle_retry, stream_with_throttle_retry
from logger import logger
from tools._html_unescape import maybe_unescape as _unescape_html_entities
from apis.opus48_debug import has_transcript_hint, log_event


def _provider_kwargs() -> dict:
    """User-configurable generation params from config.json.

    Keys are optional: omitted means provider's own default.
    """
    import config as _cfg
    kw: dict = {}
    temp = _cfg.get("temperature", 0.7)
    if isinstance(temp, (int, float)):
        kw["temperature"] = float(temp)
    mt = _cfg.get("max_tokens", 0)
    if isinstance(mt, (int, float)) and int(mt) > 0:
        kw["max_tokens"] = int(mt)
    effort = _cfg.get("reasoning_effort", "")
    if isinstance(effort, str) and effort:
        kw["reasoning_effort"] = effort
    return kw


_IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _cleanup_docx_shot(path: Path) -> None:
    try:
        from config.paths import BASE_DIR
        shots_dir = (BASE_DIR / "docx_shots").resolve()
        resolved = path.resolve()
        if shots_dir == resolved.parent:
            resolved.unlink(missing_ok=True)
            logger.info("API docx shot cleaned: %s", resolved.name)
    except Exception:
        logger.debug("API docx shot cleanup failed: %s", path, exc_info=True)


def _build_multimodal_content(text: str, image_paths: list) -> list[dict]:
    """Строит multimodal content для HumanMessage: текст + base64 изображения."""
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    for p in image_paths:
        path = Path(p)
        try:
            if not path.exists():
                logger.warning(f"API image skip (not found): {path}")
                continue
            mime = _IMAGE_MIME.get(path.suffix.lower(), "image/png")
            data = path.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"},
            })
            logger.info(f"API image attached: {path.name} ({len(data)} bytes, {mime})")
            _cleanup_docx_shot(path)
        except Exception as e:
            logger.error(f"API image read failed for {p}: {type(e).__name__}: {e}")
    return parts


# _CALL_FORMAT_INSTRUCTIONS удалён: формат вызова инструментов в text-режиме
# теперь часть системного промта (system_prompt.build_system_prompt →
# TOOL_FORMAT_TEXT_BLOCK по native_tools). Единый источник правды.


def _debug_message_summary(messages: list, limit: int = 6) -> list[dict]:
    out: list[dict] = []
    for msg in messages[-limit:]:
        role = getattr(msg, "role", type(msg).__name__)
        content = getattr(msg, "content", "")
        text = _content_to_text(content) if not isinstance(content, str) else content
        out.append({
            "role": role,
            "len": len(text),
            "transcript": has_transcript_hint(text),
            "preview": text[:300].replace("\n", "\\n"),
        })
    return out


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return _unescape_html_entities(content)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return _unescape_html_entities("".join(parts))
    if content is None:
        return ""
    return _unescape_html_entities(str(content))


class ApiSession:
    """Состояние API-сессии с нативной историей сообщений.

    Хранит AIMessage(tool_calls=...) и ToolMessage между итерациями
    для корректной работы function calling. Текстовая сериализация
    в necli Session делается отдельно через persistence хелперы.
    """

    def __init__(self, provider_id: str, model_id: str):
        self.provider_id = provider_id
        self.model_id = model_id
        self.messages: list = []
        self._llm: Optional[BaseProvider] = None
        self._llm_kwargs: dict = {}

    @property
    def use_native_tools(self) -> bool:
        # ЕДИНЫЙ глобальный переключатель native/fenced для ВСЕХ провайдеров.
        # True → native function calling; False (default) → fenced :::call.
        # Управляется командой /tool_format.
        try:
            from config.settings import get as _settings_get
            return bool(_settings_get("tool_format_force_native", False))
        except Exception:
            logger.debug("tool_format_force_native lookup failed", exc_info=True)
            return False

    @property
    def llm(self) -> BaseProvider:
        kw = _provider_kwargs()
        if self._llm is None or self._llm_kwargs != kw:
            self._llm = get_provider(self.provider_id, self.model_id, **kw)
            self._llm_kwargs = kw
            logger.info(
                "API llm (re)built: provider=" + self.provider_id
                + " model=" + self.model_id
                + " params=" + str(kw)
            )
        return self._llm

    def reset(self) -> None:
        self.messages.clear()
        self._llm = None
        self._llm_kwargs = {}

    def add_system(self, content: str, compressed: bool = False) -> None:
        kw = {"compressed": True} if compressed else None
        self.messages.append(SystemMessage(content=content, additional_kwargs=kw))

    def add_user(self, content: str, synthetic: bool = False) -> None:
        kwargs: dict[str, Any] = {"content": content}
        if synthetic:
            kwargs["additional_kwargs"] = {"synthetic": True}
        self.messages.append(HumanMessage(**kwargs))

    def add_assistant(self, content: str, tool_calls: list | None = None, reasoning_content: str = "") -> None:
        kwargs: dict[str, Any] = {"content": content}
        if tool_calls:
            kwargs["tool_calls"] = tool_calls
        if reasoning_content:
            kwargs["additional_kwargs"] = {"reasoning_content": reasoning_content}
        self.messages.append(AIMessage(**kwargs))

    def add_tool_result(self, tool_call_id: str, content: str, name: str = "") -> None:
        self.messages.append(ToolMessage(
            content=content, tool_call_id=tool_call_id, name=name or "tool",
        ))


_api_session: Optional[ApiSession] = None


def get_api_session() -> Optional[ApiSession]:
    return _api_session


def current_active_skills() -> set:
    """Скиллы, активные СЕЙЧАС по истории текущей ApiSession (для гейтинга).

    Активность = скилл загружен в пределах окна последних раундов
    (skills.registry.ACTIVE_WINDOW_ROUNDS). Используется при сборке системного
    промпта и native-схем, чтобы гейтящиеся инструменты были видны только пока
    их скилл «живёт» в контексте. Пустое множество — ничего не активно.
    """
    sess = _api_session
    if sess is None:
        return set()
    try:
        from skills.registry import active_skills_from_messages

        return active_skills_from_messages(sess.messages)
    except Exception:
        logger.debug("current_active_skills failed", exc_info=True)
        return set()


def set_api_session(session: Optional[ApiSession]) -> None:
    global _api_session
    _api_session = session


def create_api_session(provider_id: str, model_id: str) -> ApiSession:
    session = ApiSession(provider_id, model_id)
    set_api_session(session)
    return session


def _tool_calls_to_text_blocks(tool_calls):
    """Конвертирует нативные tool_calls в :::call ... call::: блоки для UI/парсера.

    Асимметричные маркеры :::call / call::: не встречаются в реальном коде,
    поэтому body может содержать что угодно — тройные backticks, тильды, HTML, код.
    """
    parts = []
    for tc in tool_calls:
        name = tc.get("name") or "shell"
        args = tc.get("args") or {}
        # write_file / create_file — контент в теле, path в шапке
        if name in ("write_file", "create_file") and isinstance(args, dict) and "content" in args:
            path = args.get("path", "")
            content = args.get("content", "")
            encoding = args.get("encoding")
            header = f'{name} path="{path}"'
            if encoding:
                header += f' encoding="{encoding}"'
            parts.append("\n:::call " + header + "\n" + content + "\ncall:::\n")
            continue
        # shell — команда в JSON
        if name == "shell" and isinstance(args, dict):
            cmd = args.get("command", "")
            body = json.dumps({"command": cmd}, ensure_ascii=False) if cmd else json.dumps(args, ensure_ascii=False)
        else:
            body = json.dumps(args, ensure_ascii=False)
        parts.append("\n:::call " + name + "\n" + body + "\ncall:::\n")
    return "".join(parts)

def _ensure_tool_call_ids(tool_calls):
    out = []
    for tc in tool_calls:
        tc = dict(tc)
        if not tc.get("id"):
            tc["id"] = "call_" + uuid.uuid4().hex[:16]
        out.append(tc)
    return out


def _pending_native_tool_calls(messages: list) -> list[dict]:
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if not isinstance(msg, AIMessage):
            continue
        calls = list(getattr(msg, "tool_calls", []) or [])
        if not calls:
            return []
        used = {
            m.tool_call_id for m in messages[i + 1 :]
            if isinstance(m, ToolMessage) and getattr(m, "tool_call_id", "")
        }
        return [tc for tc in calls if tc.get("id") not in used]
    return []


def _structured_result_content(d: dict) -> str:
    """Содержимое одного ToolMessage из структурного результата.

    Формат '$ cmd\\n<output>' читаем для модели, но каждый результат живёт в
    СВОЁМ ToolMessage — без склейки '\\n---\\n', поэтому '---' внутри output
    (markdown/diff/файлы) безопасен.
    """
    from tools._html_unescape import maybe_unescape
    cmd = d.get("command") or d.get("name") or "tool"
    output = d.get("output") or ""
    exit_code = d.get("exit_code", 0)
    if output:
        output = maybe_unescape(output)
    cmd = maybe_unescape(cmd)
    header = f"$ {cmd}"
    if exit_code and exit_code != 0:
        header += f" [exit {exit_code}]"
    content = f"{header}\n{output}".rstrip()
    return content or "(no output)"


def build_native_tool_messages(
    pending_calls: list[dict],
    tool_results: list[dict],
) -> list[ToolMessage]:
    """По одному ToolMessage на каждый pending tool_call_id.

    Сопоставление по имени с FIFO-очередью (как в subagent_api
    _append_tool_results_native) — устойчиво к перестановке результатов
    (blocked/subagent добавляются в inline_results отдельным порядком).
    Лишние результаты, не подобранные по имени, дописываются к последнему
    ToolMessage; отсутствующие → '(no output)'.
    """
    results_by_name: dict[str, list[dict]] = {}
    for d in tool_results:
        results_by_name.setdefault(d.get("name") or "tool", []).append(d)

    # plan/think — контрол-инструменты: исполняются не через registry, а через
    # parse_plan_commands/parse_think_blocks (loop). Результата в tool_results
    # для них нет, но провайдер требует ToolMessage на КАЖДЫЙ native tool_call_id
    # (иначе парность ломается → 400). Отдаём осмысленный ack.
    _control_ack = {
        "plan": "(plan recorded)",
        "think": "(thought recorded)",
    }

    out: list[ToolMessage] = []
    for tc in pending_calls:
        name = tc.get("name") or "tool"
        tc_id = tc.get("id", "")
        bucket = results_by_name.get(name) or []
        d = bucket.pop(0) if bucket else None
        if d is not None:
            content = _structured_result_content(d)
        elif name in _control_ack:
            content = _control_ack[name]
        else:
            content = "(no output)"
        out.append(ToolMessage(content=content, tool_call_id=tc_id, name=name))

    # Несопоставленные по имени результаты (модель/провайдер вернули имя,
    # которого нет среди pending_calls) — не теряем: клеим к последнему
    # ToolMessage, чтобы они дошли до модели.
    leftovers = [d for bucket in results_by_name.values() for d in bucket]
    if leftovers and out:
        extra = "\n\n".join(_structured_result_content(d) for d in leftovers)
        out[-1].content = (out[-1].content + "\n\n" + extra).strip()
    return out


def _first_int(d: dict, *keys) -> int:
    """Возвращает первое непустое int-значение из ключей или 0."""
    for k in keys:
        v = d.get(k)
        if v:
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return 0


def _extract_usage(obj) -> dict:
    """Достаёт usage из AIMessage/AIMessageChunk через usage_metadata либо response_metadata.

    Возвращает {input, output, total, reasoning} или пустой dict.
    Источники по приоритету:
      1) obj.usage_metadata
      2) obj.response_metadata.token_usage|usage (OpenAI/Anthropic-совместимое)
    """
    um = getattr(obj, "usage_metadata", None) or {}
    if isinstance(um, dict) and um:
        out: dict = {}
        inp = _first_int(um, "input_tokens")
        outp = _first_int(um, "output_tokens")
        if inp:
            out["input"] = inp
        if outp:
            out["output"] = outp
        tot = _first_int(um, "total_tokens")
        if tot:
            out["total"] = tot
        elif inp or outp:
            out["total"] = inp + outp
        otd = um.get("output_token_details") or {}
        if isinstance(otd, dict):
            reasoning = _first_int(otd, "reasoning")
            if reasoning:
                out["reasoning"] = reasoning
        if out:
            return out

    rm = getattr(obj, "response_metadata", None) or {}
    if not isinstance(rm, dict):
        return {}
    tu = rm.get("token_usage") or rm.get("usage") or {}
    if not isinstance(tu, dict) or not tu:
        return {}

    out = {}
    inp = _first_int(tu, "prompt_tokens", "input_tokens")
    outp = _first_int(tu, "completion_tokens", "output_tokens")
    if inp or outp:
        out["input"] = inp
        out["output"] = outp
        out["total"] = _first_int(tu, "total_tokens") or (inp + outp)
    ctd = tu.get("completion_tokens_details") or {}
    if isinstance(ctd, dict):
        reasoning = _first_int(ctd, "reasoning_tokens")
        if reasoning:
            out["reasoning"] = reasoning
    return out


async def api_send_message(text, system_prompt="", on_chunk=None, model=None, tools=None, images=None, on_reasoning_chunk=None, tool_results=None, extras=None):
    """Отправляет сообщение провайдеру.

    tool_results — структурные результаты раунда (list[dict] из
    agent.messages.build_structured_tool_results). В native режиме они
    превращаются в по одному ToolMessage на pending tool_call_id; extras
    (план/проверки/статистика) уходят отдельным HumanMessage. В text-режиме
    эти параметры игнорируются — туда вызывающий код передаёт уже готовый
    плоский payload через text.
    """
    session = get_api_session()
    if session is None:
        raise RuntimeError("API session not initialized. Use /api to configure.")

    if model and model != session.model_id:
        try:
            llm = get_provider(session.provider_id, model, **_provider_kwargs())
        except Exception as e:
            logger.error("Model override '" + str(model) + "' failed, fallback: " + str(e))
            llm = session.llm
    else:
        llm = session.llm

    # ── Режим вызова инструментов определяется провайдером:
    #   tool_format = "text"  (default) → НЕ биндим tools, только :::call блоки
    #   tool_format = "native"          → биндим tools, гибрид :::call + native
    # Переключается через /api → провайдер → "Tool format".
    use_tools = bool(tools) and session.use_native_tools
    logger.info(
        f"API request: provider={session.provider_id} "
        f"use_native_tools={session.use_native_tools} "
        f"hybrid_bind={use_tools} has_tools={bool(tools)}"
    )
    if use_tools:
        try:
            llm = llm.bind_tools(tools, tool_choice="auto")
        except Exception as e:
            logger.warning("bind_tools failed: " + str(e))
            use_tools = False

    log_event(
        "api_send_start",
        provider=session.provider_id,
        model=session.model_id,
        native=session.use_native_tools,
        use_tools=use_tools,
        input_len=len(str(text or "")),
        input_has_transcript=has_transcript_hint(str(text or "")),
        input_preview=str(text or "")[:2000],
    )

    from apis._context_pruner import prune_messages
    # prune_messages не мутирует входной список — копия не нужна.
    messages, prune_stats = prune_messages(session.messages)
    if prune_stats["pruned_blocks"]:
        logger.info(
            "context pruner: evicted %d read-block(s), saved ~%d chars",
            prune_stats["pruned_blocks"], prune_stats["saved_chars"],
        )
    effective_prompt = system_prompt
    # Инструкции про tool-format (native vs text) теперь ЧАСТЬ системного
    # промта (system_prompt.build_system_prompt → TOOL_FORMAT_TEXT_BLOCK по
    # _resolve_native_tools()). Здесь больше ничего не дописываем, чтобы не
    # дублировать. Единственный источник правды — build_system_prompt.
    #
    # ВАЖНО: учитываем ТОЛЬКО «настоящий» системный промпт. После /compress в
    # истории появляются system-сообщения с compress-summary (помечены
    # additional_kwargs["compressed"]). Они НЕ являются системным промптом —
    # если их считать за SystemMessage, реальный промпт (правила, формат
    # tool-calls) перестаёт инжектиться и модель «забывает» как работать.
    def _is_real_system(m) -> bool:
        return (
            isinstance(m, SystemMessage)
            and not (getattr(m, "additional_kwargs", None) or {}).get("compressed")
        )

    if effective_prompt and not any(_is_real_system(m) for m in messages):
        # Вставляем реальный промпт ПЕРЕД любым compress-summary, чтобы порядок
        # был: системный промпт → summary истории → диалог.
        insert_at = 0
        for i, m in enumerate(messages):
            if isinstance(m, SystemMessage):
                insert_at = i
                break
        messages.insert(insert_at, SystemMessage(content=effective_prompt))
    mm_content_cached = None
    has_images = False
    pending_tool_calls = _pending_native_tool_calls(messages) if use_tools else []
    tool_result_messages: list[ToolMessage] = []
    extras_message: HumanMessage | None = None
    images_message: HumanMessage | None = None
    # plan/think — контрол-вызовы: модель могла вызвать ТОЛЬКО их (без реальных
    # тулов), тогда loop отправляет следующий запрос с tool_results=None. Но
    # провайдер всё равно требует ToolMessage на КАЖДЫЙ незакрытый tool_call_id
    # — иначе 400. Поэтому закрываем pending даже при tool_results is None,
    # если среди них есть control-вызовы (им build_native_tool_messages выдаст ack).
    _CONTROL_NAMES = {"plan", "think"}
    has_pending_control = any(
        (tc.get("name") or "") in _CONTROL_NAMES for tc in pending_tool_calls
    )
    if pending_tool_calls and (tool_results is not None or has_pending_control):
        # Native: по одному ToolMessage на tool_call_id (name+FIFO), а extras
        # (план/проверки/статистика) — ОТДЕЛЬНЫМ HumanMessage, чтобы не
        # попасть внутрь tool_result и не путать модель.
        tool_result_messages = build_native_tool_messages(
            pending_tool_calls, tool_results or [],
        )
        messages.extend(tool_result_messages)
        # Изображения от инструментов (docx_screenshot и пр.) НЕ влезают в
        # ToolMessage надёжно через все провайдеры → прикрепляем их отдельным
        # multimodal HumanMessage сразу после tool-результатов. Без этого в
        # native-режиме картинки молча терялись (модель видела только текст).
        if images:
            img_content = _build_multimodal_content("", images)
            has_images = any(p.get("type") == "image_url" for p in img_content)
            if has_images:
                images_message = HumanMessage(
                    content=img_content, additional_kwargs={"synthetic": True},
                )
                messages.append(images_message)
                logger.info(
                    "API send: %d tool image(s) attached as multimodal HumanMessage",
                    sum(1 for p in img_content if p.get("type") == "image_url"),
                )
        if extras and str(extras).strip():
            extras_message = HumanMessage(
                content=str(extras), additional_kwargs={"synthetic": True},
            )
            messages.append(extras_message)
        log_event(
            "native_tool_results_as_tool_messages",
            pending=len(pending_tool_calls),
            emitted=len(tool_result_messages),
            results=len(tool_results or []),
            images=len(images or []),
            extras_len=len(str(extras or "")),
        )
    elif tool_results is not None:
        # Гибрид: провайдер native, но в прошлом ответе модель использовала
        # текстовые :::call блоки (а не native function-calling) → pending
        # tool_calls нет. Шлём результаты плоским text-payload + extras одним
        # HumanMessage, иначе ушло бы пустое сообщение и модель потеряла бы
        # вывод инструментов.
        from system_prompt import build_tool_results as _build_tool_results
        payload = _build_tool_results(tool_results)
        if extras and str(extras).strip():
            payload = (payload + "\n\n" + str(extras)).strip()
        messages.append(HumanMessage(
            content=payload, additional_kwargs={"synthetic": True},
        ))
        text = payload  # для записи в ApiSession ниже (session.add_user)
        # Изображения от инструментов в гибрид-режиме тоже нужно прикрепить
        # отдельным multimodal HumanMessage — иначе модель их молча не увидит
        # (раньше в этой ветке images терялись, в отличие от native-ветки).
        if images:
            img_content = _build_multimodal_content("", images)
            has_images = any(p.get("type") == "image_url" for p in img_content)
            if has_images:
                images_message = HumanMessage(
                    content=img_content, additional_kwargs={"synthetic": True},
                )
                messages.append(images_message)
                logger.info(
                    "API send: %d tool image(s) attached as multimodal HumanMessage (hybrid)",
                    sum(1 for p in img_content if p.get("type") == "image_url"),
                )
        log_event(
            "native_tool_results_fallback_text",
            results=len(tool_results),
            extras_len=len(str(extras or "")),
            payload_len=len(payload),
            images=len(images or []),
        )
    elif images:
        mm_content_cached = _build_multimodal_content(text, images)
        has_images = any(p.get("type") == "image_url" for p in mm_content_cached)
        if has_images:
            messages.append(HumanMessage(content=mm_content_cached))
            logger.info(f"API send: multimodal message with {sum(1 for p in mm_content_cached if p.get('type') == 'image_url')} image(s)")
        else:
            messages.append(HumanMessage(content=text))
    else:
        messages.append(HumanMessage(content=text))

    # ── Записываем user/tool-сообщение в ApiSession ДО запроса.
    # Так при отмене (Ctrl+C/двойной Ctrl+C) сообщение остаётся в истории
    # и модель видит его в следующем раунде. Раньше запись делалась
    # после llm.astream/ainvoke — при cancel в середине стрима терялась,
    # а necli Session уже содержала вопрос → рассинхрон истории.
    log_event(
        "request_messages_ready",
        count=len(messages),
        tail=_debug_message_summary(messages),
    )

    if tool_result_messages:
        session.messages.extend(tool_result_messages)
        if images_message is not None:
            session.messages.append(images_message)
        if extras_message is not None:
            session.messages.append(extras_message)
    elif has_images and mm_content_cached is not None:
        session.messages.append(
            HumanMessage(
                content=mm_content_cached, additional_kwargs={"synthetic": True},
            )
        )
    else:
        # Гибрид-ветка: текстовый payload + (опционально) отдельное
        # multimodal-сообщение с изображениями инструментов.
        session.add_user(text, synthetic=True)
        if images_message is not None:
            session.messages.append(images_message)

    log_event(
        "api_session_after_user",
        count=len(session.messages),
        tail=_debug_message_summary(session.messages),
    )

    t0 = time.monotonic()
    raw_text = ""
    tool_calls = []
    reasoning_content = ""
    usage_info: dict = {}
    _debug_stream_seen = {"transcript": False}
    _streamed_text = {"last": ""}

    def _debug_on_chunk(full_text: str) -> None:
        if full_text:
            _streamed_text["last"] = full_text
        if has_transcript_hint(full_text) and not _debug_stream_seen["transcript"]:
            _debug_stream_seen["transcript"] = True
            log_event(
                "stream_transcript_seen",
                provider=session.provider_id,
                model=session.model_id,
                len=len(full_text),
                preview=full_text,
            )
        if on_chunk is not None:
            on_chunk(full_text)

    def _extract_reasoning(obj) -> str:
        try:
            add_kw = getattr(obj, "additional_kwargs", None) or {}
            if isinstance(add_kw, dict):
                r = add_kw.get("reasoning_content") or ""
                if r:
                    return r
            resp_meta = getattr(obj, "response_metadata", None) or {}
            if isinstance(resp_meta, dict):
                return resp_meta.get("reasoning_content") or ""
        except Exception:
            logger.debug("reasoning extraction failed", exc_info=True)
            return ""
        return ""

    try:
        if on_chunk is not None:
            final_chunk = await stream_with_throttle_retry(
                lambda: llm.astream(messages),
                _debug_on_chunk,
                on_tool_chunk=lambda c: None,
                on_reasoning_chunk=on_reasoning_chunk,
            )
            raw_text = _content_to_text(getattr(final_chunk, "content", ""))
            tool_calls = list(getattr(final_chunk, "tool_calls", []) or [])
            reasoning_content = _extract_reasoning(final_chunk)
            usage_info = _extract_usage(final_chunk)

            # ── Прокси-баг (OnlySQ для Claude): при наличии tool_calls в
            # финальном чанке .content приходит пустым, хотя текст реально
            # стримился в on_chunk. Без этого восстановления текст модели
            # (reasoning перед вызовом) теряется: не сохраняется в историю,
            # не возвращается наверх — для пользователя ответ «обрывается».
            if not raw_text and _streamed_text["last"]:
                raw_text = _content_to_text(_streamed_text["last"])
                logger.info(
                    "API recovered raw_text from stream buffer: "
                    + str(len(raw_text)) + " chars (final_chunk.content was empty)"
                )

            # ── Фолбэк для прокси-багов (OnlySQ и пр.): при стриминге native
            # tool_calls часто приходят с пустыми args. Если модель решила
            # использовать native — повторяем БЕЗ стрима, чтобы получить
            # корректные аргументы одним JSON-объектом.
            need_fallback = False
            for tc in tool_calls:
                a = tc.get("args")
                if a is None or not isinstance(a, dict):
                    need_fallback = True
                    break
                # Пустой `{}` у инструмента с обязательными параметрами =
                # потерянные при стриминге аргументы (прокси-баг). Напр.
                # memory_write требует name/body — пустой dict valid НЕ бывает.
                # Безаргументные тулы (memory_list) сюда не попадают.
                if not a:
                    from apis.tool_schemas import tool_requires_args
                    if tool_requires_args(tc.get("name") or ""):
                        need_fallback = True
                        break
            if need_fallback:
                logger.warning(
                    "API native tool_calls have empty args after stream — "
                    "retrying without streaming to recover JSON args"
                )
                result = await with_throttle_retry(lambda: llm.ainvoke(messages))
                logger.info(
                    "API fallback ainvoke result: "
                    + str(len(getattr(result, "tool_calls", []) or []))
                    + " tool_calls, content_len="
                    + str(len(_content_to_text(getattr(result, "content", ""))))
                )
                fb_text = _content_to_text(getattr(result, "content", result))
                if has_transcript_hint(fb_text):
                    log_event(
                        "fallback_ainvoke_transcript_seen",
                        provider=session.provider_id,
                        model=session.model_id,
                        len=len(fb_text),
                        preview=fb_text,
                    )
                fb_calls = list(getattr(result, "tool_calls", []) or [])
                if fb_calls:
                    tool_calls = fb_calls
                    if fb_text and not raw_text:
                        raw_text = fb_text
                fb_reasoning = _extract_reasoning(result)
                if fb_reasoning and not reasoning_content:
                    reasoning_content = fb_reasoning
                fb_usage = _extract_usage(result)
                if fb_usage:
                    usage_info = fb_usage

            if tool_calls:
                tool_calls = _ensure_tool_call_ids(tool_calls)
                logger.info(
                    "API native tool_calls: "
                    + str(len(tool_calls))
                    + " calls"
                )
                log_event(
                    "native_tool_calls_received",
                    raw_len=len(raw_text),
                    calls=len(tool_calls),
                )
        else:
            result = await with_throttle_retry(lambda: llm.ainvoke(messages))
            raw_text = _content_to_text(getattr(result, "content", result))
            if has_transcript_hint(raw_text):
                log_event(
                    "nonstream_raw_transcript_seen",
                    provider=session.provider_id,
                    model=session.model_id,
                    len=len(raw_text),
                    preview=raw_text,
                )
            tool_calls = list(getattr(result, "tool_calls", []) or [])
            tool_calls = _ensure_tool_call_ids(tool_calls)
            reasoning_content = _extract_reasoning(result)
            usage_info = _extract_usage(result)
            if on_reasoning_chunk is not None and reasoning_content:
                on_reasoning_chunk(reasoning_content)
    except asyncio.CancelledError:
        # Cancel мог прилететь в середине стрима. raw_text уже содержит
        # частичный текст из on_chunk. Сохраняем то, что успели получить,
        # как assistant-сообщение в ApiSession, чтобы история не оборвалась
        # на user без ответа (иначе следующий запрос пойдёт с "пустым"
        # вопросом в хвосте и провайдеры типа Anthropic/OpenAI начинают
        # ругаться или модель теряет контекст диалога).
        try:
            from agent.sanitizer import sanitize_response as _sanitize
            partial_blocks = _tool_calls_to_text_blocks(tool_calls) if tool_calls else ""
            partial = _sanitize(raw_text or "") + partial_blocks
            if partial.strip():
                session.add_assistant(partial, reasoning_content=reasoning_content)
            else:
                # Совсем ничего не успели — добавляем плейсхолдер,
                # иначе history кончается user-сообщением без ответа.
                session.add_assistant("[Interrupted]")
            logger.info(
                "API cancelled mid-stream: saved partial assistant len="
                + str(len(partial))
            )
        except Exception:
            logger.debug("partial assistant save on cancel failed", exc_info=True)
        raise
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.opt(exception=True).error(
            "API send_message failed after "
            + str(round(elapsed, 1))
            + "s: "
            + type(e).__name__
            + ": "
            + str(e)
        )
        # User уже добавлен в историю — добавим плейсхолдер assistant,
        # чтобы пара user/assistant была сбалансирована для следующих запросов.
        try:
            from agent.sanitizer import sanitize_response as _sanitize
            partial_blocks = _tool_calls_to_text_blocks(tool_calls) if tool_calls else ""
            partial = _sanitize(raw_text or "") + partial_blocks
            if partial.strip():
                session.add_assistant(partial, reasoning_content=reasoning_content)
            else:
                session.add_assistant("[Error: " + type(e).__name__ + "]")
        except Exception:
            logger.debug("partial assistant save on error failed", exc_info=True)
        raise

    elapsed = time.monotonic() - t0
    logger.info(
        "API response: " + str(len(raw_text)) + " chars, " + str(len(tool_calls)) + " tool_calls in " + str(round(elapsed, 1)) + "s | provider=" + session.provider_id + " model=" + session.model_id
        + (" | usage=" + str(usage_info) if usage_info else " | usage=<none>")
    )
    preview = raw_text[:2000].replace("\n", "\\n")
    logger.info("API raw_text preview: " + preview)

    # КРИТИЧНО: чистим raw_text от фейк-transcript ПЕРЕД записью в ApiSession.
    # Иначе галлюцинация (`$ cmd`/`user`/`<query>`/`[Project:]`, дописанная
    # моделью после своих tool-вызовов) сохраняется как assistant-сообщение
    # и улетает модели в СЛЕДУЮЩЕМ запросе → она видит свой мусор и
    # галлюцинирует ещё сильнее (feedback loop). sanitize_response сохраняет
    # реальные :::call блоки и режет только фейк. Возвращаемый наверх text
    # санитизируется отдельно в loop._stream_send — тут чистим именно то,
    # что уходит в историю провайдера.
    from agent.sanitizer import sanitize_response as _sanitize
    clean_raw_text = _sanitize(raw_text)
    try:
        from tools import has_tool_calls as _has_tool_calls
        from tools import truncate_after_last_tool_call as _truncate_after_last_tool_call

        if _has_tool_calls(clean_raw_text):
            clean_raw_text = _truncate_after_last_tool_call(clean_raw_text)
    except Exception:
        logger.debug("assistant tool-tail truncate failed", exc_info=True)
    if len(raw_text) != len(clean_raw_text):
        logger.info(
            "API assistant sanitize: %d → %d chars (stripped fake transcript)",
            len(raw_text), len(clean_raw_text),
        )

    blocks = _tool_calls_to_text_blocks(tool_calls) if tool_calls else ""
    assistant_saved_content = clean_raw_text if (tool_calls and session.use_native_tools) else clean_raw_text + blocks
    log_event(
        "api_response_final",
        provider=session.provider_id,
        model=session.model_id,
        raw_len=len(raw_text),
        clean_len=len(clean_raw_text),
        raw_has_transcript=has_transcript_hint(raw_text),
        saved_has_transcript=has_transcript_hint(assistant_saved_content),
        blocks_len=len(blocks),
        calls=len(tool_calls),
        raw_preview=raw_text,
    )
    session.add_assistant(
        assistant_saved_content,
        tool_calls=tool_calls if (tool_calls and session.use_native_tools) else None,
        reasoning_content=reasoning_content,
    )
    log_event(
        "api_session_after_assistant",
        count=len(session.messages),
        saved_len=len(assistant_saved_content),
        saved_has_transcript=has_transcript_hint(assistant_saved_content),
        saved_tool_calls=len(tool_calls) if (tool_calls and session.use_native_tools) else 0,
        tail=_debug_message_summary(session.messages),
    )
    if reasoning_content:
        logger.info("API reasoning_content captured: " + str(len(reasoning_content)) + " chars")
    return {
        "text": raw_text + blocks,
        "raw_text": raw_text,
        "tool_calls": tool_calls,
        "reasoning_content": reasoning_content,
        "usage": usage_info,
    }


async def api_new_chat():
    session = get_api_session()
    if session:
        session.messages.clear()
        logger.debug("API chat reset")


async def api_compress_history(compress_prompt: str) -> str:
    """One-shot запрос на сжатие истории в чистом контексте.

    Использует активную модель текущей ApiSession — без привязки к
    конкретному провайдеру/модели. Шлёт ОДНО сообщение с промптом
    сжатия (без tools, без истории), ждёт полный ответ и возвращает
    его как строку. Активная ApiSession не трогается — очистку/инжект
    сжатого текста делает вызывающий код.
    """
    session = get_api_session()
    if session is None:
        raise RuntimeError("API session not active")

    llm = session.llm
    provider_id = getattr(session, "provider_id", "?")
    model_id = getattr(session, "model_id", "?")

    t0 = time.monotonic()
    logger.info(
        "API compress: provider=" + str(provider_id)
        + " model=" + str(model_id)
        + " prompt_chars=" + str(len(compress_prompt))
    )

    messages = [HumanMessage(content=compress_prompt)]
    try:
        result = await with_throttle_retry(lambda: llm.ainvoke(messages))
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "API compress failed after " + str(round(elapsed, 1)) + "s: "
            + type(e).__name__ + ": " + str(e)
        )
        raise

    text = _content_to_text(getattr(result, "content", result)).strip()
    elapsed = time.monotonic() - t0
    logger.info(
        "API compress done: " + str(len(text)) + " chars in "
        + str(round(elapsed, 1)) + "s"
    )
    return text


async def api_recap(conversation_text: str) -> str:
    """One-shot рекап диалога в чистом контексте.

    Использует активную модель ApiSession, но НЕ трогает её историю —
    отдельный provider-инстанс, одно сообщение, без tools. Возвращает
    очень короткое (1-2 предложения) напоминание о теме чата на
    языке диалога. Вызывается в фоне на каждом N-м пользовательском
    сообщении; вывод печатает вызывающий код.
    """
    session = get_api_session()
    if session is None:
        raise RuntimeError("API session not active")

    llm = get_provider(session.provider_id, session.model_id, **_provider_kwargs())
    prompt = (
        "Below is the transcript of a coding chat between a user and an AI agent. "
        "Write a VERY SHORT recap (1-2 short sentences, max ~40 words) that says only "
        "what the chat was about: the concrete topic/problem/domain discussed. "
        "Do NOT describe current actions, current focus, next steps, progress, decisions, "
        "or what is being worked on now. Write it as a neutral topic reminder in the SAME "
        "language the conversation is in. No preamble, no headings, no bullet points — "
        "just the sentence(s).\n\n"
        "--- TRANSCRIPT ---\n" + conversation_text + "\n--- END ---"
    )

    t0 = time.monotonic()
    logger.info(
        "API recap: provider=" + str(session.provider_id)
        + " model=" + str(session.model_id)
        + " transcript_chars=" + str(len(conversation_text))
    )
    try:
        result = await with_throttle_retry(lambda: llm.ainvoke([HumanMessage(content=prompt)]))
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "API recap failed after " + str(round(elapsed, 1)) + "s: "
            + type(e).__name__ + ": " + str(e)
        )
        raise
    text = _content_to_text(getattr(result, "content", result)).strip()
    elapsed = time.monotonic() - t0
    logger.info("API recap done: " + str(len(text)) + " chars in " + str(round(elapsed, 1)) + "s")
    return text


async def api_extract_memory(prompt: str) -> str:
    """One-shot извлечение долговременных фактов в чистом контексте.

    Как api_recap: отдельный provider-инстанс активной модели, одно сообщение,
    без tools, история ApiSession не трогается. Возвращает СЫРОЙ текст ответа
    модели (ожидается JSON) — парсинг и запись делает memory.extract.
    """
    session = get_api_session()
    if session is None:
        raise RuntimeError("API session not active")

    llm = get_provider(session.provider_id, session.model_id, **_provider_kwargs())
    t0 = time.monotonic()
    logger.info(
        "API memory-extract: provider=" + str(session.provider_id)
        + " model=" + str(session.model_id)
        + " prompt_chars=" + str(len(prompt))
    )
    try:
        result = await with_throttle_retry(lambda: llm.ainvoke([HumanMessage(content=prompt)]))
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "API memory-extract failed after " + str(round(elapsed, 1)) + "s: "
            + type(e).__name__ + ": " + str(e)
        )
        raise
    text = _content_to_text(getattr(result, "content", result)).strip()
    logger.info(
        "API memory-extract done: " + str(len(text)) + " chars in "
        + str(round(time.monotonic() - t0, 1)) + "s"
    )
    return text


async def api_insights(prompt: str) -> str:
    """One-shot анализ всего общения в чистом контексте.

    Как api_extract_memory: отдельный provider-инстанс активной модели, одно
    сообщение, без tools, история ApiSession не трогается. Возвращает СЫРОЙ
    текст ответа модели (ожидается JSON) — парсинг и рендер делает
    memory.insights.
    """
    session = get_api_session()
    if session is None:
        raise RuntimeError("API session not active")

    llm = get_provider(session.provider_id, session.model_id, **_provider_kwargs())
    t0 = time.monotonic()
    logger.info(
        "API insights: provider=" + str(session.provider_id)
        + " model=" + str(session.model_id)
        + " prompt_chars=" + str(len(prompt))
    )
    try:
        result = await with_throttle_retry(lambda: llm.ainvoke([HumanMessage(content=prompt)]))
    except Exception as e:
        elapsed = time.monotonic() - t0
        logger.error(
            "API insights failed after " + str(round(elapsed, 1)) + "s: "
            + type(e).__name__ + ": " + str(e)
        )
        raise
    text = _content_to_text(getattr(result, "content", result)).strip()
    logger.info(
        "API insights done: " + str(len(text)) + " chars in "
        + str(round(time.monotonic() - t0, 1)) + "s"
    )
    return text



def _split_tool_result_segments(content: str, count: int) -> list[str]:
    """Режет плоский tool_result-блок на `count` сегментов по `$ ` заголовкам.

    Формат payload (system_prompt.build_tool_results): каждый результат начинается
    со строки `$ <cmd>`; первый сегмент может быть без него. Если заголовков
    меньше, чем pending-вызовов, недостающие сегменты = "(no output)"; лишний
    текст клеится к последнему сегменту.
    """
    if count <= 0:
        return []
    lines = content.split("\n")
    segments: list[str] = []
    cur: list[str] = []
    for ln in lines:
        if ln.startswith("$ ") and cur:
            segments.append("\n".join(cur))
            cur = [ln]
        else:
            cur.append(ln)
    if cur:
        segments.append("\n".join(cur))
    if not segments:
        segments = [content]
    if len(segments) > count:
        head = segments[:count - 1]
        head.append("\n".join(segments[count - 1:]))
        segments = head
    while len(segments) < count:
        segments.append("(no output)")
    return [s.strip() or "(no output)" for s in segments]


def restore_api_session_history(necli_session):
    """Восстанавливает API-историю из necli session.

    Контракт под OpenAI/Anthropic: SystemMessage ТОЛЬКО в начале. Все
    промежуточные system-сообщения (nudge / mode_switch / think_notice),
    появившиеся после первого user, склеиваются в следующий user-блок
    как обычный текст-префикс — иначе провайдеры игнорируют их или ругаются.

    В native-режиме (use_native_tools) assistant-сообщения с fenced :::call
    блоками парсятся обратно в нативные tool_calls, а следующий tool_result
    превращается в по одному ToolMessage на tool_call_id — иначе после
    /resume или смены модели терялась связка tool_call↔tool_result и
    провайдер (Anthropic/OpenAI) падал на парности. В fenced-режиме
    поведение прежнее (tool_calls не восстанавливаются, tool_result
    пропускается — он уже зашит в текст следующего user-блока).
    """
    api_sess = get_api_session()
    if api_sess is None:
        logger.warning("restore_api_session_history: no active API session")
        return 0

    native = api_sess.use_native_tools
    # Связка assistant.tool_calls → следующий tool_result. Заполняется при
    # обработке assistant, потребляется при обработке tool_result.
    pending_restore_calls: list[dict] = []

    source_messages = necli_session.messages

    api_sess.messages.clear()
    loaded = 0
    head_system: list[str] = []          # system-сообщения ДО первого user
    pending_inline_system: list[str] = []  # system-сообщения ПОСЛЕ первого user
    seen_user = False

    def _flush_head():
        if head_system:
            joined = "\n\n".join(head_system)
            # Если в head попала compress-мета ([compressed...] + summary),
            # помечаем сообщение флагом, чтобы api_send_message не принял его
            # за настоящий системный промпт и всё равно вставил build_system_prompt.
            is_compressed = any(
                s.lstrip().startswith("[compressed") for s in head_system
            )
            kw = {"compressed": True} if is_compressed else None
            api_sess.messages.append(
                SystemMessage(content=joined, additional_kwargs=kw)
            )
            head_system.clear()

    def _flush_pending_calls():
        # Восстановленный assistant.tool_calls без следующего tool_result —
        # провайдер всё равно требует ToolMessage на КАЖДЫЙ tool_call_id,
        # иначе 400 на парности. Закрываем ack-ами.
        nonlocal pending_restore_calls
        for tc in pending_restore_calls:
            api_sess.messages.append(ToolMessage(
                content="(no output)",
                tool_call_id=tc.get("id", ""),
                name=tc.get("name") or "tool",
            ))
        pending_restore_calls = []

    for msg in source_messages:
        role = msg.role
        content = msg.content
        if not content:
            continue
        loaded += 1
        if role == "system":
            if not seen_user:
                head_system.append(content)
            else:
                pending_inline_system.append(content)
            continue

        if not seen_user:
            _flush_head()

        if native and pending_restore_calls and role != "tool_result":
            _flush_pending_calls()

        if role == "assistant":
            # Перед assistant pending_inline_system быть не должно — если есть,
            # дописываем как префикс к предыдущему user (или создаём пустой user).
            if pending_inline_system:
                prefix = "\n\n".join(pending_inline_system)
                pending_inline_system.clear()
                # Ищем последний HumanMessage с str-content
                attached = False
                for prev in reversed(api_sess.messages):
                    if isinstance(prev, HumanMessage) and isinstance(prev.content, str):
                        prev.content = prev.content + "\n\n" + prefix
                        attached = True
                        break
                if not attached:
                    api_sess.messages.append(HumanMessage(content=prefix))
            if native:
                from tools.parser import parse_tool_calls, strip_tool_calls
                parsed = parse_tool_calls(content)
                if parsed:
                    native_calls = _ensure_tool_call_ids([
                        {"name": c.tool_name, "args": c.args, "id": ""}
                        for c in parsed
                    ])
                    clean = strip_tool_calls(content)
                    api_sess.messages.append(
                        AIMessage(content=clean, tool_calls=native_calls)
                    )
                    pending_restore_calls = native_calls
                else:
                    api_sess.messages.append(AIMessage(content=content))
                    pending_restore_calls = []
            else:
                api_sess.messages.append(AIMessage(content=content))
        elif role == "user":
            seen_user = True
            full = content
            if pending_inline_system:
                full = "\n\n".join(pending_inline_system) + "\n\n" + content
                pending_inline_system.clear()
            api_sess.messages.append(HumanMessage(content=full))
        elif role == "tool_result":
            seen_user = True
            pending_inline_system.clear()
            if native and pending_restore_calls:
                segments = _split_tool_result_segments(content, len(pending_restore_calls))
                for tc, seg in zip(pending_restore_calls, segments):
                    api_sess.messages.append(ToolMessage(
                        content=seg,
                        tool_call_id=tc.get("id", ""),
                        name=tc.get("name") or "tool",
                    ))
                pending_restore_calls = []
            continue
        else:
            logger.debug("restore_api_session_history: unknown role '" + role + "', treating as user")
            seen_user = True
            api_sess.messages.append(HumanMessage(content=content))

    # Если в самом конце остались inline-system без следующего user/assistant —
    # дописываем их к последнему user (или создаём отдельный user).
    if pending_inline_system:
        suffix = "\n\n".join(pending_inline_system)
        attached = False
        for prev in reversed(api_sess.messages):
            if isinstance(prev, HumanMessage) and isinstance(prev.content, str):
                prev.content = prev.content + "\n\n" + suffix
                attached = True
                break
        if not attached:
            api_sess.messages.append(HumanMessage(content=suffix))

    if native and pending_restore_calls:
        _flush_pending_calls()

    if not seen_user:
        _flush_head()

    logger.info("API session restored: " + str(loaded) + " messages from necli session " + necli_session.id[:16])
    return loaded
