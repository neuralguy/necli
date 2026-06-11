"""Исполнение hooks для события и сведение результатов.

Контракт hook (совместим с claude-code):
  - На stdin подаётся JSON payload: {event, tool_name, tool_input, ...}.
  - Hook может вернуть:
      * JSON в stdout c полями:
          decision: "approve" | "block"
          reason: str
          continue: bool                 (false → попросить остановиться)
          systemMessage: str             (показать пользователю)
          additionalContext: str         (подмешать в историю)
          hookSpecificOutput.additionalContext: str
      * либо просто exit code: 0 = ок, 2 = block (stderr → reason), иное = ошибка (не блок).

Синхронный API (run_hooks) — для удобства встраивания в текущий синхронный
execute_call. Внутри гоняет subprocess/HTTP с таймаутом.
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from config.paths import BASE_DIR
from logger import logger

from .matcher import if_matches, matcher_matches
from .schema import HookOutcome, HookSpec

# Защитный потолок суммарного времени на событие, чтобы цепочка hooks
# не подвесила агента надолго даже при многих синхронных hooks.
_MAX_OUTPUT_CHARS = 50_000


def _run_command_hook(
    spec: HookSpec, payload_json: str, working_dir: str | None, event: str = ""
) -> tuple[int, str, str]:
    """Запускает shell-hook. Возвращает (exit_code, stdout, stderr)."""
    env = dict(os.environ)
    env["NECLI_HOOK_EVENT"] = event
    try:
        proc = subprocess.run(
            spec.command,
            shell=True,
            input=payload_json,
            capture_output=True,
            text=True,
            timeout=spec.timeout,
            cwd=working_dir or str(BASE_DIR),
            env=env,
        )
        return proc.returncode, (proc.stdout or "")[:_MAX_OUTPUT_CHARS], (proc.stderr or "")[:_MAX_OUTPUT_CHARS]
    except subprocess.TimeoutExpired:
        logger.warning("hook command timed out after {}s: {}", spec.timeout, spec.command[:80])
        return 124, "", f"hook timed out after {spec.timeout}s"
    except Exception as e:  # noqa: BLE001 — hook не должен ронять агента
        logger.opt(exception=True).error("hook command failed: {}", e)
        return 1, "", f"{type(e).__name__}: {e}"


def _run_http_hook(spec: HookSpec, payload_json: str) -> tuple[int, str, str]:
    """POST payload на URL. Возвращает (exit_code, stdout, stderr)."""
    try:
        import httpx

        headers = {"Content-Type": "application/json"}
        for k, v in spec.headers.items():
            # Интерполяция $VAR / ${VAR} из окружения.
            headers[k] = os.path.expandvars(v)
        resp = httpx.post(spec.url, content=payload_json, headers=headers, timeout=spec.timeout)
        body = (resp.text or "")[:_MAX_OUTPUT_CHARS]
        # 2xx → ок; иной статус трактуем как «не блок, но залогируем».
        code = 0 if resp.is_success else 1
        return code, body, "" if resp.is_success else f"HTTP {resp.status_code}"
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).error("hook http failed: {}", e)
        return 1, "", f"{type(e).__name__}: {e}"


def _apply_output(
    spec: HookSpec,
    code: int,
    stdout: str,
    stderr: str,
    outcome: HookOutcome,
) -> None:
    """Парсит вывод hook и аккумулирует в outcome."""
    parsed: dict[str, Any] | None = None
    text = (stdout or "").strip()
    if text.startswith("{"):
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                parsed = obj
        except json.JSONDecodeError:
            parsed = None

    # 1. JSON-протокол.
    if parsed is not None:
        decision = str(parsed.get("decision", "")).lower()
        reason = str(parsed.get("reason", "")).strip()
        if decision == "block":
            outcome.blocked = True
            if reason:
                outcome.block_reason = reason
        if parsed.get("continue") is False:
            outcome.stop = True
        sysmsg = parsed.get("systemMessage")
        if isinstance(sysmsg, str) and sysmsg.strip():
            outcome.system_messages.append(sysmsg.strip())
        ctx = parsed.get("additionalContext")
        if isinstance(ctx, str) and ctx.strip():
            outcome.additional_context.append(ctx.strip())
        hso = parsed.get("hookSpecificOutput")
        if isinstance(hso, dict):
            hctx = hso.get("additionalContext")
            if isinstance(hctx, str) and hctx.strip():
                outcome.additional_context.append(hctx.strip())
        return

    # 2. Exit-code протокол.
    if code == 2:
        outcome.blocked = True
        msg = (stderr or stdout or "").strip()
        if msg:
            outcome.block_reason = msg
    elif code not in (0, 2):
        # Ошибка hook: не блокируем выполнение, но сообщаем пользователю.
        msg = (stderr or "").strip()
        if msg:
            outcome.system_messages.append(f"hook error: {msg}")
    else:
        # exit 0 с непустым stdout (не-JSON) — трактуем как additionalContext.
        if text:
            outcome.additional_context.append(text)


def run_hooks(
    event: str,
    payload: dict[str, Any] | None = None,
    *,
    working_dir: str | None = None,
) -> HookOutcome:
    """Прогоняет все подходящие hooks события и возвращает сводный HookOutcome.

    Никогда не бросает исключения — любые сбои hooks логируются и игнорируются,
    чтобы не уронить агента.
    """
    outcome = HookOutcome()
    try:
        from config.hooks import load_hooks

        cfg = load_hooks()
    except Exception as e:  # noqa: BLE001
        logger.opt(exception=True).error("hooks: load failed: {}", e)
        return outcome

    matchers = cfg.get(event)
    if not matchers:
        return outcome

    payload = dict(payload or {})
    payload.setdefault("event", event)
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    payload_json = json.dumps(payload, ensure_ascii=False)

    for matcher in matchers:
        if not matcher_matches(matcher.matcher, tool_name):
            continue
        for spec in matcher.hooks:
            try:
                spec.validate()
            except ValueError as e:
                logger.warning("hooks: invalid spec skipped: {}", e)
                continue
            if not if_matches(spec.if_, tool_name, tool_input):
                continue
            if spec.is_async:
                # Fire-and-forget: запускаем без ожидания результата.
                _spawn_async(spec, payload_json, working_dir)
                continue
            if spec.type == "http":
                code, out, err = _run_http_hook(spec, payload_json)
            else:
                code, out, err = _run_command_hook(spec, payload_json, working_dir, event)
            _apply_output(spec, code, out, err, outcome)
            # Блокирующее решение прерывает дальнейшие hooks события.
            if outcome.blocked:
                logger.info("hook blocked event={} tool={}: {}", event, tool_name, outcome.block_reason[:120])
                return outcome

    return outcome


def _spawn_async(spec: HookSpec, payload_json: str, working_dir: str | None) -> None:
    """Запускает async-hook в фоне без ожидания."""
    try:
        if spec.type == "http":
            import threading

            threading.Thread(
                target=_run_http_hook, args=(spec, payload_json), daemon=True
            ).start()
            return
        subprocess.Popen(  # noqa: S602
            spec.command,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=working_dir or str(BASE_DIR),
            text=True,
        ).stdin.write(payload_json)  # type: ignore[union-attr]
    except Exception as e:  # noqa: BLE001
        logger.warning("hooks: async spawn failed: {}", e)
