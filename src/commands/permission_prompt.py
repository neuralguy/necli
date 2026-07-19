import os
import sys

from rich.console import Console
from rich.text import Text

import tools
from config.i18n import t as _t
from config.permissions import set_decision
from config.themes import t
from ui.menu import select_menu

console = Console()


def _is_headless() -> bool:
    """True если нет интерактивного TTY (CI/pipe/headless mode)."""
    if os.environ.get("NECLI_HEADLESS") == "1":
        return True
    try:
        return not sys.stdin.isatty() or not sys.stdout.isatty()
    except (ValueError, OSError):
        return True


def _hex_to_ansi_fg(h: str) -> str:
    h = h.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return ""
    return f"\x1b[38;2;{r};{g};{b}m"


_RESET = "\x1b[0m"


def _color(text: str, role: str, *, bold: bool = False) -> str:
    """Оборачивает текст в ANSI-цвет роли темы (для label в select_menu)."""
    fg = _hex_to_ansi_fg(t(role))
    if not fg:
        return text
    prefix = ("\x1b[1m" if bold else "") + fg
    return f"{prefix}{text}{_RESET}"


def _tool_display(call: tools.ToolCall) -> tuple[str, str, str]:
    """(emoji, label, color_role) для инструмента — как в основном UI."""
    try:
        from config.ui import ui
        meta = ui.tool(call.tool_name)
        emoji = (meta.get("emoji") or "").strip()
        label = (meta.get("label") or "").strip() or call.tool_name
        color_role = meta.get("color_role") or "accent"
        return emoji, label, color_role
    except Exception:
        return "", call.tool_name, "accent"


def _count_lines(value) -> int:
    if not isinstance(value, str):
        value = str(value or "")
    if not value:
        return 0
    return value.count("\n") + 1


def _clip(value, limit: int = 56) -> str:
    sv = " ".join(str(value or "").split())
    if len(sv) > limit:
        return sv[: limit - 1] + "…"
    return sv


def _smart_preview(call: tools.ToolCall) -> str:
    """Краткое, осмысленное превью того, ЧТО инструмент собирается сделать.

    shell → команда; write/create/patch → путь + объём изменений; чтение/ФС →
    путь(и); прочее → ключевые аргументы. Возвращает компактную строку.
    """
    name = call.tool_name
    args = call.args or {}

    def path_of() -> str:
        p = args.get("path")
        if isinstance(p, (list, tuple)):
            return f"{len(p)} files" if len(p) != 1 else _clip(p[0], 48)
        return _clip(p, 48) if p else ""

    if name in ("shell",):
        cmd = args.get("command") or ""
        first = str(cmd).splitlines()[0] if cmd else ""
        return f"$ {_clip(first, 60)}" if first else "(empty command)"

    if name == "create_file":
        path = path_of()
        n = _count_lines(args.get("content"))
        if "b64" in args:
            return f"{path}  (binary)"
        return f"{path}  ({n} lines)" if n else path

    if name == "patch_file":
        path = path_of()
        patches = args.get("patches")
        if isinstance(patches, list):
            return f"{path}  ({len(patches)} patch{'es' if len(patches) != 1 else ''})"
        if args.get("delete_lines"):
            return f"{path}  (delete {args['delete_lines']})"
        if "insert" in args:
            return f"{path}  (insert @ line {args.get('line', '?')})"
        return f"{path}  (find/replace)"

    if name in ("read_files", "read_file"):
        if args.get("pattern"):
            return f"{_clip(args['pattern'], 32)}  in {path_of() or '.'}"
        return path_of() or "."

    if name == "ssh":
        host = args.get("host") or args.get("alias") or ""
        cmd = args.get("command") or ""
        return f"{host}: {_clip(cmd, 44)}" if cmd else _clip(host, 48)

    if name == "web_search":
        return _clip(args.get("query") or args.get("url") or "", 60)

    # Fallback: ключевые аргументы строкой.
    parts = []
    for k, v in list(args.items())[:3]:
        if k in ("content", "b64", "insert", "replace", "find", "diff", "patches"):
            parts.append(f"{k}=…")
        else:
            parts.append(f"{k}={_clip(v, 28)}")
    return "  ".join(parts) or "(no args)"


def confirm_tool_call(call: tools.ToolCall) -> bool:
    """Спрашивает у пользователя разрешение на выполнение call.

    Возвращает True если разрешено, False если запрещено.
    Побочно: может записать decision на выбранный scope.
    В headless-режиме (нет TTY) сразу возвращает False.
    """
    emoji, label, color_role = _tool_display(call)
    preview = _smart_preview(call)

    # Telegram-режим: спрашиваем разрешение инлайн-кнопками в чате. Это работает
    # даже в headless (нет TTY) — решение приходит из TG. None (таймаут/нет
    # ответа) → отказ.
    try:
        import config as _cfg
        if _cfg.get_telegram_enabled() and _cfg.get_telegram_approve():
            import html as _html

            from apis.telegram import get_bridge
            bridge = get_bridge()
            if bridge.is_running:
                q = (
                    f"⚠ <b>{_html.escape(label)}</b>\n"
                    f"<code>{_html.escape(preview)}</code>\n\n"
                    f"{_t('perm.run_q', tool=label)}"
                )
                decision = bridge.request_approval(q)
                allowed = bool(decision)
                if not _is_headless():
                    icon = "✓" if allowed else "✗"
                    console.print(
                        f"  [{t('success') if allowed else t('error')}]"
                        f"{icon} TG: {label} {'allowed' if allowed else 'denied'}"
                        f"[/{t('success') if allowed else t('error')}]"
                    )
                return allowed
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).debug("tg approval failed, falling back", exc_info=True)

    if _is_headless():
        console.print(
            f"  [{t('error')}]"
            f"{_t('perm.headless_denied', tool=call.tool_name)}"
            f"[/{t('error')}]"
        )
        return False

    # Однострочная шапка: ⚠  <emoji> <label>  <умное превью>
    console.print()
    head = Text("  ")
    head.append("⚠ ", style=f"bold {t('warning')}")
    if emoji:
        head.append(f"{emoji} ", style=color_role)
    head.append(label, style=f"bold {color_role}")
    if preview:
        head.append("  ")
        head.append(preview, style="dim")
    console.print(head)

    # Цветные пункты: зелёные — разрешения, красные — отказы.
    items = [
        {"label": _color(_t("perm.allow_once"), "success"),    "hint": _t("perm.allow_once_hint")},
        {"label": _color(_t("perm.allow_session"), "success"), "hint": _t("perm.allow_session_hint")},
        {"label": _color(_t("perm.allow_process"), "success"), "hint": _t("perm.allow_process_hint")},
        {"label": _color(_t("perm.allow_forever"), "success"), "hint": _t("perm.allow_forever_hint")},
        {"label": _color(_t("perm.deny_once"), "error"),       "hint": ""},
        {"label": _color(_t("perm.deny_session"), "error"),    "hint": _t("perm.deny_session_hint")},
        {"label": _color(_t("perm.deny_forever"), "error"),    "hint": _t("perm.deny_forever_hint")},
    ]
    choice = select_menu(items, title=_t("perm.run_q", tool=label))

    # Стираем шапку (пустая строка + строка ⚠ <label> <preview>),
    # чтобы после решения в выводе не оставалось ничего от подтверждения.
    from ui.menu import clear_lines
    clear_lines(2)

    # Маппинг индексов на действия.
    if choice is None or choice == 4:  # esc/cancel или «deny once»
        return False
    if choice == 0:
        return True
    if choice == 1:
        set_decision(call.tool_name, "allow", "session")
        return True
    if choice == 2:
        set_decision(call.tool_name, "allow", "process")
        return True
    if choice == 3:
        set_decision(call.tool_name, "allow", "forever")
        return True
    if choice == 5:
        set_decision(call.tool_name, "deny", "session")
        return False
    if choice == 6:
        set_decision(call.tool_name, "deny", "forever")
        return False
    return False
