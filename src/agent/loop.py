"""Основной агентный цикл для API-only режима."""

import asyncio
import os
import time
from pathlib import Path

from rich.console import Console

import config
import tools
from tools import strip_tool_calls
from system_prompt import build_system_prompt
from collections import Counter

from planner import (
    parse_plan_commands,
    strip_plan_commands,
    apply_plan_commands,
    save_plan_file,
    load_plan_file,
    delete_plan_file,
    resolve_plan_command_focus,
)
from tools.registry import is_tool_allowed, build_blocked_result
from tools.background import drain_finished_results
from agent.think import strip_think_blocks, parse_think_blocks
from agent.context import AgentContext
from agent.sanitizer import sanitize_response
from agent.executor import execute_and_show_async
from agent.events import RichEventHandler
from agent.stream import LiveStream, StreamEarlyAbort
from logger import logger
from tools.subagent import set_subagent_context
from agent.messages import (
    gather_proof as _gather_proof,
    is_api_proxy_error as _is_api_proxy_error,
    is_likely_truncated as _is_likely_truncated,
    build_first_message,
    _build_result_message,
    build_structured_tool_results as _build_structured_tool_results,
    _build_result_extras,
    build_continue_message as _build_continue_message,
)


def _api_uses_native_tools() -> bool:
    """True если активная API-сессия доставляет результаты как native ToolMessage."""
    try:
        from apis.agent_adapter import get_api_session
        api_sess = get_api_session()
        return api_sess is not None and bool(getattr(api_sess, "use_native_tools", False))
    except Exception:
        logger.debug("native-tools detection failed", exc_info=True)
        return False

console = Console()

MAX_ITERATIONS = 500


def _format_history_block(history, *, leading_newline: bool = False) -> str:
    """Форматирует список {role,content} в блок CONVERSATION CONTEXT.

    Длинные сообщения (>2000) урезаются: первые 1000 + ...(truncated)... + последние 500.
    leading_newline=True добавляет '\\n' перед заголовком (для конкатенации к system_prompt).
    """
    if not history:
        return ""
    from prompts import CONVERSATION_CONTEXT_HEADER, CONVERSATION_CONTEXT_FOOTER
    header = ("\n" if leading_newline else "") + CONVERSATION_CONTEXT_HEADER
    parts = [header]
    for h_msg in history:
        role = h_msg["role"].upper()
        cnt = h_msg["content"]
        if len(cnt) > 2000:
            cnt = cnt[:1000] + "\n...(truncated)...\n" + cnt[-500:]
        parts.append(f"{role}:\n{cnt}")
    parts.append(CONVERSATION_CONTEXT_FOOTER)
    return "\n".join(parts)


def _clean_for_save(text: str) -> str:
    """Очищает текст ответа от plan/think блоков перед сохранением в session."""
    return strip_think_blocks(strip_plan_commands(text))


def _extract_thoughts(text: str) -> list[str]:
    """Извлекает мысли из call think блоков для сохранения отдельным полем."""
    try:
        return parse_think_blocks(text or "")
    except Exception:
        return []


def _with_interrupt_marker(content: str) -> str:
    """Добавляет маркер остановки к ответу ассистента, если его ещё нет.

    Маркер живёт в тексте сохранённого сообщения (а не во флаге ctx), поэтому
    переживает reset_interrupt() и виден модели в истории на следующем ходу.
    """
    from prompts import INTERRUPTED_NOTICE
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
        (tc.get("name") or "")
        for tc in (native_tool_calls or [])
        if isinstance(tc, dict)
    ]
    if native_names and all(name in ("think", "plan") for name in native_names):
        return True
    return False


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

_current_ctx: AgentContext | None = None


def get_current_plan():
    if _current_ctx:
        return _current_ctx.plan
    return None


def get_current_ctx() -> AgentContext | None:
    return _current_ctx


def set_current_ctx(ctx: AgentContext | None) -> None:
    global _current_ctx
    _current_ctx = ctx


def _format_background_notice(results: list[tools.ToolResult]) -> str:
    """Текстовый блок-уведомление о завершённых фоновых shell-задачах."""
    if not results:
        return ""
    parts = [r.output for r in results if r.output]
    return (
        "[BACKGROUND TASKS FINISHED]\n"
        + "\n---\n".join(parts)
    )

def _collect_image_paths(results: list[tools.ToolResult]) -> list[Path]:
    paths = []
    for r in results:
        if r.image_path and r.image_path.exists():
            paths.append(r.image_path)
        for p in (r.image_paths or []):
            if p and p.exists():
                paths.append(p)
    return paths


def _native_tool_calls_to_calls(native_calls: list[dict] | None) -> list[tools.ToolCall]:
    calls: list[tools.ToolCall] = []
    for tc in native_calls or []:
        name = tc.get("name") or "shell"
        args = tc.get("args") if isinstance(tc.get("args"), dict) else {}
        if name == "shell":
            command = str(args.get("command") or "shell")
        else:
            command = name
        calls.append(tools.ToolCall(command=command, tool_name=name, args=args, raw=""))
    return calls


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


async def _stream_send(text, model, ctx, session=None, images=None, message_num=1,
                       tool_results=None, extras=None):
    """Отправляет сообщение со стримингом через API.

    tool_results/extras — структурная доставка результатов раунда в native
    режиме (см. apis.agent_adapter.api_send_message). В text-режиме не
    используются: туда вызывающий код кладёт готовый payload в text.

    Returns: (sanitized_response, inline_results, inline_call_keys, plan_processed, usage)
    """
    stream = LiveStream(model=model, ctx=ctx, session=session, message_num=message_num)
    stream.start()
    usage: dict = {}
    native_tool_calls: list[dict] = []
    try:
        from apis.agent_adapter import api_send_message, current_active_skills
        from apis.tool_schemas import get_tool_schemas
        from system_prompt import _resolve_native_tools
        api_proof = await _gather_proof(ctx.working_dir)
        # Активные скиллы определяют видимость гейтящихся инструментов
        # (web_search/image_search/ssh/subagent/workflow) — и в промте, и в схемах.
        active_skills = current_active_skills()
        api_sys = build_system_prompt(
            proof=api_proof, mode=ctx.mode, working_dir=ctx.working_dir,
            active_skills=active_skills,
        )
        # tools нужны ТОЛЬКО в native — в fenced они игнорируются (не биндятся),
        # а синтаксис вызова описан в системном промте. Не считаем схемы зря.
        api_tools = (
            get_tool_schemas(ctx.mode, active_skills)
            if _resolve_native_tools() else None
        )
        api_result = await api_send_message(
            text,
            system_prompt=api_sys,
            on_chunk=stream.on_text_update,
            on_reasoning_chunk=stream.on_reasoning_update,
            tools=api_tools,
            images=images,
            tool_results=tool_results,
            extras=extras,
        )
        if isinstance(api_result, dict):
            response = api_result["text"]
            usage = api_result.get("usage") or {}
            native_tool_calls = api_result.get("tool_calls") or []
        else:
            response = api_result
        # ТОЛЬКО native function-calling: think приходит как tool_call и
        # стримится в on_chunk БЕЗ сконвертированных :::call think блоков,
        # поэтому think_log в LiveStream остаётся пустым и thinking-panel не
        # рисуется. Финальный response уже содержит блоки
        # (_tool_calls_to_text_blocks) — точечно добираем недостающие мысли,
        # чтобы stop() напечатал static-панель. В fenced-режиме think уже
        # распарсен из стрим-буфера во время on_text_update — backfill там
        # вреден (дублирует панель в финале), поэтому пропускаем.
        if response and _resolve_native_tools():
            try:
                thoughts = parse_think_blocks(response)
                if len(thoughts) > stream.think_log.total:
                    for t in thoughts[stream.think_log.total:]:
                        stream.think_log.add(t)
            except Exception:
                logger.debug("backfill think_log failed", exc_info=True)
    except asyncio.CancelledError:
        stream.stop(cancelled=True)
        partial = strip_tool_calls(stream.buffer).strip() or "[Interrupted]"
        if session:
            session.add_assistant_message(partial, model=model or "", usage=usage or None, thoughts=_extract_thoughts(stream.buffer))
        raise
    except StreamEarlyAbort:
        logger.info("stream aborted early (precheck failed)")
        stream.stop(show_final=True)
        response = stream.buffer
    except Exception:
        stream.stop(cancelled=True)
        raise
    else:
        stream.stop(show_final=True)
    return sanitize_response(response), stream.inline_results, stream.inline_call_keys, stream._plan_processed_count, usage, native_tool_calls


async def _send_via_api(text, on_chunk, images, tool_results=None, extras=None, return_result: bool = False, system_prompt=""):
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
        get_tool_schemas(mode, current_active_skills())
        if _resolve_native_tools() else None
    )
    result = await api_send_message(
        text, system_prompt=system_prompt, on_chunk=on_chunk, tools=api_tools, images=images,
        tool_results=tool_results, extras=extras,
    )
    # usage не аккумулируется: run_agent — служебная ветка без сессии/биллинга
    if return_result:
        return result if isinstance(result, dict) else {"text": result, "tool_calls": []}
    return result["text"] if isinstance(result, dict) else result


def _process_plan_commands(response: str, ctx: AgentContext, already_processed: int = 0) -> None:
    plan_cmds = parse_plan_commands(response)
    remaining = plan_cmds[already_processed:]
    if remaining:
        plan_events = []
        plan_before = ctx.plan
        for cmd in remaining:
            plan_events.append((
                cmd.action,
                resolve_plan_command_focus(plan_before, cmd),
                str(cmd.data.get("status") or ""),
            ))
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


def _show_plan_between_iterations(ctx: AgentContext):
    # План больше не печатается между итерациями. Просмотр — через /plan.
    pass


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

        outcome = run_hooks(
            "UserPromptSubmit",
            {"prompt": user_message},
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
    except Exception as e:  # noqa: BLE001 — hooks не роняют агента
        logger.opt(exception=True).warning("UserPromptSubmit hook error ignored: {}", e)
        return ""


def _fire_stop_hooks(final_text: str, ctx: AgentContext) -> str:
    """Stop hooks (агент завершил раунд). Показывает systemMessage пользователю.

    Возвращает final_text без изменений (Stop здесь не переписывает ответ —
    лишь информирует/триггерит сайд-эффекты вроде memory-extract)."""
    try:
        from config.hooks import has_hooks

        if not has_hooks("Stop"):
            return final_text
        from hooks import run_hooks

        outcome = run_hooks(
            "Stop",
            {"final_response": final_text},
            working_dir=ctx.working_dir,
        )
        for msg in outcome.system_messages:
            if ctx.event_handler:
                ctx.event_handler.on_status(f"🪝 {msg}", level="info")
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).warning("Stop hook error ignored: {}", e)
    return final_text


async def run_agent(user_message, model=None, on_chunk=None, working_dir=None, history=None, images=None):
    ctx = AgentContext(working_dir=working_dir or os.getcwd())
    if ctx.event_handler is None:
        ctx.event_handler = _wrap_with_telegram(RichEventHandler())
    set_current_ctx(ctx)
    logger.info(
        "run_agent start: model={} workdir={} msg_len={}",
        model, ctx.working_dir, len(user_message or ""),
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
    ctx.original_message = user_message

    # UserPromptSubmit hooks: могут заблокировать отправку или подмешать контекст.
    extra_context = _run_user_prompt_hooks(user_message, ctx)
    if extra_context is None:
        return ""  # заблокировано hook'ом
    if extra_context:
        user_message = f"{user_message}\n\n[hook context]\n{extra_context}"
        ctx.original_message = user_message

    loaded_plan = load_plan_file(ctx.effective_plan_dir)
    if loaded_plan and not loaded_plan.is_complete:
        ctx.plan = loaded_plan
        if ctx.event_handler:
            ctx.event_handler.on_plan_update(ctx.plan)

    first_msg = await build_first_message(
        user_message, ctx.working_dir, history=history,
        plan=ctx.plan,
    )

    # build_first_message больше НЕ вшивает системный промпт в тело сообщения —
    # передаём его отдельно как system_prompt, адаптер вставит SystemMessage.
    from apis.agent_adapter import current_active_skills as _cas
    api_sys = build_system_prompt(
        proof=await _gather_proof(ctx.working_dir),
        mode=ctx.mode, working_dir=ctx.working_dir,
        active_skills=_cas(),
    )
    api_result = await _send_via_api(
        first_msg, on_chunk, images, return_result=True, system_prompt=api_sys,
    )
    full_response = sanitize_response(api_result["text"])
    native_tool_calls = api_result.get("tool_calls") or []
    _process_plan_commands(full_response, ctx)

    for _ in range(MAX_ITERATIONS):
        if _is_api_proxy_error(full_response):
            if ctx.event_handler:
                ctx.event_handler.on_status(
                    "⚠ API returned an error — auto-continuing…", level="warning",
                )
            api_result = await _send_via_api("continue", on_chunk, None, return_result=True)
            full_response = sanitize_response(api_result["text"])
            native_tool_calls = api_result.get("tool_calls") or []
            _process_plan_commands(full_response, ctx)
            continue

        if _api_uses_native_tools():
            calls = _dedupe_tool_calls(_native_tool_calls_to_calls(native_tool_calls))
        else:
            calls = _dedupe_tool_calls(tools.parse_tool_calls(full_response))
        calls = [c for c in calls if c.tool_name not in ("think", "plan")]
        if not calls:
            if _is_control_only_response(full_response, native_tool_calls=native_tool_calls):
                extras = _build_result_extras(
                    plan=ctx.plan, working_dir=ctx.working_dir,
                    step_tracker=ctx.step_tracker, ctx=ctx,
                )
                ctx.step_tracker.reset()
                if _api_uses_native_tools():
                    api_result = await _send_via_api(
                        "", on_chunk, None,
                        tool_results=[], extras=extras or None,
                        return_result=True,
                    )
                else:
                    api_result = await _send_via_api(
                        extras or _build_continue_message(),
                        on_chunk, None, return_result=True,
                    )
                full_response = sanitize_response(api_result["text"])
                native_tool_calls = api_result.get("tool_calls") or []
                _process_plan_commands(full_response, ctx)
                continue

            if _is_likely_truncated(full_response):
                api_result = await _send_via_api(_build_continue_message(), on_chunk, None, return_result=True)
                full_response = sanitize_response(api_result["text"])
                native_tool_calls = api_result.get("tool_calls") or []
                _process_plan_commands(full_response, ctx)
                continue

            return _fire_stop_hooks(_clean_for_save(full_response).strip(), ctx)

        # Сохраняем исходный индекс каждого call, чтобы пересобрать results
        # в порядке появления в ответе модели (web_search/subagent исполняются
        # отдельными путями, но их результаты должны вернуться на свои места).
        ws_calls = [(i, c) for i, c in enumerate(calls) if c.tool_name == 'web_search']
        subagent_calls = [(i, c) for i, c in enumerate(calls) if c.tool_name == 'subagent']
        plain_calls = [(i, c) for i, c in enumerate(calls) if c.tool_name not in ('web_search', 'subagent')]

        indexed_results: list[tuple[int, tools.ToolResult]] = []
        for idx, sa_call in subagent_calls:
            indexed_results.append((idx, await _execute_subagent_call(sa_call, model, ctx)))

        if ws_calls:
            ws_results = await execute_and_show_async([c for _, c in ws_calls], event_handler=ctx.event_handler)
            for (idx, _), r in zip(ws_calls, ws_results):
                indexed_results.append((idx, r))

        if plain_calls:
            plain_results = await execute_and_show_async([c for _, c in plain_calls], event_handler=ctx.event_handler)
            for (idx, _), r in zip(plain_calls, plain_results):
                indexed_results.append((idx, r))

        results = [r for _, r in sorted(indexed_results, key=lambda x: x[0])]
        _show_plan_between_iterations(ctx)
        result_images = _collect_image_paths(results)
        bg_notice = _format_background_notice(drain_finished_results())
        if _api_uses_native_tools():
            struct_results = _build_structured_tool_results(results)
            extras = _build_result_extras(
                plan=ctx.plan, working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker, ctx=ctx,
            )
            if bg_notice:
                extras = (extras + "\n\n" + bg_notice) if extras else bg_notice
            api_result = await _send_via_api(
                "", on_chunk, result_images or None,
                tool_results=struct_results, extras=extras or None,
                return_result=True,
            )
            full_response = api_result["text"]
            native_tool_calls = api_result.get("tool_calls") or []
        else:
            result_msg = _build_result_message(
                results, plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            if bg_notice:
                result_msg = result_msg + "\n\n" + bg_notice
            api_result = await _send_via_api(
                result_msg,
                on_chunk, result_images or None,
                return_result=True,
            )
            full_response = api_result["text"]
            native_tool_calls = api_result.get("tool_calls") or []
        ctx.step_tracker.reset()
        full_response = sanitize_response(full_response)
        _process_plan_commands(full_response, ctx)

    logger.warning("run_agent: MAX_ITERATIONS={} reached", MAX_ITERATIONS)
    return strip_tool_calls(strip_plan_commands(full_response)) + "\n\n[Iteration limit]"


async def _execute_subagent_call(
    call: tools.ToolCall,
    model: str,
    ctx: AgentContext,
) -> tools.ToolResult:
    """Выполняет вызов subagent с мультиплексным отображением."""
    from agent.subagent import SubagentTask, SubagentOrchestrator, format_subagent_results
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
            name="subagent", status="error",
            output=(
                "No valid subagent tasks provided. Use prompt, tasks[], "
                "items+stages, or phases[]."
            ),
            exit_code=1, command=call.command,
        )

    # Резолвим модель каждого таска заранее — нужно для отображения в шапке.
    from agent.subagent_api import resolve_subagent_model
    from apis.agent_adapter import get_api_session
    api_sess_for_label = get_api_session()
    default_pid = api_sess_for_label.provider_id if api_sess_for_label else ""
    default_mid = api_sess_for_label.model_id if api_sess_for_label else (model or config.TARGET_MODEL)
    task_models: list[str] = []
    for t_ in tasks:
        try:
            _pid, mid = resolve_subagent_model(getattr(t_, "model", None), default_pid, default_mid)
        except Exception:
            mid = default_mid
        task_models.append(mid or "")

    buffers = [
        SubagentBuffer(
            index=i, mode=t.mode, prompt=t.prompt, model_label=task_models[i],
            role=t.role or "", preset=t.preset or "", depends_on=t.depends_on,
            phase=t.phase or "", label=t.label or "",
        )
        for i, t in enumerate(tasks)
    ]

    tracker = SubagentTracker(buffers)

    orchestrator = SubagentOrchestrator(
        model=model or config.TARGET_MODEL,
        working_dir=ctx.working_dir,
        buffers=buffers,
        isolate=bool((call.args or {}).get("isolate", False)),
    )

    results = []
    tracker.start()
    try:
        results = await orchestrator.run(tasks)
        await tracker.wait_all_done()
    except Exception as e:
        logger.error("subagent orchestrator.run failed: {}", e, exc_info=True)
        tracker.stop()
        return tools.ToolResult(
            name="subagent",
            status="error",
            output=f"Subagent orchestrator failed: {type(e).__name__}: {e}",
            exit_code=1,
            command=call.command,
        )
    finally:
        for r in results:
            if 0 <= r.task_index < len(buffers):
                buffers[r.task_index].files_changed = len(r.files_changed)
        tracker.stop()

    output = f"Subagent run {summary}\n\n" + format_subagent_results(results, run_dir=orchestrator.run_dir)
    has_errors = any(r.error for r in results)

    return tools.ToolResult(
        name="subagent",
        status="error" if has_errors else "ok",
        output=output,
        exit_code=1 if has_errors else 0,
        command=call.command,
    )


async def run_agent_interactive(user_message, model=None, working_dir=None,
    is_continuation=False, session=None, history=None,
    images=None, mode="agent"):
    logger.info(
        "run_agent_interactive start: model={} mode={} continuation={} msg_len={}",
        model, mode, is_continuation, len(user_message or ""),
    )
    if not is_continuation:
        existing = get_current_ctx()
        if existing and existing.event_handler and not isinstance(existing.event_handler, RichEventHandler):
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
    # Новый ход пользователя — отсчёт «работал Nм» с этого момента.
    # is_continuation здесь означает «в сессии уже была история» (msg_num>1),
    # а НЕ продолжение того же хода, поэтому turn_start_time обновляем всегда.
    ctx.turn_start_time = time.monotonic()
    if not is_continuation:
        ctx.original_message = user_message

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
            user_message, ctx.working_dir, history=history,
            plan=ctx.plan,
            session_dir=str(session.dir) if session else None,
        )

    if session and not is_continuation:
        proof = await _gather_proof(ctx.working_dir)
        system_overhead = build_system_prompt(proof=proof, mode=ctx.mode, working_dir=ctx.working_dir)
        hist_block = _format_history_block(history, leading_newline=True)
        if hist_block:
            system_overhead += hist_block
        session.add_system_message(system_overhead, model=model or "")
    elif session and is_continuation and history:
        session.add_system_message(_format_history_block(history), model=model or "")

    first_images = images
    msg_num = 1
    last_usage: dict = {}

    try:
        full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
            msg, model, ctx, session, images=first_images,
            message_num=msg_num,
        )
    except asyncio.CancelledError:
        # Прервали до первого ответа: full_response/last_usage могли не присвоиться.
        return _handle_hard_interrupt(session, "", model, {})

    _process_plan_commands(full_response, ctx, already_processed=plan_processed)

    for iteration in range(MAX_ITERATIONS):
        if _is_api_proxy_error(full_response):
            if ctx.event_handler:
                ctx.event_handler.on_status(
                    "⚠ API returned an error — auto-continuing…", level="warning",
                )
            if session:
                session.add_assistant_message(full_response, model=model or "", usage=last_usage, thoughts=_extract_thoughts(full_response))

            msg_num += 1
            try:
                full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                    "continue", model, ctx, session,
                    message_num=msg_num,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)

            _process_plan_commands(full_response, ctx, already_processed=plan_processed)
            continue

        if ctx.interrupted:
            if ctx.event_handler:
                ctx.event_handler.on_status(
                    "■ Interrupted by user — waiting for input", level="warning",
                )
            final = _clean_for_save(full_response).strip() or "[Interrupted]"
            if session:
                session.add_assistant_message(
                    _with_interrupt_marker(final),
                    model=model or "", usage=last_usage,
                    thoughts=_extract_thoughts(full_response),
                )
            return final

        if _api_uses_native_tools():
            all_calls = _dedupe_tool_calls(_native_tool_calls_to_calls(native_tool_calls))
        else:
            all_calls = _dedupe_tool_calls(tools.parse_tool_calls(full_response))
        # think — не исполняемый инструмент, а отображаемая мысль.
        # Native function-calling провайдеры присылают его как обычный tool_call;
        # parse_think_blocks в LiveStream уже добавил его в think_log и нарисовал
        # thinking-panel. Если не отфильтровать здесь — execute_and_show_async
        # выполнит его повторно через generic-pipeline → дубль рамок.
        all_calls = [c for c in all_calls if c.tool_name not in ("think", "plan")]
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

        allowed = []
        blocked_results = []
        for c in remaining_calls:
            if is_tool_allowed(c.tool_name, ctx.mode):
                allowed.append(c)
            else:
                blocked_results.append(build_blocked_result(c))
        remaining_calls = allowed

        if blocked_results:
            inline_results.extend(blocked_results)

        subagent_calls = [c for c in remaining_calls if c.tool_name == "subagent"]
        remaining_calls = [c for c in remaining_calls if c.tool_name != "subagent"]

        if subagent_calls:
            for sa_call in subagent_calls:
                sa_result = await _execute_subagent_call(sa_call, model, ctx)
                inline_results.append(sa_result)

        if remaining_calls:
            results = await execute_and_show_async(remaining_calls, event_handler=ctx.event_handler)
            inline_results.extend(results)
            _show_plan_between_iterations(ctx)
            fatal = next((r for r in results if r.fatal), None)
            if fatal:
                if session:
                    session.add_assistant_message(full_response, model=model or "", usage=last_usage, thoughts=_extract_thoughts(full_response))
                    # Сохраняем ВСЕ собранные tool-результаты (не только fatal),
                    # чтобы не потерять параллельные ok-вызовы из того же раунда.
                    full_results_msg = _build_result_message(
                        inline_results, plan=ctx.plan,
                        working_dir=ctx.working_dir,
                        step_tracker=ctx.step_tracker,
                        ctx=ctx,
                    )
                    session.add_tool_result(full_results_msg, model=model or "")
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
                        full_response, model=model or "", usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                    )
                extras = _build_result_extras(
                    plan=ctx.plan, working_dir=ctx.working_dir,
                    step_tracker=ctx.step_tracker, ctx=ctx,
                )
                ctx.step_tracker.reset()
                msg_num += 1
                try:
                    if _api_uses_native_tools():
                        full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                            "", model, ctx, session,
                            message_num=msg_num,
                            tool_results=[],
                            extras=extras or None,
                        )
                    else:
                        payload = extras or _build_continue_message()
                        if session:
                            session.add_system_message(payload, model=model or "")
                        full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                            payload, model, ctx, session,
                            message_num=msg_num,
                        )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(full_response, ctx, already_processed=plan_processed)
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
                    session.add_assistant_message(full_response, model=model or "", usage=last_usage, thoughts=_extract_thoughts(full_response))
                    session.add_system_message(cont, model=model or "")

                msg_num += 1
                try:
                    full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                        cont, model, ctx, session,
                        message_num=msg_num,
                    )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)

                _process_plan_commands(full_response, ctx, already_processed=plan_processed)
                continue

            # Перед завершением хода доставляем уведомления о завершившихся
            # фоновых задачах — модель продолжит, увидев их вывод.
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                if session:
                    session.add_assistant_message(
                        _clean_for_save(full_response).strip(),
                        model=model or "", usage=last_usage,
                        thoughts=_extract_thoughts(full_response),
                    )
                    session.add_system_message(bg_notice, model=model or "")
                ctx.step_tracker.reset()
                msg_num += 1
                try:
                    if _api_uses_native_tools():
                        full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                            "", model, ctx, session,
                            message_num=msg_num,
                            tool_results=[],
                            extras=bg_notice,
                        )
                    else:
                        full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                            bg_notice, model, ctx, session,
                            message_num=msg_num,
                        )
                except asyncio.CancelledError:
                    return _handle_hard_interrupt(session, full_response, model, last_usage)
                _process_plan_commands(full_response, ctx, already_processed=plan_processed)
                continue

            if (
                _clean_for_save(full_response).strip()
                and not getattr(ctx, "silent_console", False)
            ):
                from agent.stream import print_worked_footer
                print_worked_footer(ctx)
            if session:
                session.add_assistant_message(
                    _clean_for_save(full_response).strip(),
                    model=model or "",
                    usage=last_usage,
                    thoughts=_extract_thoughts(full_response),
                )
            return _fire_stop_hooks(_clean_for_save(full_response).strip(), ctx)

        saved_msg = None
        if session:
            saved_msg = session.add_assistant_message(full_response, model=model or "", usage=last_usage, thoughts=_extract_thoughts(full_response))

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
            extras = _build_result_extras(
                plan=ctx.plan, working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker, ctx=ctx,
            )
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                extras = (extras + "\n\n" + bg_notice) if extras else bg_notice
            ctx.step_tracker.reset()
            msg_num += 1
            try:
                full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                    "", model, ctx, session,
                    images=result_images or None,
                    message_num=msg_num,
                    tool_results=struct_results,
                    extras=extras or None,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)
        else:
            result_msg = _build_result_message(
                inline_results, plan=ctx.plan,
                working_dir=ctx.working_dir,
                step_tracker=ctx.step_tracker,
                ctx=ctx,
            )
            bg_notice = _format_background_notice(drain_finished_results())
            if bg_notice:
                result_msg = result_msg + "\n\n" + bg_notice
            ctx.step_tracker.reset()
            if session:
                session.add_tool_result(result_msg, model=model or "")
            msg_num += 1
            try:
                full_response, inline_results, inline_call_keys, plan_processed, last_usage, native_tool_calls = await _stream_send(
                    result_msg, model, ctx, session,
                    images=result_images or None,
                    message_num=msg_num,
                )
            except asyncio.CancelledError:
                return _handle_hard_interrupt(session, full_response, model, last_usage)

        _process_plan_commands(full_response, ctx, already_processed=plan_processed)

    final_text = (
        strip_tool_calls(_clean_for_save(full_response)) + "\n\n[Iteration limit]"
    )
    if session:
        session.add_assistant_message(final_text, model=model or "", usage=last_usage, thoughts=_extract_thoughts(full_response))
    return final_text
