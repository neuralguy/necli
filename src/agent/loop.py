"""Основной агентный цикл для API-only режима."""

import asyncio
import contextvars
import json
import os
import time
from collections import Counter
from pathlib import Path

import config
import tools
from agent._common import build_repeat_tool_notice, native_tool_calls_to_calls
from agent.context import AgentContext
from agent.events import RichEventHandler
from agent.executor import execute_and_show_async
from agent.messages import (
    _build_result_extras,
    _build_result_message,
    build_first_message,
    truncate_history_content,
)
from agent.messages import (
    build_continue_message as _build_continue_message,
)
from agent.messages import (
    build_structured_tool_results as _build_structured_tool_results,
)
from agent.messages import (
    gather_proof as _gather_proof,
)
from agent.messages import (
    is_api_proxy_error as _is_api_proxy_error,
)
from agent.messages import (
    is_likely_truncated as _is_likely_truncated,
)
from agent.sanitizer import sanitize_response
from agent.stream import LiveStream, StreamEarlyAbort
from agent.telemetry import TurnStats, end_round, end_turn, start_round, start_turn
from agent.think import parse_think_blocks, strip_think_blocks
from logger import error, info, logger
from planner import (
    apply_plan_commands,
    delete_plan_file,
    load_plan_file,
    parse_native_plan_commands,
    parse_plan_commands,
    resolve_plan_command_focus,
    save_plan_file,
    strip_plan_commands,
)
from session.tokens import count_tokens
from system_prompt import build_system_prompt
from tools import strip_tool_calls, truncate_after_last_tool_call
from tools.background import drain_finished_results
from tools.registry import build_blocked_result, is_tool_allowed
from tools.subagent import set_subagent_context

# Сильные ссылки нужны до завершения: asyncio loop не обязан удерживать
# fire-and-forget Task. Готовые задачи сами удаляются callback'ом.
_subagent_background_tasks: set[asyncio.Task] = set()

_MAX_STOP_HOOK_CONTINUES = 3
_STOP_HOOK_CONTINUE_PROMPT = "The Stop hook blocked completion. Continue the current task."


async def stop_background_subagent_tasks() -> None:
    """Cancel and join background subagent runs owned by the interactive loop."""
    tasks = list(_subagent_background_tasks)
    _subagent_background_tasks.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _api_uses_native_tools() -> bool:
    """True если активная API-сессия доставляет результаты как native ToolMessage."""
    try:
        from apis.agent_adapter import get_api_session

        api_sess = get_api_session()
        return api_sess is not None and bool(getattr(api_sess, "use_native_tools", False))
    except Exception:
        logger.debug("native-tools detection failed", exc_info=True)
        return False


def _format_history_block(history) -> str:
    """Форматирует список {role,content} в блок CONVERSATION CONTEXT.

    Длинные сообщения (>2000) урезаются: первые 1000 + ...(truncated)... + последние 500.
    """
    if not history:
        return ""
    from system_prompt import CONVERSATION_CONTEXT_FOOTER, CONVERSATION_CONTEXT_HEADER

    header = CONVERSATION_CONTEXT_HEADER
    parts = [header]
    for h_msg in history:
        role = h_msg["role"].upper()
        cnt = truncate_history_content(h_msg["content"])
        parts.append(f"{role}:\n{cnt}")
    parts.append(CONVERSATION_CONTEXT_FOOTER)
    return "\n".join(parts)


def _clean_for_save(text: str) -> str:
    """Очищает текст ответа от plan/think блоков перед сохранением в session."""
    return strip_think_blocks(strip_plan_commands(text))


def _sanitize_agent_response(text: str) -> str:
    cleaned = sanitize_response(text)
    if tools.has_tool_calls(cleaned):
        return truncate_after_last_tool_call(cleaned)
    return cleaned


def _extract_thoughts(text: str) -> list[str]:
    """Извлекает мысли из call think блоков для сохранения отдельным полем."""
    try:
        return parse_think_blocks(text or "")
    except Exception:
        return []


def _extract_native_thoughts(native_calls: list[dict] | None) -> list[str]:
    """Извлекает мысли из native think tool_calls (args.thought).

    В native-режиме мысль приходит отдельным структурным вызовом think, а не
    :::think блоком в тексте, поэтому _extract_thoughts(full_response) пуст.
    """
    thoughts: list[str] = []
    for call in native_calls or []:
        if not isinstance(call, dict) or call.get("name") != "think":
            continue
        args = call.get("args")
        if isinstance(args, dict):
            thought = str(args.get("thought") or "")
            if thought.strip():
                thoughts.append(thought)
    return thoughts


def _native_calls_json(native_calls: list[dict] | None) -> str:
    """Сериализует native tool_calls в JSON для сохранения отдельным сообщением.

    Контрол-инструменты think/plan отфильтровываются — они не исполняются и
    не имеют результата, поэтому не должны ломать парность вызов↔результат.
    """
    calls = [
        {
            "name": tc.get("name") or "shell",
            "args": tc.get("args") if isinstance(tc.get("args"), dict) else {},
        }
        for tc in native_calls or []
        if tc.get("name") not in ("think", "plan")
    ]
    return json.dumps(calls, ensure_ascii=False)


def _native_results_payload(results) -> str:
    """Плоский '$ cmd\\n<output>' payload из ToolResult'ов (формат native ToolMessage).

    Совпадает с форматом _structured_result_content и _split_tool_result_segments,
    чтобы restore_api_session_history корректно разрезал его обратно на сегменты.
    """
    from agent.messages import _result_dicts

    parts = []
    for d in _result_dicts(results):
        cmd = d.get("command") or d.get("name") or "tool"
        header = f"$ {cmd}"
        if d.get("exit_code"):
            header += f" [exit {d['exit_code']}]"
        parts.append(f"{header}\n{d.get('output', '')}".rstrip())
    return "\n\n".join(parts)


def _with_interrupt_marker(content: str) -> str:
    """Добавляет маркер остановки к ответу ассистента, если его ещё нет.

    Маркер живёт в тексте сохранённого сообщения (а не во флаге ctx), поэтому
    переживает reset_interrupt() и виден модели в истории на следующем ходу.
    """
    from system_prompt import INTERRUPTED_NOTICE

    base = (content or "").strip()
    if INTERRUPTED_NOTICE in base:
        return base
    if not base:
        return INTERRUPTED_NOTICE
    return base + "\n\n" + INTERRUPTED_NOTICE


def _handle_hard_interrupt(session, full_response, model, last_usage) -> str:
    """Жёсткая остановка (Ctrl+C дважды → CancelledError): сохраняем частичный
    ответ модели с маркером прерывания, чтобы он не потерялся и модель на
    следующем ходу понимала, что её остановили."""
    ctx = get_current_ctx()
    if ctx is not None:
        ctx.tool_cancel_scope = None
    try:
        if session is not None:
            partial = _clean_for_save(full_response or "").strip()
            session.add_assistant_message(
                _with_interrupt_marker(partial),
                model=model or "",
                usage=last_usage or {},
                thoughts=_extract_thoughts(full_response or ""),
            )
    except Exception:
        logger.debug("save partial on hard interrupt failed", exc_info=True)
    try:
        from apis.agent_adapter import close_pending_native_tool_calls

        close_pending_native_tool_calls()
    except Exception:
        logger.debug("close pending native calls after hard interrupt failed", exc_info=True)
    try:
        from agent.working import finish_working_round

        finish_working_round(get_current_ctx(), force=True)
    except Exception:
        logger.debug("finish Working after interrupt failed", exc_info=True)
    return "[Interrupted]"


def _is_control_only_response(
    text: str,
    plan_processed: int = 0,
    native_tool_calls: list[dict] | None = None,
) -> bool:
    if plan_processed > 0 or parse_plan_commands(text):
        return True
    clean_text = _clean_for_save(text).strip()
    if parse_think_blocks(text or "") and not clean_text:
        return True
    native_names = [
        (tc.get("name") or "") for tc in (native_tool_calls or []) if isinstance(tc, dict)
    ]
    return bool(native_names and all(name in ("think", "plan") for name in native_names))


def _is_raw_reasoning_only_response(text: str, reasoning_content: str) -> bool:
    """True when the provider stopped after raw reasoning without a response."""
    return bool((reasoning_content or "").strip() and not _clean_for_save(text).strip())


_RAW_REASONING_NUDGE = "You haven't finished. Continue."


def _wrap_with_telegram(handler):
    """Оборачивает event handler в TelegramEventHandler если TG включён."""
    try:
        import config as _cfg

        if not _cfg.get_telegram_enabled():
            return handler
        from apis.telegram import get_bridge

        if not get_bridge().is_running:
            return handler
        from agent.telegram_handler import TelegramEventHandler

        return TelegramEventHandler(handler)
    except Exception:
        logger.debug("tg wrap failed", exc_info=True)
        return handler


_current_ctx_var: contextvars.ContextVar[AgentContext | None] = contextvars.ContextVar(
    "agent_context", default=None
)


def get_current_ctx() -> AgentContext | None:
    return _current_ctx_var.get()


def set_current_ctx(ctx: AgentContext | None) -> None:
    _current_ctx_var.set(ctx)


def _refresh_agent_status(ctx: AgentContext | None) -> None:
    """Обновить статус-панель над вводом после действия агента.

    Хук ставит интерактивный цикл; вне интерактива (headless, тесты) он None,
    и вызов — дешёвый no-op.
    """
    if ctx is None or ctx.refresh_status is None:
        return
    try:
        ctx.refresh_status()
    except Exception:
        logger.debug("status refresh failed", exc_info=True)


def _format_background_notice(results: list[tools.ToolResult]) -> str:
    """Текстовый блок результатов фоновых процессов и субагентов."""
    if not results:
        return ""
    parts = [r.output for r in results if r.output]
    return "[BACKGROUND WORK FINISHED]\n" + "\n---\n".join(parts)


def _collect_image_paths(results: list[tools.ToolResult]) -> list[Path]:
    paths = []
    for r in results:
        if r.image_path and r.image_path.exists():
            paths.append(r.image_path)
        for p in r.image_paths or []:
            if p and p.exists():
                paths.append(p)
    return paths


def _tool_call_identity(call: tools.ToolCall) -> tuple[str, str]:
    import json

    try:
        args_key = json.dumps(call.args or {}, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        args_key = str(call.args or {})
    return call.tool_name, args_key


def _dedupe_tool_calls(calls: list[tools.ToolCall]) -> list[tools.ToolCall]:
    seen = set()
    deduped: list[tools.ToolCall] = []
    for call in calls:
        key = _tool_call_identity(call)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(call)
    return deduped


async def _stream_send(
    text, model, ctx, session=None, images=None, message_num=1, tool_results=None, extras=None
):
    """Отправляет сообщение со стримингом через API.

    tool_results/extras — структурная доставка результатов раунда в native
    режиме (см. apis.agent_adapter.api_send_message). В text-режиме не
    используются: туда вызывающий код кладёт готовый payload в text.

    Returns: (sanitized_response, inline_results, inline_call_keys, plan_processed, usage)
    """
    stream = LiveStream(model=model, ctx=ctx, session=session, message_num=message_num)
    stream.start()
    # Round tracking: каждый запрос к модели — отдельный round.
    _turn_stats = getattr(ctx, "_turn_stats", None)
    _round_start_time = time.monotonic()
    if _turn_stats is not None:
        start_round(_turn_stats)
    usage: dict = {}
    native_tool_calls: list[dict] = []
    reasoning_content: str = ""
    finish_reason = None
    stream_incomplete = False
    try:
        from apis.agent_adapter import api_send_message, current_active_skills
        from apis.tool_schemas import get_tool_schemas
        from system_prompt import _resolve_native_tools

        api_proof = await _gather_proof(ctx.working_dir)
        # Активные скиллы передаются для добавления инструкций в системный prompt.
        active_skills = current_active_skills(tool_results)
        api_sys = build_system_prompt(
            proof=api_proof,
            mode=ctx.mode,
            working_dir=ctx.working_dir,
            active_skills=active_skills,
            memory_query=ctx.memory_query,
        )
        # Системный промт в историю не пишется, но входит в реальный контекст
        # запроса — запоминаем его размер, чтобы полоса контекста над полем
        # ввода показывала полный объём (в т.ч. до первого ответа провайдера).
        if session is not None:
            session.system_prompt_tokens = count_tokens(api_sys, model)
            _refresh_agent_status(ctx)
        # tools нужны ТОЛЬКО в native — в fenced они игнорируются (не биндятся),
        # а синтаксис вызова описан в системном промте. Не считаем схемы зря.
        api_tools = get_tool_schemas(ctx.mode, active_skills) if _resolve_native_tools() else None
        # Track API request start
        info(
            "api.request.start",
            provider=getattr(session, "provider_id", "unknown") if session else "unknown",
            model=model,
            tools=len(api_tools) if api_tools else 0,
        )
        api_result = await api_send_message(
            text,
            system_prompt=api_sys,
            on_chunk=stream.on_text_update,
            on_reasoning_chunk=stream.on_reasoning_update,
            on_tool_chunk=stream.on_native_tool_update,
            tools=api_tools,
            images=images,
            tool_results=tool_results,
            extras=extras,
        )
        if isinstance(api_result, dict):
            response = api_result["text"]
            usage = api_result.get("usage") or {}
            native_tool_calls = api_result.get("tool_calls") or []
            reasoning_content = api_result.get("reasoning_content") or ""
            finish_reason = api_result.get("finish_reason")
            stream_incomplete = bool(api_result.get("stream_incomplete", False))

            # Track API metrics
            if usage:
                input_tok = usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                output_tok = usage.get("output_tokens") or usage.get("completion_tokens") or 0
                if hasattr(stream, "_ttfb") and stream._ttfb is not None:
                    ttfb = stream._ttfb
                else:
                    ttfb = 0.0
                api_duration = time.monotonic() - stream.start_time

                # Find turn_stats from context
                if _turn_stats is not None:
                    _turn_stats.add_api_call(api_duration, ttfb, input_tok, output_tok)

                info(
                    "api.request.end",
                    duration=api_duration,
                    ttfb=ttfb,
                    input_tokens=input_tok,
                    output_tokens=output_tok,
                    tool_calls=len(native_tool_calls),
                    finish_reason=finish_reason,
                )
        else:
            response = api_result
            reasoning_content = ""
        # Native function-calling: think приходит отдельным структурным вызовом,
        # поэтому добираем его без синтетического :::call блока.
        if native_tool_calls and _resolve_native_tools():
            try:
                thoughts = [
                    str(call.get("args", {}).get("thought") or "")
                    for call in native_tool_calls
                    if isinstance(call, dict)
                    and call.get("name") == "think"
                    and isinstance(call.get("args"), dict)
                ]
                for thought in thoughts[stream.think_log.total :]:
                    if thought:
                        stream.think_log.add(thought)
            except Exception:
                logger.debug("backfill think_log failed", exc_info=True)
    except asyncio.CancelledError:
        stream.cancel_pending_executions()
        stream.stop(cancelled=True)
        partial = strip_tool_calls(stream.buffer).strip() or "[Interrupted]"
        if session:
            session.add_assistant_message(
                partial,
                model=model or "",
                usage=usage or None,
                thoughts=_extract_thoughts(stream.buffer),
                reasoning=reasoning_content,
            )
        from agent.working import finish_working_round

        finish_working_round(ctx)
        raise
    except StreamEarlyAbort:
        logger.info("stream aborted early (precheck failed)")
        stream.cancel_pending_executions()
        stream.stop(show_final=True)
        response = stream.buffer
    except Exception as e:
        error("api.request.error", exception=str(e), duration=time.monotonic() - stream.start_time)
        stream.cancel_pending_executions()
        stream.stop(cancelled=True)
        from agent.working import finish_working_round

        finish_working_round(ctx)
        raise
    else:
        # A few OpenAI-compatible SSE implementations expose the final text
        # fragment only on the completed response object.  Reconcile it before
        # finalizing the block streamer; otherwise history contains the full
        # answer while the UI intermittently misses its last token.
        if isinstance(response, str):
            await stream.on_text_complete(response)

        # Дожидаемся спекулятивных исполнений до обработки результатов
        await stream.drain_pending_executions()

        stream.stop(show_final=True)
    if _resolve_native_tools() and native_tool_calls:
        _process_plan_commands(response, ctx, native_tool_calls=native_tool_calls)
        stream._plan_processed_count = len(parse_native_plan_commands(native_tool_calls))
    working = getattr(ctx, "working_round", None)
    if working is not None:
        # Usage относится ко всему текущему AI response, а не к отдельным
        # native tool calls, которые могли исполниться ещё во время SSE-стрима.
        # message_num меняется только при реальном следующем запросе к модели,
        # поэтому WorkingRound может безопасно дедуплицировать accounting по нему.
        working.set_usage(usage, stream_index=message_num)
        if _resolve_native_tools():
            call_names = [
                str(call.get("name") or "") for call in native_tool_calls if isinstance(call, dict)
            ]
        else:
            call_names = [call.tool_name for call in tools.parse_tool_calls(response)]
        working.observe_calls(call_names)
    _refresh_agent_status(ctx)
    if _turn_stats is not None:
        end_round(_turn_stats, time.monotonic() - _round_start_time)
    return (
        _sanitize_agent_response(response),
        stream.inline_results,
        stream.inline_call_keys,
        stream._plan_processed_count,
        usage,
        native_tool_calls,
        reasoning_content,
        finish_reason,
        stream_incomplete,
    )


async def _send_via_api(
    text,
    on_chunk,
    images,
    tool_results=None,
    extras=None,
    return_result: bool = False,
    system_prompt="",
):
    """Отправка через API без стрима для retry-веток в run_agent.

    system_prompt передаём ТОЛЬКО на первом вызове хода (headless run_agent):
    адаптер вставит его как SystemMessage. На последующих вызовах пусто —
    SystemMessage уже в истории, адаптер его не продублирует.
    """
    from apis.agent_adapter import api_send_message, current_active_skills
    from apis.tool_schemas import get_tool_schemas
    from system_prompt import _resolve_native_tools

    ctx = get_current_ctx()
    mode = ctx.mode if ctx else "agent"
    # tools только в native (см. _stream_send).
    api_tools = (
        get_tool_schemas(mode, current_active_skills(tool_results))
        if _resolve_native_tools()
        else None
    )
    result = await api_send_message(
        text,
        system_prompt=system_prompt,
        on_chunk=on_chunk,
        tools=api_tools,
        images=images,
        tool_results=tool_results,
        extras=extras,
    )
    # usage не аккумулируется: run_agent — служебная ветка без сессии/биллинга
    if return_result:
        return result if isinstance(result, dict) else {"text": result, "tool_calls": []}
    return result["text"] if isinstance(result, dict) else result


def _process_plan_commands(
    response: str,
    ctx: AgentContext,
    already_processed: int = 0,
    native_tool_calls: list[dict] | None = None,
) -> None:
    plan_cmds = (
        parse_native_plan_commands(native_tool_calls)
        if native_tool_calls is not None and _api_uses_native_tools()
        else parse_plan_commands(response)
    )
    remaining = plan_cmds[already_processed:]
    if remaining:
        plan_events = []
        plan_before = ctx.plan
        for cmd in remaining:
            plan_events.append(
                (
                    cmd.action,
                    resolve_plan_command_focus(plan_before, cmd),
                    str(cmd.data.get("status") or ""),
                )
            )
            plan_before = apply_plan_commands(plan_before, [cmd])
        ctx.plan = plan_before
        if ctx.event_handler and ctx.plan:
            for action, focus_index, status in plan_events:
                if action == "update" and status == "in_progress" and not ctx.plan.is_complete:
                    continue
                ctx.event_handler.on_plan_update(
                    ctx.plan,
                    action=action,
                    focus_index=focus_index,
                )
        if ctx.plan:
            if ctx.plan.is_complete:
                delete_plan_file(ctx.effective_plan_dir)
            else:
                save_plan_file(ctx.plan, ctx.effective_plan_dir)


def _run_user_prompt_hooks(user_message: str, ctx: AgentContext) -> str | None:
    """UserPromptSubmit hooks.

    Возвращает:
      None  — отправка заблокирована hook'ом (continue=false / decision=block);
      ""    — продолжать без доп. контекста;
      str   — доп. контекст для подмешивания в сообщение.
    """
    try:
        from config.hooks import has_hooks

        if not has_hooks("UserPromptSubmit"):
            return ""
        from hooks import run_hooks

        payload = {"prompt": user_message}
        payload.update(_hook_api_route())
        outcome = run_hooks(
            "UserPromptSubmit",
            payload,
            working_dir=ctx.working_dir,
        )
        for msg in outcome.system_messages:
            if ctx.event_handler:
                ctx.event_handler.on_status(f"🪝 {msg}", level="info")
        if outcome.blocked or outcome.stop:
            if ctx.event_handler:
                reason = outcome.block_reason or "Prompt blocked by UserPromptSubmit hook."
                ctx.event_handler.on_status(f"⛔ {reason}", level="warning")
            return None
        return outcome.context_text
    except Exception as e:
        logger.opt(exception=True).warning("UserPromptSubmit hook error ignored: {}", e)
        return ""


def _hook_api_route() -> dict[str, str]:
    try:
        from apis.agent_adapter import get_api_session

        session = get_api_session()
        if session is not None:
            return {
                "provider": str(session.provider_id),
                "model": str(session.model_id),
            }
    except Exception:
        logger.debug("hook API route lookup failed", exc_info=True)
    return {}


def _fire_stop_hooks(
    final_text: str,
    ctx: AgentContext,
    *,
    stop_hook_count: int = 0,
) -> tuple[bool, str]:
    """Run Stop hooks and return ``(completion_blocked, continuation_prompt)``."""
    try:
        from config.hooks import has_hooks

        if not has_hooks("Stop"):
            return False, ""
        from hooks import run_hooks

        payload = {
            "final_response": final_text,
            "stop_hook_active": stop_hook_count > 0,
            "stop_hook_count": stop_hook_count,
        }
        payload.update(_hook_api_route())
        outcome = run_hooks(
            "Stop",
            payload,
            working_dir=ctx.working_dir,
        )
        for msg in outcome.system_messages:
            if ctx.event_handler:
                ctx.event_handler.on_status(f"🪝 {msg}", level="info")
        if outcome.blocked and not outcome.stop:
            prompt_parts = [outcome.block_reason, outcome.context_text]
            prompt = "\n\n".join(part for part in prompt_parts if part.strip())
            if not prompt:
                prompt = _STOP_HOOK_CONTINUE_PROMPT
            return True, prompt
    except Exception as e:
        logger.opt(exception=True).warning("Stop hook error ignored: {}", e)
    return False, ""


async def _run_agent_impl(
    user_message,
    model=None,
    on_chunk=None,
    working_dir=None,
    history=None,
    images=None,
    event_handler=None,
    suppress_project_stats=False,
):
    ctx = AgentContext(
        working_dir=working_dir or os.getcwd(),
        event_handler=event_handler,
        suppress_project_stats=suppress_project_stats,
        memory_query=user_message or "",
    )
    if ctx.event_handler is None:
        ctx.event_handler = _wrap_with_telegram(RichEventHandler())
    set_current_ctx(ctx)
    logger.info(
        "run_agent start: model={} workdir={} msg_len={}",
        model,
        ctx.working_dir,
        len(user_message or ""),
    )

    tools.set_working_dir(ctx.working_dir)
    set_subagent_context(
        model=model or config.TARGET_MODEL,
        working_dir=ctx.working_dir,
        event_handler=ctx.event_handler,
    )
    if ctx.last_fs_snapshot is None:
        try:
            from agent.fs_watcher import take_snapshot_throttled

            ctx.last_fs_snapshot = take_snapshot_throttled(ctx.working_dir)
        except Exception:
            logger.debug("initial fs snapshot failed", exc_info=True)

    # UserPromptSubmit hooks: могут заблокировать отправку или подмешать контекст.
    extra_context = _run_user_prompt_hooks(user_message, ctx)
    if extra_context is None:
        return ""  # заблокировано hook'ом
    if extra_context:
        user_message = f"{user_message}\n\n[hook context]\n{extra_context}"

    loaded_plan = load_plan_file(ctx.effective_plan_dir)
    if loaded_plan and not loaded_plan.is_complete:
        ctx.plan = loaded_plan
        if ctx.event_handler:
            ctx.event_handler.on_plan_update(ctx.plan)

    first_msg = await build_first_message(
        user_message,
        ctx.working_dir,
        history=history,
        plan=ctx.plan,
    )

    # build_first_message больше НЕ вшивает системный промпт в тело сообщения —
    # передаём его отдельно как system_prompt, адаптер вставит SystemMessage.
    from apis.agent_adapter import current_active_skills as _cas

    api_sys = build_system_prompt(
        proof=await _gather_proof(ctx.working_dir),
        mode=ctx.mode,
        working_dir=ctx.working_dir,
        active_skills=_cas(),
        memory_query=ctx.memory_query,
    )
    api_result = await _send_via_api(
        first_msg,
        on_chunk,
        images,
        return_result=True,
        system_prompt=api_sys,
    )
    full_response = _sanitize_agent_response(api_result["text"])
    native_tool_calls = api_result.get("tool_calls") or []
    _process_plan_commands(full_response, ctx, native_tool_calls=native_tool_calls)

    last_tool_name: str | None = None
    stop_hook_count = 0
    while True:
        if _is_api_proxy_error(full_response):
            if ctx.event_handler:
                ctx.event_handler.on_status(
                    "⚠ API returned an error — auto-continuing…",
                    level="warning",
                )
            api_result = await _send_via_api("continue", on_chunk, None, return_result=True)
            full_response = _sanitize_agent_response(api_result["text"])
            native_tool_calls = api_result.get("tool_calls") or []
            _process_plan_commands(full_response, ctx, native_tool_calls=native_tool_calls)
            continue

        if _api_uses_native_tools():
            calls = _dedupe_tool_calls(native_tool_calls_to_calls(native_tool_calls))
        else:
            calls = _dedupe_tool_calls(tools.parse_tool_calls(full_response))
        calls = [c for c in calls if c.tool_name not in ("think", "plan")]
        if calls:
            stop_hook_count = 0
        repeat_tool_notice, last_tool_name = build_repeat_tool_notice(last_tool_name, calls)
        if not calls:
            if _is_control_only_response(full_response, native_tool_calls=native_tool_calls):
                extras = _build_result_extras(
                    plan=ctx.plan,
                    working_dir=ctx.working_dir,
                    step_tracker=ctx.step_tracker,
                    ctx=ctx,
                )
                ctx.step_tracker.reset()
                if _api_uses_native_tools():
                    api_result = await _send_via_api(
                        "",
                        on_chunk,
                        None,
                        tool_results=None,
                        extras=extras or None,
                        return_result=True,
                    )
                else:
                    api_result = await _send_via_api(
                        extras or _build_continue_message(),
                        on_chunk,
                        None,
                        return_result=True,
                    )
                full_response = _sanitize_agent_response(api_result["text"])
                native_tool_calls = api_result.get("tool_calls") or []
                _process_plan_commands(full_response, ctx, native_tool_calls=native_tool_calls)
                continue

            if _is_likely_truncated(full_response):
                api_result = await _send_via_api(
                    _build_continue_message(), on_chunk, None, return_result=True
                )
                full_response = _sanitize_agent_response(api_result["text"])
                native_tool_calls = api_result.get("tool_calls") or []
                _process_plan_commands(full_response, ctx, native_tool_calls=native_tool_calls)
                continue

            final_text = _clean_for_save(full_response).strip()
            blocked, continuation = _fire_stop_hooks(
                final_text,
                ctx,
                stop_hook_count=stop_hook_count,
            )
            if blocked and stop_hook_count < _MAX_STOP_HOOK_CONTINUES:
                stop_hook_count += 1
                if ctx.event_handler:
                    ctx.event_handler.on_status(
                        "🪝 Stop hook blocked completion — continuing…",
                        level="warning",
                    )
                api_result = await _send_via_api(
                    continuation,
                    on_chunk,
                    None,
                    return_result=True,
                )
                full_response = _sanitize_agent_response(api_result["text"])
                native_tool_calls = api_result.get("tool_calls") or []
                _process_plan_commands(
                    full_response,
                    ctx,
                    native_tool_calls=native_tool_calls,
                )
                continue
            if blocked and ctx.event_handler:
                ctx.event_handler.on_status(
                    "🪝 Stop hook continuation limit reached; ending the round.",
                    level="warning",
                )
            return final_text

        # Сохраняем исходный индекс каждого call, чтобы пересобрать results
        # в порядке появления в ответе модели (web_search/subagent исполняются
        # отдельными путями, но их результаты должны вернуться на свои места).
        ws_calls = [
            (i, c) for i, c in enumerate(calls) if c.tool_name in ("web_search", "web_fetch")
        ]
        subagent_calls = [(i, c) for i, c in enumerate(calls) if c.tool_name == "subagent"]
        plain_calls = [
            (i, c)
            for i, c in enumerate(calls)
            if c.tool_name not in ("web_search", "web_fetch", "subagent")
        ]

        indexed_results: list[tuple[int, tools.ToolResult]] = []
        for idx, sa_call in subagent_calls:
            indexed_results.append(
                (
                    idx,
                    await _execute_subagent_call(sa_call, model, ctx, background=False),
                )
            )

        if ws_calls:
            ws_results = await execute_and_show_async(
                [c for _, c in ws_calls], event_handler=ctx.event_handler
            )
            for (idx, _), r in zip(ws_calls, ws_results, strict=False):
                indexed_results.append((idx, r))

        if plain_calls:
            plain_results = await execute_and_show_async(
                [c for _, c in plain_calls], event_handler=ctx.event_handler
            )
            for (idx, _), r in zip(plain_calls, plain_results, strict=False):
                indexed_results.append((idx, r))

        results = [r for _, r in sorted(indexed_results, key=lambda x: x[0])]
        result_images = _collect_image_paths(results)
        bg_notice = _format_background_notice(drain_finished_results())
        if _api_uses_native_tools():
            struct_results = _build_structured_tool_results(results)
            extras = _build_result_extras(
                plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            if bg_notice:
                extras = (extras + "\n\n" + bg_notice) if extras else bg_notice
            if repeat_tool_notice:
                extras = (extras + "\n\n" + repeat_tool_notice) if extras else repeat_tool_notice
            api_result = await _send_via_api(
                "",
                on_chunk,
                result_images or None,
                tool_results=struct_results,
                extras=extras or None,
                return_result=True,
            )
            full_response = api_result["text"]
            native_tool_calls = api_result.get("tool_calls") or []
        else:
            result_msg = _build_result_message(
                results,
                plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            if bg_notice:
                result_msg = result_msg + "\n\n" + bg_notice
            if repeat_tool_notice:
                result_msg = result_msg + "\n\n" + repeat_tool_notice
            api_result = await _send_via_api(
                result_msg,
                on_chunk,
                result_images or None,
                return_result=True,
            )
            full_response = api_result["text"]
            native_tool_calls = api_result.get("tool_calls") or []
        ctx.step_tracker.reset()
        full_response = _sanitize_agent_response(full_response)
        _process_plan_commands(full_response, ctx, native_tool_calls=native_tool_calls)


async def run_agent(
    user_message,
    model=None,
    on_chunk=None,
    working_dir=None,
    history=None,
    images=None,
    event_handler=None,
    suppress_project_stats=False,
):
    try:
        return await _run_agent_impl(
            user_message,
            model=model,
            on_chunk=on_chunk,
            working_dir=working_dir,
            history=history,
            images=images,
            event_handler=event_handler,
            suppress_project_stats=suppress_project_stats,
        )
    except asyncio.CancelledError:
        try:
            from apis.agent_adapter import close_pending_native_tool_calls

            close_pending_native_tool_calls()
        except Exception:
            logger.debug(
                "close pending native calls after headless cancellation failed", exc_info=True
            )
        return "[Interrupted]"


async def _execute_subagent_call(
    call: tools.ToolCall,
    model: str,
    ctx: AgentContext,
    *,
    background: bool = True,
) -> tools.ToolResult:
    """Запускает subagent в фоне и сразу возвращает управление главному агенту."""
    from agent.subagent import SubagentOrchestrator, SubagentTask, format_subagent_results
    from agent.subagent_render import SubagentBuffer, SubagentTracker
    from tools.subagent_specs import build_subagent_task_specs

    task_specs, summary = build_subagent_task_specs(call.args or {})
    tasks = [
        SubagentTask(
            prompt=spec["prompt"],
            mode="agent",
            model=spec.get("model"),
            role=spec.get("role"),
            preset=spec.get("preset"),
            depends_on=list(spec.get("depends_on") or []),
            phase=spec.get("phase"),
            label=spec.get("label"),
        )
        for spec in task_specs
    ]

    if not tasks:
        return tools.ToolResult(
            name="subagent",
            status="error",
            output=(
                "No valid subagent tasks provided. Use prompt, tasks[], items+stages, or phases[]."
            ),
            exit_code=1,
            command=call.command,
        )

    # Резолвим модель каждого таска заранее — нужно для отображения в шапке.
    from agent.subagent_api import resolve_subagent_model
    from apis.agent_adapter import get_api_session

    api_sess_for_label = get_api_session()
    default_pid = api_sess_for_label.provider_id if api_sess_for_label else ""
    default_mid = (
        api_sess_for_label.model_id if api_sess_for_label else (model or config.TARGET_MODEL)
    )
    task_models: list[str] = []
    for t_ in tasks:
        try:
            _pid, mid = resolve_subagent_model(getattr(t_, "model", None), default_pid, default_mid)
        except Exception:
            mid = default_mid
        task_models.append(mid or "")

    buffers = [
        SubagentBuffer(
            index=i,
            mode=t.mode,
            prompt=t.prompt,
            model_label=task_models[i],
            role=t.role or "",
            preset=t.preset or "",
            depends_on=t.depends_on,
            phase=t.phase or "",
            label=t.label or "",
        )
        for i, t in enumerate(tasks)
    ]

    raw_args = call.args or {}
    tracker = SubagentTracker(
        buffers,
        name=str(raw_args.get("name") or raw_args.get("goal") or ""),
        phased=(
            isinstance(raw_args.get("phases"), list)
            or (
                isinstance(raw_args.get("items"), list) and isinstance(raw_args.get("stages"), list)
            )
        ),
    )

    orchestrator = SubagentOrchestrator(
        model=model or config.TARGET_MODEL,
        working_dir=ctx.working_dir,
        buffers=buffers,
        isolate=bool((call.args or {}).get("isolate", False)),
    )

    tracker.start()

    # У неинтерактивного run_agent нет постоянного event loop: fire-and-forget
    # задача была бы отменена при возврате API-вызова. Там сохраняем синхронную
    # семантику; фон нужен постоянному CLI, где есть ввод и насос уведомлений.
    if not background:
        results = []
        try:
            results = await orchestrator.run(tasks)
        except Exception as e:
            logger.error("subagent orchestrator.run failed: {}", e, exc_info=True)
            return tools.ToolResult(
                name="subagent",
                status="error",
                output=f"Subagent orchestrator failed: {type(e).__name__}: {e}",
                exit_code=1,
                command=call.command,
            )
        finally:
            for result in results:
                if 0 <= result.task_index < len(buffers):
                    buffers[result.task_index].files_changed = len(result.files_changed)
            tracker.stop()
        output = f"Subagent run {summary}\n\n" + format_subagent_results(
            results, run_dir=orchestrator.run_dir
        )
        has_errors = any(result.error for result in results)
        return tools.ToolResult(
            name="subagent",
            status="error" if has_errors else "ok",
            output=output,
            exit_code=1 if has_errors else 0,
            command=call.command,
        )

    async def _finish_in_background() -> None:
        from tools.background import publish_external_result

        results = []
        try:
            results = await orchestrator.run(tasks)
            for result in results:
                if 0 <= result.task_index < len(buffers):
                    buffers[result.task_index].files_changed = len(result.files_changed)
            output = (
                f"[background subagents finished] Subagent run {summary}\n\n"
                + format_subagent_results(results, run_dir=orchestrator.run_dir)
            )
            has_errors = any(result.error for result in results)
            finished = tools.ToolResult(
                name="subagent",
                status="error" if has_errors else "ok",
                output=output,
                exit_code=1 if has_errors else 0,
                command=call.command,
            )
        except asyncio.CancelledError:
            for buffer in buffers:
                if buffer.status not in ("done", "error"):
                    buffer.on_error("Cancelled by user.")
            finished = tools.ToolResult(
                name="subagent",
                status="error",
                output=f"[background subagents stopped] Subagent run {summary}",
                exit_code=130,
                command=call.command,
            )
        except Exception as e:
            logger.error("subagent orchestrator.run failed: {}", e, exc_info=True)
            for buffer in buffers:
                if buffer.status not in ("done", "error"):
                    buffer.on_error(f"{type(e).__name__}: {e}")
            finished = tools.ToolResult(
                name="subagent",
                status="error",
                output=(f"[background subagents failed] {type(e).__name__}: {e}"),
                exit_code=1,
                command=call.command,
            )
        finally:
            try:
                tracker.stop()
            except Exception:
                logger.debug("subagent tracker stop failed", exc_info=True)
        publish_external_result(finished)

    from tools.background import register_external_work

    register_external_work()
    job = asyncio.create_task(
        _finish_in_background(),
        name=f"subagent-run-{id(tracker):x}",
    )
    _subagent_background_tasks.add(job)
    job.add_done_callback(_subagent_background_tasks.discard)

    return tools.ToolResult(
        name="subagent",
        status="ok",
        output=(
            f"Started subagent run {summary} in background. "
            "Continue working; its results will be delivered automatically."
        ),
        exit_code=0,
        command=call.command,
    )


async def run_agent_interactive(
    user_message,
    model=None,
    working_dir=None,
    is_continuation=False,
    session=None,
    history=None,
    images=None,
    mode="agent",
    background_resume=False,
):
    # Start turn tracking
    turn_stats = start_turn(session_id=session.id if session else "")

    logger.info(
        "run_agent_interactive start: model={} mode={} continuation={} msg_len={}",
        model,
        mode,
        is_continuation,
        len(user_message or ""),
    )
    try:
        return await _run_agent_interactive_impl(
            user_message,
            model=model,
            working_dir=working_dir,
            is_continuation=is_continuation,
            session=session,
            history=history,
            images=images,
            mode=mode,
            background_resume=background_resume,
            turn_stats=turn_stats,
        )
    finally:
        end_turn(turn_stats)


async def _run_agent_interactive_impl(
    user_message,
    model=None,
    working_dir=None,
    is_continuation=False,
    session=None,
    history=None,
    images=None,
    mode="agent",
    background_resume=False,
    turn_stats: TurnStats | None = None,
):
    if not is_continuation:
        existing = get_current_ctx()
        if (
            existing
            and existing.event_handler
            and not isinstance(existing.event_handler, RichEventHandler)
        ):
            ctx = existing
            ctx.working_dir = working_dir or os.getcwd()
            ctx.mode = mode
        elif existing is not None:
            # Переиспользуем существующий ctx — сохраняем render_store между сообщениями.
            ctx = existing
            ctx.working_dir = working_dir or os.getcwd()
            ctx.mode = mode
            if ctx.event_handler is None:
                ctx.event_handler = _wrap_with_telegram(RichEventHandler())
        else:
            ctx = AgentContext(working_dir=working_dir or os.getcwd(), mode=mode)
            if ctx.event_handler is None:
                ctx.event_handler = _wrap_with_telegram(RichEventHandler())
        set_current_ctx(ctx)
    else:
        ctx = get_current_ctx() or AgentContext(working_dir=working_dir or os.getcwd(), mode=mode)
        ctx.mode = mode
        set_current_ctx(ctx)

    ctx._turn_stats = turn_stats
    ctx.memory_query = user_message or ""

    tools.set_working_dir(ctx.working_dir)
    set_subagent_context(
        model=model or config.TARGET_MODEL,
        working_dir=ctx.working_dir,
        event_handler=ctx.event_handler,
    )
    if ctx.last_fs_snapshot is None:
        try:
            from agent.fs_watcher import take_snapshot_throttled

            ctx.last_fs_snapshot = take_snapshot_throttled(ctx.working_dir)
        except Exception:
            logger.debug("initial fs snapshot failed", exc_info=True)
    if session is not None:
        try:
            session.ensure_dir()
            ctx.plan_dir = str(session.dir)
            ctx.session_id = session.id
        except Exception as e:
            logger.warning("plan_dir from session.dir failed: {}", e)
    ctx.reset_interrupt()
    if not background_resume:
        extra_context = _run_user_prompt_hooks(user_message, ctx)
        if extra_context is None:
            return ""
        if extra_context:
            user_message = f"{user_message}\n\n[hook context]\n{extra_context}"
    # Автодоставка фонового результата — продолжение прежнего пользовательского
    # хода. Его таймер и Working не начинают заново.
    if not background_resume:
        ctx.turn_start_time = time.monotonic()
    if not is_continuation and ctx.plan is None:
        loaded_plan = load_plan_file(ctx.effective_plan_dir)
        if loaded_plan and not loaded_plan.is_complete:
            ctx.plan = loaded_plan
            if ctx.event_handler:
                ctx.event_handler.on_plan_update(ctx.plan)

    if is_continuation:
        hist_block = _format_history_block(history)
        parts = [hist_block] if hist_block else []
        parts.append(user_message)
        msg = "\n".join(parts)
    else:
        msg = await build_first_message(
            user_message,
            ctx.working_dir,
            history=history,
            plan=ctx.plan,
            session_dir=str(session.dir) if session else None,
        )

    # Не пишем runtime/system/history context в persisted necli Session.
    # API-история живёт в apis.agent_adapter.ApiSession и получает system_prompt
    # отдельно на каждом запросе. Если сохранять эти блоки как session.system,
    # они потом восстанавливаются как часть диалога, дублируют/двигают историю и
    # ухудшают prompt-cache prefix matching.

    first_images = images
    msg_num = 1
    last_usage: dict = {}

    last_tool_name: str | None = None
    auto_continue_count = 0
    stop_hook_count = 0
    max_auto_continues = 3
    # Один живой Working-блок на весь ход пользователя. Последующие запросы к
    # модели после инструментов лишь продолжают его через LiveStream.start().
    from agent.working import begin_working_round, continue_working_round

    if background_resume:
        continue_working_round(ctx, model or "", msg_num, session=session)
    else:
        begin_working_round(ctx, model or "", msg_num, session=session)
    try:
        (
            full_response,
            inline_results,
            inline_call_keys,
            plan_processed,
            last_usage,
            native_tool_calls,
            reasoning_content,
            finish_reason,
            stream_incomplete,
        ) = await _stream_send(
            msg,
            model,
            ctx,
            session,
            images=first_images,
            message_num=msg_num,
        )
    except asyncio.CancelledError:
        ctx.tool_cancel_scope = None
        # Прервали до первого ответа: full_response/last_usage могли не присвоиться.
        return _handle_hard_interrupt(session, "", model, {})

    _process_plan_commands(
        full_response,
        ctx,
        already_processed=plan_processed,
        native_tool_calls=native_tool_calls,
    )

    while True:
        if _is_api_proxy_error(full_response):
            if ctx.event_handler:
                ctx.event_handler.on_status(
                    "⚠ API returned an error — auto-continuing…",
                    level="warning",
                )
            if session:
                session.add_assistant_message(
                    full_response,
                    model=model or "",
                    usage=last_usage,
                    thoughts=_extract_thoughts(full_response),
                    reasoning=reasoning_content,
                )

            msg_num += 1
            try:
                (
                    full_response,
                    inline_results,
                    inline_call_keys,
                    plan_processed,
                    last_usage,
                    native_tool_calls,
                    reasoning_content,
                    finish_reason,
                    stream_incomplete,
                ) = await _stream_send(
                    "continue",
                    model,
                    ctx,
                    session,
                    message_num=msg_num,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)

            _process_plan_commands(
                full_response,
                ctx,
                already_processed=plan_processed,
                native_tool_calls=native_tool_calls,
            )
            continue

        if ctx.interrupted:
            # Native tool calls появляются только в финальном ответе API, поэтому
            # LiveStream не мог показать их как fenced-блоки. Рисуем вызовы
            # перед финализацией Stopped, но не передаём их executor'у.
            if _api_uses_native_tools():
                from agent.display import show_command

                for call in native_tool_calls_to_calls(native_tool_calls):
                    if call.tool_name in ("think", "plan"):
                        continue
                    show_command(
                        call.command,
                        tool_name=call.tool_name,
                        args=call.args,
                        subtitle="skipped (interrupted)",
                        skipped=True,
                    )
                from apis.agent_adapter import close_pending_native_tool_calls

                close_pending_native_tool_calls()
            final = _clean_for_save(full_response).strip() or "[Interrupted]"
            if session:
                thoughts = _extract_thoughts(full_response) + _extract_native_thoughts(
                    native_tool_calls
                )
                session.add_assistant_message(
                    _with_interrupt_marker(final),
                    model=model or "",
                    usage=last_usage,
                    thoughts=thoughts,
                    reasoning=reasoning_content,
                )
            from agent.working import finish_working_round

            finish_working_round(ctx, force=True)
            return final

        if _api_uses_native_tools():
            all_calls = _dedupe_tool_calls(native_tool_calls_to_calls(native_tool_calls))
        else:
            all_calls = _dedupe_tool_calls(tools.parse_tool_calls(full_response))
        # think — не исполняемый инструмент, а отображаемая мысль.
        # Native function-calling провайдеры присылают его как обычный tool_call;
        # parse_think_blocks в LiveStream уже добавил его в think_log и нарисовал
        # thinking-panel. Если не отфильтровать здесь — execute_and_show_async
        # выполнит его повторно через generic-pipeline → дубль рамок.
        all_calls = [c for c in all_calls if c.tool_name not in ("think", "plan")]
        if all_calls:
            # A real tool round is forward progress; a later incomplete/no-final
            # response gets a fresh bounded continuation budget.
            auto_continue_count = 0
            stop_hook_count = 0
        repeat_tool_notice, last_tool_name = build_repeat_tool_notice(last_tool_name, all_calls)
        executed_counts = Counter(inline_call_keys)
        remaining_calls = []
        for c in all_calls:
            # Дедуп по (command, tool_name): два ЛЕГИТИМНО идентичных вызова
            # в одном ответе схлопнутся в один. На практике повтор одинаковой
            # команды в одном раунде — почти всегда дубль парсера, поэтому
            # схлопывание желательно; менять не стоит.
            key = _tool_call_identity(c)
            if executed_counts.get(key, 0) > 0:
                executed_counts[key] -= 1
                continue
            remaining_calls.append(c)

        from apis.agent_adapter import current_active_skills

        active_skills = current_active_skills()
        allowed = []
        blocked_results = []
        for c in remaining_calls:
            if is_tool_allowed(c.tool_name, ctx.mode, active_skills, c.args):
                allowed.append(c)
            else:
                blocked_results.append(build_blocked_result(c, ctx.mode))
        remaining_calls = allowed

        if blocked_results:
            inline_results.extend(blocked_results)

        subagent_calls = [c for c in remaining_calls if c.tool_name == "subagent"]
        remaining_calls = [c for c in remaining_calls if c.tool_name != "subagent"]

        if subagent_calls:
            for sa_call in subagent_calls:
                working = getattr(ctx, "working_round", None)
                if working is not None:
                    working.begin_call("subagent")
                try:
                    sa_result = await _execute_subagent_call(sa_call, model, ctx)
                finally:
                    if working is not None:
                        working.finish_call("subagent")
                inline_results.append(sa_result)
                _refresh_agent_status(ctx)

        if remaining_calls:
            results = await execute_and_show_async(remaining_calls, event_handler=ctx.event_handler)
            inline_results.extend(results)
            fatal = next((r for r in results if r.fatal), None)
            if fatal:
                if session:
                    session.add_assistant_message(
                        full_response,
                        model=model or "",
                        usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                        reasoning=reasoning_content,
                    )
                    # Сохраняем ВСЕ собранные tool-результаты (не только fatal),
                    # чтобы не потерять параллельные ok-вызовы из того же раунда.
                    full_results_msg = _build_result_message(
                        inline_results,
                        plan=ctx.plan,
                        working_dir=ctx.working_dir,
                        step_tracker=ctx.step_tracker,
                        ctx=ctx,
                    )
                    session.add_tool_result(full_results_msg, model=model or "")
                from agent.working import finish_working_round

                finish_working_round(ctx)
                return fatal.output

        if not inline_results and not all_calls:
            # Native: модель вызвала ТОЛЬКО plan (control-tool) — реальных
            # инструментов нет, но провайдер держит незакрытый pending
            # tool_call на plan, которому нужен ToolMessage-ack. Это НЕ конец
            # хода: модель зафиксировала план и ждёт возможности продолжить.
            # Если просто return — pending tool_call осиротеет (следующий
            # запрос упадёт в 400 на парности) и выполнение оборвётся сразу
            # после создания плана. Поэтому шлём пустой раунд (tool_results=[]
            # закроет plan-ack через _control_ack в адаптере) и продолжаем.
            if _is_control_only_response(full_response, plan_processed, native_tool_calls):
                if session:
                    session.add_assistant_message(
                        full_response,
                        model=model or "",
                        usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                        reasoning=reasoning_content,
                    )
                extras = _build_result_extras(
                    plan=ctx.plan,
                    working_dir=ctx.working_dir,
                    step_tracker=ctx.step_tracker,
                    ctx=ctx,
                )
                ctx.step_tracker.reset()
                msg_num += 1
                try:
                    if _api_uses_native_tools():
                        (
                            full_response,
                            inline_results,
                            inline_call_keys,
                            plan_processed,
                            last_usage,
                            native_tool_calls,
                            reasoning_content,
                            finish_reason,
                            stream_incomplete,
                        ) = await _stream_send(
                            "",
                            model,
                            ctx,
                            session,
                            message_num=msg_num,
                            tool_results=[],
                            extras=extras or None,
                        )
                    else:
                        payload = extras or _build_continue_message()
                        if session:
                            session.add_system_message(payload, model=model or "")
                        (
                            full_response,
                            inline_results,
                            inline_call_keys,
                            plan_processed,
                            last_usage,
                            native_tool_calls,
                            reasoning_content,
                            finish_reason,
                            stream_incomplete,
                        ) = await _stream_send(
                            payload,
                            model,
                            ctx,
                            session,
                            message_num=msg_num,
                        )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(
                    full_response,
                    ctx,
                    already_processed=plan_processed,
                    native_tool_calls=native_tool_calls,
                )
                continue

            hit_output_limit = str(finish_reason or "").lower() in {
                "length",
                "max_tokens",
                "max_output_tokens",
            }
            needs_auto_continue = (
                stream_incomplete
                or hit_output_limit
                or _is_raw_reasoning_only_response(full_response, reasoning_content)
            )
            if needs_auto_continue and auto_continue_count < max_auto_continues:
                if ctx.event_handler and stream_incomplete:
                    ctx.event_handler.on_status(
                        "⚠ Stream ended unexpectedly, requesting continuation…",
                        level="warning",
                    )
                if session:
                    session.add_assistant_message(
                        _clean_for_save(full_response).strip(),
                        model=model or "",
                        usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                        reasoning=reasoning_content,
                    )
                    session.add_system_message(_RAW_REASONING_NUDGE, model=model or "")
                auto_continue_count += 1
                msg_num += 1
                try:
                    (
                        full_response,
                        inline_results,
                        inline_call_keys,
                        plan_processed,
                        last_usage,
                        native_tool_calls,
                        reasoning_content,
                        finish_reason,
                        stream_incomplete,
                    ) = await _stream_send(
                        _RAW_REASONING_NUDGE,
                        model,
                        ctx,
                        session,
                        message_num=msg_num,
                    )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(
                    full_response,
                    ctx,
                    already_processed=plan_processed,
                    native_tool_calls=native_tool_calls,
                )
                continue

            # Незакрытый план НЕ пинаем: модель закончила ход — завершаем ответ.
            # План уже сохранён в файл (_process_plan_commands → save_plan_file)
            # и переживёт между сообщениями; продолжит со следующего ввода.
            if _is_likely_truncated(full_response):
                if ctx.event_handler:
                    ctx.event_handler.on_status(
                        "⚠ Response truncated, requesting continuation…", level="warning"
                    )

                cont = _build_continue_message()
                if session:
                    session.add_assistant_message(
                        full_response,
                        model=model or "",
                        usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                        reasoning=reasoning_content,
                    )
                    session.add_system_message(cont, model=model or "")

                msg_num += 1
                try:
                    (
                        full_response,
                        inline_results,
                        inline_call_keys,
                        plan_processed,
                        last_usage,
                        native_tool_calls,
                        reasoning_content,
                        finish_reason,
                        stream_incomplete,
                    ) = await _stream_send(
                        cont,
                        model,
                        ctx,
                        session,
                        message_num=msg_num,
                    )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)

                _process_plan_commands(
                    full_response,
                    ctx,
                    already_processed=plan_processed,
                    native_tool_calls=native_tool_calls,
                )
                continue

            # Перед завершением хода доставляем уведомления о завершившихся
            # фоновых задачах — модель продолжит, увидев их вывод.
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                if session:
                    session.add_assistant_message(
                        _clean_for_save(full_response).strip(),
                        model=model or "",
                        usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                        reasoning=reasoning_content,
                    )
                    session.add_system_message(bg_notice, model=model or "")
                ctx.step_tracker.reset()
                msg_num += 1
                try:
                    if _api_uses_native_tools():
                        (
                            full_response,
                            inline_results,
                            inline_call_keys,
                            plan_processed,
                            last_usage,
                            native_tool_calls,
                            reasoning_content,
                            finish_reason,
                            stream_incomplete,
                        ) = await _stream_send(
                            "",
                            model,
                            ctx,
                            session,
                            message_num=msg_num,
                            tool_results=[],
                            extras=bg_notice,
                        )
                    else:
                        (
                            full_response,
                            inline_results,
                            inline_call_keys,
                            plan_processed,
                            last_usage,
                            native_tool_calls,
                            reasoning_content,
                            finish_reason,
                            stream_incomplete,
                        ) = await _stream_send(
                            bg_notice,
                            model,
                            ctx,
                            session,
                            message_num=msg_num,
                        )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(
                    full_response,
                    ctx,
                    already_processed=plan_processed,
                    native_tool_calls=native_tool_calls,
                )
                continue

            final_text = _clean_for_save(full_response).strip()
            blocked, continuation = _fire_stop_hooks(
                final_text,
                ctx,
                stop_hook_count=stop_hook_count,
            )
            if blocked and stop_hook_count < _MAX_STOP_HOOK_CONTINUES:
                stop_hook_count += 1
                if ctx.event_handler:
                    ctx.event_handler.on_status(
                        "🪝 Stop hook blocked completion — continuing…",
                        level="warning",
                    )
                if session:
                    thoughts = _extract_thoughts(full_response) + _extract_native_thoughts(
                        native_tool_calls
                    )
                    session.add_assistant_message(
                        final_text,
                        model=model or "",
                        usage=last_usage,
                        thoughts=thoughts,
                        reasoning=reasoning_content,
                    )
                    session.add_system_message(continuation, model=model or "")
                msg_num += 1
                try:
                    (
                        full_response,
                        inline_results,
                        inline_call_keys,
                        plan_processed,
                        last_usage,
                        native_tool_calls,
                        reasoning_content,
                        finish_reason,
                        stream_incomplete,
                    ) = await _stream_send(
                        continuation,
                        model,
                        ctx,
                        session,
                        message_num=msg_num,
                    )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(
                    full_response,
                    ctx,
                    already_processed=plan_processed,
                    native_tool_calls=native_tool_calls,
                )
                continue
            if blocked and ctx.event_handler:
                ctx.event_handler.on_status(
                    "🪝 Stop hook continuation limit reached; ending the round.",
                    level="warning",
                )

            from agent.working import finish_working_round

            finish_working_round(ctx)
            if session:
                thoughts = _extract_thoughts(full_response) + _extract_native_thoughts(
                    native_tool_calls
                )
                session.add_assistant_message(
                    final_text,
                    model=model or "",
                    usage=last_usage,
                    thoughts=thoughts,
                    reasoning=reasoning_content,
                )
            return final_text

        saved_msg = None
        if session:
            thoughts = _extract_thoughts(full_response) + _extract_native_thoughts(
                native_tool_calls
            )
            saved_msg = session.add_assistant_message(
                full_response,
                model=model or "",
                usage=last_usage,
                thoughts=thoughts,
                reasoning=reasoning_content,
            )
        _refresh_agent_status(ctx)

        # Web: фиксируем накопленный текст итерации как assistant-message ДО
        # старта следующего стрима. Иначе фронт-овский liveStream.text будет
        # перезаписан стримом следующей итерации, и промежуточный ответ
        # модели (например "✓ Готов: …" перед получением tool_result) пропадёт.
        try:
            eh = ctx.event_handler
            if eh is not None and hasattr(eh, "emit_stream_chunk"):
                visible = _clean_for_save(full_response).strip()
                if visible:
                    msg_id = saved_msg.id if saved_msg is not None else None
                    eh.emit_stream_chunk(visible, "tool_prefix", message_id=msg_id)
        except Exception:
            logger.exception("emit iteration tail tool_prefix failed")

        result_images = _collect_image_paths(inline_results)
        native = _api_uses_native_tools()
        if native:
            # Native: каждый результат — отдельный ToolMessage (по tool_call_id),
            # extras (план/проверки/статистика) — отдельным HumanMessage. Их
            # формирует apis.agent_adapter.api_send_message из tool_results/
            # extras. Плоский текстовый payload в native НЕ строим и в историю
            # necli как tool_result НЕ пишем — источник истины это структурные
            # ToolMessage в ApiSession (add_tool_result сломал бы парность
            # tool_call/tool_result для провайдера).
            struct_results = _build_structured_tool_results(inline_results)
            # В necli-сессию сохраняем вызовы (роль tool_call) и результаты
            # (роль tool_result, формат '$ cmd\\n<output>') отдельными сообщениями,
            # чтобы инструменты были видны в перезагруженной истории и могли быть
            # восстановлены в API-историю. В live API-сессию они уходят
            # структурными ToolMessage — это отдельный путь.
            if session:
                session.add_tool_call_message(
                    _native_calls_json(native_tool_calls), model=model or ""
                )
                session.add_tool_result(_native_results_payload(inline_results), model=model or "")
            extras = _build_result_extras(
                plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                extras = (extras + "\n\n" + bg_notice) if extras else bg_notice
            if repeat_tool_notice:
                extras = (extras + "\n\n" + repeat_tool_notice) if extras else repeat_tool_notice
            ctx.step_tracker.reset()
            msg_num += 1
            try:
                (
                    full_response,
                    inline_results,
                    inline_call_keys,
                    plan_processed,
                    last_usage,
                    native_tool_calls,
                    reasoning_content,
                    finish_reason,
                    stream_incomplete,
                ) = await _stream_send(
                    "",
                    model,
                    ctx,
                    session,
                    images=result_images or None,
                    message_num=msg_num,
                    tool_results=struct_results,
                    extras=extras or None,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)
        else:
            result_msg = _build_result_message(
                inline_results,
                plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                result_msg = result_msg + "\n\n" + bg_notice
            if repeat_tool_notice:
                result_msg = result_msg + "\n\n" + repeat_tool_notice
            ctx.step_tracker.reset()
            if session:
                session.add_tool_result(result_msg, model=model or "")
            msg_num += 1
            try:
                (
                    full_response,
                    inline_results,
                    inline_call_keys,
                    plan_processed,
                    last_usage,
                    native_tool_calls,
                    reasoning_content,
                    finish_reason,
                    stream_incomplete,
                ) = await _stream_send(
                    result_msg,
                    model,
                    ctx,
                    session,
                    images=result_images or None,
                    message_num=msg_num,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)

        _process_plan_commands(
            full_response,
            ctx,
            already_processed=plan_processed,
            native_tool_calls=native_tool_calls,
        )
