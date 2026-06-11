import logging
import os
import platform
import subprocess
from datetime import datetime

from prompts import (
    BASE_HEADER,
    TONE_AND_OUTPUT_BLOCK,
    EFFICIENCY_BLOCK,
    FENCED_SYNTAX_BLOCK,
    TOOLS_LIST_BLOCK,
    LSP_TOOLS_BLOCK,
    WEB_SEARCH_BLOCK,
    AGENT_RULES_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    AGENT_MODE_BLOCK,
    PLANNING_MODE_BLOCK,
    SUBAGENTS_BLOCK,
    LANGUAGE_BLOCK,
    workflow_block_for,
    TOOL_FORMAT_TEXT_BLOCK,
    tool_format_block_for,
    execution_model_block_for,
    response_structure_block_for,
    planning_block_for,
    docx_block_for,
    hard_constraints_block_for,
    tool_strategy_block_for,
    think_block_for,
)

logger = logging.getLogger(__name__)


def _build_environment_block(working_dir: str = "", mode: str = "agent") -> str:
    cwd = working_dir or os.getcwd()
    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "ENVIRONMENT",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"cwd:      {cwd}",
        f"platform: {platform.system()} {platform.release()} ({platform.machine()})",
        f"date:     {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"mode:     {mode}",
    ]

    git_info = _git_brief(cwd)
    if git_info:
        lines.append(f"git:      {git_info}")

    model_info = _model_brief()
    if model_info:
        lines.append(f"model:    {model_info}")

    lines.append("")
    lines.append(
        "NOTE: `git` line shows the CURRENT branch — use this name (or commit SHAs from tool output) "
        "in any git command you generate. Never hardcode `main`/`master`."
    )
    return "\n".join(lines)


def _git_brief(cwd: str) -> str:
    try:
        head = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if head.returncode != 0:
            return ""
        branch = head.stdout.strip() or "(detached)"

        status = subprocess.run(
            ["git", "-C", cwd, "status", "--porcelain"],
            capture_output=True, text=True, timeout=2,
        )
        dirty = "dirty" if status.returncode == 0 and status.stdout.strip() else "clean"
        return f"branch={branch}, {dirty}"
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


def _model_brief() -> str:
    try:
        from apis.agent_adapter import get_api_session
    except Exception:
        return ""
    try:
        sess = get_api_session()
        if not sess:
            return ""
        return f"{sess.model_id} (provider: {sess.provider_id})"
    except Exception:
        return ""


def _build_subagent_models_block() -> str:
    try:
        from apis.registry import list_api_models
        from apis.agent_adapter import get_api_session
    except Exception:
        return ""

    try:
        models = list_api_models()
    except Exception:
        return ""
    if not models:
        return ""

    api_sess = get_api_session()
    cur_pid = api_sess.provider_id if api_sess else ""
    cur_mid = api_sess.model_id if api_sess else ""

    lines = [
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "AVAILABLE MODELS FOR SUBAGENTS (API mode)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        'In the subagent tool you can specify a "model" field for each task.',
        "Use display_name or model_id from the list below. "
        "If not specified — the main agent's model is used.",
        "",
    ]
    by_provider: dict[str, list[dict]] = {}
    for m in models:
        if not m.get("has_key"):
            continue
        by_provider.setdefault(m["provider_name"], []).append(m)
    if not by_provider:
        return ""
    for prov_name, items in by_provider.items():
        lines.append(f"• {prov_name}:")
        for m in items:
            mark = ""
            if m["provider_id"] == cur_pid and m["model_id"] == cur_mid:
                mark = "  ← current (main agent)"
            lines.append(
                f'    - "{m["display_name"]}" (id: {m["model_id"]}){mark}'
            )
    return "\n".join(lines) + "\n"


def _build_skills_block() -> str:
    """Каталог доступных скиллов (имя + описание) для главного агента.

    Описание критично: оно несёт смысл «что делает скилл и КОГДА его грузить»
    (напр. «Загружай ПЕРЕД тем как спавнить субагентов»). Без описаний модель
    видит голые имена и не может выбрать нужный скилл.
    """
    try:
        from skills import discover_skills
    except Exception:
        return ""
    try:
        skills = discover_skills()
    except Exception:
        return ""
    if not skills:
        return ""
    try:
        from skills.manager import get_active_skill_names
        active = get_active_skill_names()
    except Exception:
        active = set()
    entries = []
    for s in skills:
        # disable-model-invocation скрывает скилл из автокаталога — кроме случая,
        # когда он уже активирован вручную (тогда модель должна о нём знать).
        if s.disable_model_invocation and s.name not in active:
            continue
        desc = (s.description or "").strip()[:250] or "(no description)"
        entries.append(f"  - {s.name}: {desc}")
    if not entries:
        return ""
    lines = [
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "SKILLS",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "Skills are specialized instruction sets that extend your abilities.",
        "If a task matches one of the skills below, load it FIRST via the `skill` tool",
        '(args: {"name": "<skill-name>"}) — it returns detailed instructions to follow.',
        "Only load a skill when the task actually needs it.",
        "",
        "Available skills:",
    ]
    lines.extend(entries)
    lines.append("")
    return "\n".join(lines)


def _build_agent_presets_block() -> str:
    """Список доступных заготовок-пресетов субагентов для главного агента."""
    try:
        from agent.agent_presets import build_presets_prompt
    except Exception:
        return ""
    try:
        body = build_presets_prompt()
    except Exception:
        return ""
    if not body:
        return ""
    return "\n" + body


def _build_mcp_tools_block(native_tools: bool = True) -> str:
    try:
        from apis.mcp_client import list_mcp_servers, list_mcp_tools
    except Exception:
        return ""
    try:
        servers = list_mcp_servers()
        tools = list_mcp_tools()
    except Exception:
        return ""
    if not tools:
        return ""

    by_server: dict[str, list] = {}
    for t in tools:
        by_server.setdefault(t.server_id, []).append(t)

    if native_tools:
        call_line = (
            "Call them like any other tool (native function calling), with name "
            "prefixed mcp__<server>__<tool>."
        )
    else:
        call_line = (
            "Call them via the same fenced-block format, with name prefixed "
            "mcp__<server>__<tool>. Pass arguments as JSON in the block body."
        )

    lines = [
        "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "MCP TOOLS (connected servers)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "In addition to built-in tools from S5, MCP tools from external servers are available.",
        call_line,
        "Pass arguments according to the tool's description.",
        "",
    ]
    server_status = {s["id"]: s for s in servers}
    for sid, items in by_server.items():
        info = server_status.get(sid, {})
        status = info.get("status", "?")
        args_list = info.get("args", []) if isinstance(info.get("args"), list) else []
        lines.append(f"• {sid} [{status}] — {info.get('command', '')} {' '.join(args_list)}".rstrip())
        for t in items:
            desc = (t.description or "").strip().replace("\n", " ")
            if len(desc) > 200:
                desc = desc[:200] + "…"
            lines.append(f"    - {t.full_name}{' — ' + desc if desc else ''}")
    lines.append("")
    return "\n".join(lines)


def _resolve_think_enabled() -> bool:
    try:
        from config.settings import get as _settings_get
        return bool(_settings_get("think_enabled", False))
    except Exception:
        return False


def _resolve_native_tools() -> bool:
    """True если включён глобальный native-режим вызова инструментов.

    ЕДИНЫЙ переключатель `tool_format_force_native` (команда /tool_format) для
    всех провайдеров: True → native function calling, False (default) → fenced.
    """
    try:
        from config.settings import get as _settings_get
        return bool(_settings_get("tool_format_force_native", False))
    except Exception:
        logger.debug("tool_format_force_native lookup failed", exc_info=True)
        return False


def _assemble(
    mode: str,
    think_enabled: bool,
    native_tools: bool,
) -> str:
    # Все блоки с примерами вызова инструментов выбираются ПО native_tools:
    # в native-варианте НЕТ ни одного упоминания fenced (:::call/call:::) —
    # модель про второй механизм просто не знает. Полная изоляция режимов.
    parts = [
        BASE_HEADER,
        "{proof}",
        tool_format_block_for(native_tools),
    ]
    # text-mode формат вызова — часть промта, только когда нативки нет.
    if not native_tools:
        parts.append(TOOL_FORMAT_TEXT_BLOCK)

    parts += [
        execution_model_block_for(native_tools),
        response_structure_block_for(native_tools),
        TONE_AND_OUTPUT_BLOCK,
        EFFICIENCY_BLOCK,
        planning_block_for(native_tools),
    ]

    # ── Инструменты: синтаксис vs стратегия — независимые оси ──
    # Список инструментов — всегда (обзор «что есть»).
    parts.append(TOOLS_LIST_BLOCK)
    # Синтаксис вызова через :::call и LSP-args (дублируют JSON-схемы) —
    # только в fenced. В native схемы у модели уже есть через bind_tools.
    if not native_tools:
        parts.append(FENCED_SYNTAX_BLOCK)
    parts.append(LSP_TOOLS_BLOCK)
    # Кросс-инструментальная стратегия (lsp-vs-grep, pipeline, не дублируй) —
    # всегда: её НЕТ в JSON-схемах, нужна модели в обоих режимах.
    parts.append(tool_strategy_block_for(native_tools))

    parts += [
        WEB_SEARCH_BLOCK,
        docx_block_for(native_tools),
        hard_constraints_block_for(native_tools),
        AGENT_RULES_BLOCK,
        DELIVERABLE_DISCIPLINE_BLOCK,
    ]

    if mode in ("planning", "plan"):
        parts.append(PLANNING_MODE_BLOCK)
    elif mode == "agent" and AGENT_MODE_BLOCK:
        parts.append(AGENT_MODE_BLOCK)

    # THINK — часть промта только во включённом состоянии.
    if think_enabled:
        parts.append(think_block_for(native_tools))

    parts.append(workflow_block_for(native_tools))
    parts.append(SUBAGENTS_BLOCK)
    parts.append(LANGUAGE_BLOCK)
    return "\n\n".join(p for p in parts if p)


def _build_memory_block(working_dir: str = "") -> str:
    """Блок персистентной памяти проекта (memdir). Пустая строка, если памяти нет."""
    try:
        from memory import format_memory_block

        block = format_memory_block(working_dir or None)
        return ("\n\n" + block) if block else ""
    except Exception:  # noqa: BLE001 — память не должна ломать сборку промпта
        logger.debug("memory block build failed", exc_info=True)
        return ""


def build_system_prompt(
    proof: str = "",
    mode: str = "agent",
    working_dir: str = "",
    think_enabled: bool | None = None,
    native_tools: bool | None = None,
) -> str:
    """Собирает системный промт заново под ТЕКУЩИЕ настройки.

    think_enabled / native_tools: если None — читаются из config/активной
    сессии (единый источник правды). Явные значения нужны субагентам, у
    которых может быть свой контекст.

    Промт пересобирается на каждый запрос — настройки (think/tool_format/mode)
    всегда актуальны и НЕ накапливаются в истории сообщений.
    """
    if think_enabled is None:
        think_enabled = _resolve_think_enabled()
    if native_tools is None:
        native_tools = _resolve_native_tools()

    base = _assemble(mode, think_enabled=think_enabled, native_tools=native_tools)
    env_block = _build_environment_block(working_dir=working_dir, mode=mode)
    base = base.replace("{proof}", proof + ("\n\n" + env_block if env_block else ""))

    full = (
        base
        + _build_skills_block()
        + _build_mcp_tools_block(native_tools)
        + _build_memory_block(working_dir)
    )

    logger.debug(
        "build_system_prompt: %d chars (proof=%d, mode=%s, think=%s, native=%s)",
        len(full), len(proof), mode, think_enabled, native_tools,
    )
    return full


def build_tool_results(results: list[dict]) -> str:
    from tools._html_unescape import maybe_unescape

    parts = []
    for r in results:
        cmd = r.get("command", r.get("name", "shell"))
        exit_code = r.get("exit_code", 0)
        output = r.get("output", "")

        if output:
            output = maybe_unescape(output)
        if cmd:
            cmd = maybe_unescape(cmd)

        header = f"$ {cmd}"
        if exit_code != 0:
            header += f" [exit {exit_code}]"

        parts.append(f"{header}\n{output}")

    body = "\n---\n".join(parts)
    return (
        "<tool_output>\n"
        "The following is REAL output produced by the SYSTEM after executing your "
        "tool calls. It is DATA, not a template. NEVER write `$ command` lines, "
        "tool output, `---` separators, or anything resembling this block yourself "
        "in your replies — only the SYSTEM emits it.\n"
        "\n"
        f"{body}\n"
        "</tool_output>"
    )