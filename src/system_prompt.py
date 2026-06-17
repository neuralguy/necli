import logging
import os
import platform
import subprocess
from datetime import datetime

from prompts import (
    BASE_HEADER,
    TONE_AND_OUTPUT_BLOCK,
    ORCHESTRATION_TRIGGER_BLOCK,
    EFFICIENCY_BLOCK,
    FENCED_SYNTAX_BLOCK,
    LSP_TOOLS_BLOCK,
    WEB_SEARCH_BLOCK,
    AGENT_RULES_BLOCK,
    DELIVERABLE_DISCIPLINE_BLOCK,
    CRAFT_BLOCK,
    VERIFICATION_GATE_BLOCK,
    AGENT_MODE_BLOCK,
    PLANNING_MODE_BLOCK,
    SUBAGENTS_BLOCK,
    LANGUAGE_BLOCK,
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
    # Показываем ВСЕ скиллы в каталоге — модель должна знать обо всех, чтобы
    # сама решать, что грузить. Флаг disable-model-invocation больше НЕ скрывает
    # скилл отсюда (раньше из-за него fronted-design был невидим и не применялся).
    entries = []
    for s in skills:
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


# Канонический порядок инструментов в S5.0 (fenced-обзор). Гейтящиеся скиллами
# имена (web_search/ssh/subagent) убираются из списка, пока их скилл
# не загружен — модель не должна видеть инструмент до активации скилла.
_TOOLS_LIST_ORDER = [
    "shell", "read_files", "write_file", "patch_file", "create_file",
    "delete_file", "rename_file", "copy_file", "move_file", "ls", "tree",
    "mkdir", "rmdir", "find_files", "grep_files", "poll",
    "ssh", "web_search", "subagent", "skill",
    "create_docx", "docx_screenshot",
    "lsp_definition", "lsp_references", "lsp_hover", "lsp_diagnostics",
    "memory_write", "memory_list", "memory_read",
]

_TOOLS_LIST_FOOTER = ""


def _short_arg_desc(text: str, limit: int = 80) -> str:
    """Сжимает описание аргумента до одной короткой строки."""
    one_line = " ".join((text or "").split())
    if len(one_line) > limit:
        one_line = one_line[: limit - 1].rstrip() + "…"
    return one_line


def _tool_signature(schema: dict) -> str:
    """Компактная сигнатура: tool(req1, req2, [opt1], [opt2]) из JSON-схемы.

    Обязательные параметры — без скобок, опциональные — в [ ]. Порядок:
    сначала required в порядке объявления, затем остальные.
    """
    fn = schema.get("function", {})
    name = fn.get("name", "")
    params = (fn.get("parameters") or {}).get("properties") or {}
    required = (fn.get("parameters") or {}).get("required") or []
    req_set = set(required)
    ordered = list(required) + [p for p in params if p not in req_set]
    parts = [p if p in req_set else f"[{p}]" for p in ordered]
    return f"{name}({', '.join(parts)})"


def _build_tools_list_block(active_skills: set | None, native_tools: bool = True) -> str:
    """S5 TOOLS — единый блок про инструменты.

    В fenced-режиме модель НЕ получает JSON-схемы через bind_tools, поэтому имена
    аргументов берутся ОТСЮДА — единый источник правды (TOOL_SCHEMAS). Каждый
    инструмент: `tool(req, [opt]) — краткое описание`. Гейтящиеся незагруженными
    скиллами инструменты исключаются.

    Чтобы всё про инструменты не было раскидано по секциям, в fenced-режиме этот
    блок собирает в ОДНУ секцию S5: синтаксис вызова через :::call (FENCED_SYNTAX_BLOCK)
    + список инструментов с сигнатурами + LSP-заметки (LSP_TOOLS_BLOCK). В native
    синтаксис не нужен (его роль выполняет function calling), а LSP-args дублируют
    схемы из bind_tools — поэтому там только список.
    """
    try:
        from skills.registry import is_tool_gated_out as _is_tool_gated_out
    except Exception:
        def _is_tool_gated_out(tool: str, active_skills: set | None) -> bool:
            return False

    from apis.tool_schemas import TOOL_SCHEMAS

    by_name = {s["function"]["name"]: s for s in TOOL_SCHEMAS}
    # Канонический порядок S5.0 + хвост из тех схем, что не перечислены в нём
    # (apply_diff, expand_tool_result, plan, think и т.п. — кроме гейтящихся).
    listed = list(_TOOLS_LIST_ORDER)
    extra = [
        n for n in by_name
        if n not in listed and n not in ("plan", "think")
    ]
    order = listed + extra

    lines = []
    for tool in order:
        if _is_tool_gated_out(tool, active_skills):
            continue
        schema = by_name.get(tool)
        if schema is None:
            continue
        sig = _tool_signature(schema)
        desc = _short_arg_desc(schema["function"].get("description", ""))
        lines.append(f"  {sig}{(' — ' + desc) if desc else ''}")

    list_section = (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "S5.1. AVAILABLE TOOLS\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Each tool with its arguments — required first, [optional] in brackets. "
        "Use EXACTLY these argument names.\n\n"
        + "\n".join(lines)
    )

    # В native всё про инструменты — только список: синтаксис вызова обеспечивает
    # function calling, а аргументы дублируются JSON-схемами через bind_tools.
    if native_tools:
        return list_section

    # В fenced весь tool-материал склеиваем в ОДНУ секцию S5: формат вызова
    # (:::call) → список инструментов с сигнатурами → LSP-заметки.
    return "\n\n".join([FENCED_SYNTAX_BLOCK, list_section, LSP_TOOLS_BLOCK])


def _assemble(
    mode: str,
    think_enabled: bool,
    native_tools: bool,
    for_subagent: bool = False,
    active_skills: set | None = None,
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

    parts.append(execution_model_block_for(native_tools))
    # S2/S2.1 — структура ответа ПОЛЬЗОВАТЕЛЮ и тон терминала. Субагент пишет не
    # юзеру, а главному агенту (свой FINAL ANSWER FORMAT в mode_block), поэтому
    # эти блоки ему лишний повторяющийся вес — пропускаем.
    if not for_subagent:
        parts.append(response_structure_block_for(native_tools))
        parts.append(TONE_AND_OUTPUT_BLOCK)
        parts.append(ORCHESTRATION_TRIGGER_BLOCK)
    parts.append(EFFICIENCY_BLOCK)
    parts.append(planning_block_for(native_tools))

    # ── Инструменты ──
    # Единый блок S5: в fenced он включает синтаксис вызова (:::call), список
    # инструментов с сигнатурами и LSP-заметки; в native — только список (схемы
    # и синтаксис даёт function calling). Гейтящиеся скиллами инструменты скрыты,
    # пока их скилл не загружен.
    parts.append(_build_tools_list_block(active_skills, native_tools))
    # Кросс-инструментальная стратегия (lsp-vs-grep, pipeline, не дублируй) —
    # всегда: её НЕТ в JSON-схемах, нужна модели в обоих режимах.
    parts.append(tool_strategy_block_for(native_tools))

    _active = active_skills or set()
    # S5.2 WEB SEARCH — только когда скилл `web` загружен (web_search/image_search
    # гейтятся им). Иначе модель не должна знать про web_search до активации.
    if "web" in _active:
        parts.append(WEB_SEARCH_BLOCK)
    parts += [
        docx_block_for(native_tools),
        hard_constraints_block_for(native_tools),
        AGENT_RULES_BLOCK,
        DELIVERABLE_DISCIPLINE_BLOCK,
        CRAFT_BLOCK,
        VERIFICATION_GATE_BLOCK,
    ]

    if mode in ("planning", "plan"):
        parts.append(PLANNING_MODE_BLOCK)
    elif mode == "agent" and AGENT_MODE_BLOCK:
        parts.append(AGENT_MODE_BLOCK)

    # THINK — часть промта только во включённом состоянии.
    if think_enabled:
        parts.append(think_block_for(native_tools))

    # S8 SUBAGENTS — как оркестрировать. Субагенту недоступно (он сам внутри
    # оркестрации); главному — только когда скилл `subagents` загружен (иначе
    # subagent скрыт и этот блок ссылается на невидимый инструмент).
    if not for_subagent and "subagents" in _active:
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
    for_subagent: bool = False,
    active_skills: set | None = None,
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

    base = _assemble(mode, think_enabled=think_enabled, native_tools=native_tools,
                     for_subagent=for_subagent, active_skills=active_skills)
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