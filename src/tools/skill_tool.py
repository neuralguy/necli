"""Инструмент skill — загрузка скилла по имени."""

from config.i18n import t as _i18n
from skills import list_skills, load_skill
from tools.models import ToolCall, ToolResult


def execute_skill(call: ToolCall) -> ToolResult:
    name = (call.args or {}).get("name", "").strip()
    if not name:
        name = call.command.strip()
    if not name:
        return ToolResult(
            name="skill",
            status="error",
            command="skill",
            output=_i18n("skill.name_required"),
            exit_code=1,
        )
    skill = load_skill(name)
    if skill is None:
        available = [s.name for s in list_skills()]
        hint = (
            _i18n("skill.available", names=", ".join(available))
            if available else _i18n("skill.none_installed")
        )
        return ToolResult(
            name="skill",
            status="error",
            command=f"skill {name}",
            output=_i18n("skill.not_found", name=name, hint=hint),
            exit_code=1,
        )
    header = f"{_i18n('skill.base_path')}: {skill.path}/\n\n"
    body = skill.body
    if name == "ssh":
        body += _render_ssh_hosts()
    elif name == "subagents":
        body += _render_subagent_info()
    return ToolResult(
        name="skill",
        status="ok",
        command=f"skill {name}",
        output=header + body,
        exit_code=0,
    )


def _render_subagent_info() -> str:
    """Живой список моделей и пресетов субагентов — подставляется в скилл."""
    try:
        from system_prompt import _build_agent_presets_block, _build_subagent_models_block
    except Exception:
        return ""
    out = ""
    try:
        out += _build_subagent_models_block()
    except Exception:
        pass
    try:
        out += _build_agent_presets_block()
    except Exception:
        pass
    return out


def _render_ssh_hosts() -> str:
    """Живой список настроенных SSH-хостов — подставляется в скилл при загрузке."""
    try:
        from config.ssh import list_hosts

        hosts = list_hosts()
    except Exception:
        return "\n\n## Доступные хосты\n\n(не удалось прочитать список)"
    if not hosts:
        return (
            "\n\n## Доступные хосты\n\n"
            "Ни одного хоста не настроено. Попроси пользователя добавить сервер через /ssh."
        )
    lines = ["\n\n## Доступные хосты\n", "alias  →  user@host:port"]
    for alias, info in hosts.items():
        user = info.get("user", "root")
        host = info.get("host", "")
        port = info.get("port", 22)
        lines.append(f"  - {alias}  →  {user}@{host}:{port}")
    return "\n".join(lines)
