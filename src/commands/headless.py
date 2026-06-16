from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

import click

import config
from logger import logger


def _read_stdin_if_piped() -> str:
    """Читает stdin если он не tty (pipe-режим)."""
    if sys.stdin.isatty():
        return ""
    try:
        data = sys.stdin.read()
        return data.strip()
    except Exception:
        return ""


def _fail(msg: str, json_output: bool, code: int = 2) -> None:
    """Завершает с ошибкой консистентно режиму вывода.

    В --json режиме ошибка идёт на stdout как валидный JSON {ok:false,error},
    чтобы CI/cron-скрипт `run --json | parse` не падал на пустом stdout. Без
    --json — привычное `error: ...` на stderr.
    """
    if json_output:
        click.echo(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    else:
        click.echo(f"error: {msg}", err=True)
    sys.exit(code)


def _resolve_model(model_arg: str | None) -> tuple[str, str | None]:
    """Возвращает (display_name, error_or_none)."""
    if model_arg:
        from models import resolve_model
        resolved = resolve_model(model_arg)
        if not resolved:
            return "", f"Model not found: {model_arg!r}"
        return resolved, None
    cfg_model = config.get("model", "")
    if cfg_model:
        return cfg_model, None
    return config.TARGET_MODEL, None


def _check_active_api() -> str | None:
    """Возвращает текст ошибки или None если всё ок."""
    if not config.get_active_api():
        return (
            "No API provider selected. Run once "
            "`python src/main.py cli --api PROVIDER` or use --api."
        )
    return None


def _select_api_model(provider_id: str) -> tuple[str, str | None]:
    """Возвращает model_id для провайдера и ошибку, если выбрать нечего."""
    from apis.registry import get_definition

    defn = get_definition(provider_id)
    if defn is None:
        return "", f"API provider not found: {provider_id}"
    current = config.get_active_api_model() or ""
    if current and defn.get_model_info(current):
        return current, None
    model_id = defn.default_model or (defn.models[0].id if defn.models else "")
    if not model_id:
        return "", f"API provider has no models configured: {provider_id}"
    return model_id, None


async def _run_once(
    prompt: str,
    model: str,
    workdir: str,
    quiet: bool,
    timeout: float | None,
) -> tuple[str, dict]:
    """Запускает один проход агента и возвращает (text, meta)."""
    from agent.loop import run_agent

    started = time.monotonic()

    if not quiet:
        click.echo(f"→ model={model}  workdir={workdir}", err=True)

    def _no_chunk(_chunk: str) -> None:
        return None

    coro = run_agent(
        user_message=prompt,
        model=model,
        on_chunk=_no_chunk,
        working_dir=workdir,
    )

    try:
        if timeout:
            text = await asyncio.wait_for(coro, timeout=timeout)
        else:
            text = await coro
    except asyncio.TimeoutError:
        raise click.ClickException(f"timeout {timeout}s exceeded") from None

    elapsed = time.monotonic() - started
    meta = {
        "model": model,
        "workdir": workdir,
        "elapsed_sec": round(elapsed, 2),
    }
    return text or "", meta


@click.command("run")
@click.argument("prompt", nargs=-1)
@click.option("--model", "-m", default=None, help="Model (id or display_name).")
@click.option("--workdir", "-w", default=None, help="Working directory (defaults to cwd).")
@click.option("--api", "-A", default=None, help="API provider for this run.")
@click.option("--json", "json_output", is_flag=True, help="JSON output to stdout.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress progress on stderr.")
@click.option("--timeout", type=float, default=None, help="Global timeout (sec).")
@click.option(
    "--allow-all", is_flag=True,
    help="Allow all tools without confirmation for this run.",
)
def run_command(
    prompt: tuple[str, ...],
    model: str | None,
    workdir: str | None,
    api: str | None,
    json_output: bool,
    quiet: bool,
    timeout: float | None,
    allow_all: bool,
):
    """Headless mode: run a prompt and print the result.

    Examples:

      necli run "fix the failing test"
      git diff | necli run "write a commit message" --quiet
      necli run --json "how many lines in the project" | jq .text
    """
    logger.info("headless run: argv-prompt-len={} api={} json={}", len(prompt), api, json_output)

    stdin_text = _read_stdin_if_piped()
    cli_text = " ".join(prompt).strip()

    full_prompt_parts = []
    if cli_text:
        full_prompt_parts.append(cli_text)
    if stdin_text:
        full_prompt_parts.append("\n--- STDIN ---\n" + stdin_text)
    full_prompt = "\n".join(full_prompt_parts).strip()

    if not full_prompt:
        _fail("empty prompt (pass as argument or via stdin)", json_output)

    # Активный API
    if api:
        api_model, api_model_err = _select_api_model(api)
        if api_model_err:
            _fail(api_model_err, json_output)
        config.set_active_api(api)
        config.set_active_api_model(api_model)

    err = _check_active_api()
    if err:
        _fail(err, json_output)

    # Модель
    resolved_model, model_err = _resolve_model(model)
    if model_err:
        _fail(model_err, json_output)

    # Инициализация API session
    try:
        from apis.agent_adapter import create_api_session
        api_id = config.get_active_api()
        api_model = config.get_active_api_model() or ""
        create_api_session(api_id, api_model)
    except Exception as e:
        _fail(f"failed to create API session: {e}", json_output)

    # Workdir
    workdir_resolved = os.path.abspath(workdir or os.getcwd())
    if not Path(workdir_resolved).is_dir():
        _fail(f"workdir does not exist: {workdir_resolved}", json_output)

    # allow-all: ставим wildcard, чтобы покрыть и динамически зарегистрированные MCP-tools
    if allow_all:
        from config.permissions import set_decision
        set_decision("*", "allow", "process")
        if not quiet:
            click.echo("→ allow-all: all tools allowed for this run", err=True)

    # Headless override tool format (для harness): NECLI_TOOL_FORMAT_FORCE_NATIVE=1|0
    try:
        tf = os.environ.get("NECLI_TOOL_FORMAT_FORCE_NATIVE")
        if tf in ("0", "1"):
            from config.settings import set_value as _set_setting
            _set_setting("tool_format_force_native", tf == "1")
            if not quiet:
                click.echo(f"→ tool_format_force_native={tf}", err=True)
    except Exception as e:
        if not quiet:
            click.echo(f"warning: failed to set tool format from env: {e}", err=True)

    # В headless ask-режим не имеет смысла. Помечаем процесс как headless,
    # чтобы confirm_tool_call мог сразу отказать вместо зависания на TTY-меню.
    os.environ.setdefault("NECLI_HEADLESS", "1")
    from config.permissions import get_decision
    from tools.registry import list_tools
    ask_tools = [t for t in list_tools() if get_decision(t) == "ask" and t != "poll"]
    if ask_tools and not quiet:
        click.echo(
            f"warning: {len(ask_tools)} tool(s) in ask mode — "
            f"in headless they will be auto-denied. Use --allow-all "
            f"or configure /permissions via interactive mode.",
            err=True,
        )

    # Запуск
    try:
        text, meta = asyncio.run(
            _run_once(full_prompt, resolved_model, workdir_resolved, quiet, timeout),
        )
    except click.ClickException as e:
        # timeout и пр. структурированные ошибки: в --json — как JSON, иначе обычно
        if json_output:
            _fail(e.format_message(), json_output, code=e.exit_code)
        raise
    except KeyboardInterrupt:
        _fail("interrupted", json_output, code=130)
    except Exception as e:
        logger.opt(exception=True).error("headless run failed: {}", e)
        _fail(f"{type(e).__name__}: {e}", json_output, code=1)

    if json_output:
        payload = {
            "ok": True,
            "text": text,
            "model": meta["model"],
            "workdir": meta["workdir"],
            "elapsed_sec": meta["elapsed_sec"],
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")

    sys.exit(0)