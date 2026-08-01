"""Запрос разрешения на опасный вызов инструмента.

Спрашивается ПОСРЕДИ хода агента, поэтому виджет обязан жить в нижней зоне
Application, а шапка «иконка Инструмент (цель)» — внутри самого
виджета: печатать её в scrollback нельзя, иначе после решения в истории
останется мусор, который раньше приходилось затирать `clear_lines(2)`.

Диалог намеренно компактен: цель видна в шапке, ниже четыре уровня
разрешения и один разовый отказ. Долгосрочные deny-правила настраиваются в
`/permissions`, а не в prompt посреди хода.

Рамок нет ни одной: группы разделены пустой строкой, выбранная строка залита
фоном `bg_select`. У опасных вызовов (rm -rf, запись вне рабочего каталога, …)
курсор сразу стоит на «Запретить»,
чтобы рефлекторный Enter ничего не выполнил.
"""

import os
import re
import sys

import tools
from config.i18n import t as _t
from config.permissions import set_decision
from config.themes import t
from ui.overlays import (
    DIM,
    RESET,
    cell_width,
    clip,
    more_note,
    paint,
    row,
    scroll_window,
    spacer,
)
from ui.shell import Overlay, get_shell, print_static

_DIM = DIM
_BOLD = "\x1b[1m"
_RESET = RESET

#: Шкала «докуда дотянется решение». Порядок пунктов меню менять нельзя —
#: на индексы завязан маппинг решений ниже.
_REACH_STEPS = 4


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
    Однострочная форма нужна и Telegram-ветке, где верстать нечем.
    """
    name = call.tool_name
    args = call.args or {}

    def path_of() -> str:
        p = args.get("path")
        if isinstance(p, (list, tuple)):
            if len(p) != 1:
                return f"{len(p)} files"
            p = p[0]
        if not p:
            return ""
        raw = os.path.expanduser(str(p))
        full = raw if os.path.isabs(raw) else os.path.abspath(raw)
        return _clip(full, 60)

    if name in ("shell",):
        cmd = args.get("command") or ""
        first = str(cmd).splitlines()[0] if cmd else ""
        return f"$ {_clip(first, 60)}" if first else "empty command"

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

    if name in ("read", "grep"):
        if args.get("pattern"):
            return f"{_clip(args['pattern'], 32)}  in {path_of() or '.'}"
        return path_of() or "."

    if name == "web_search":
        qs = args.get("queries", [])
        if isinstance(qs, str):
            qs = [qs]
        return _clip("; ".join(str(q) for q in qs[:3]) if qs else "", 60)
    if name == "web_fetch":
        urls = args.get("urls", [])
        if isinstance(urls, str):
            urls = [urls]
        return _clip(", ".join(str(u) for u in urls[:2]) if urls else "", 60)

    # Fallback: ключевые аргументы строкой.
    parts = []
    for k, v in list(args.items())[:3]:
        if k in ("content", "b64", "insert", "replace", "find", "diff", "patches"):
            parts.append(f"{k}=…")
        else:
            parts.append(f"{k}={_clip(v, 28)}")
    return "  ".join(parts) or "no args"


def _menu_title(emoji: str, label: str, color_role: str, preview: str) -> str:
    """Компактная шапка для запасного пути без Application.

    Живой виджет верстает шапку сам (`PermissionOverlay`), но при headless-старте
    и при вызове из самого loop'а рисует старое синхронное меню — ему нужен
    заголовок одной ANSI-строкой.
    """
    head = ""
    if emoji:
        head += f"{_hex_to_ansi_fg(t(color_role))}{emoji} {_RESET}"
    head += f"{_BOLD}{_hex_to_ansi_fg(t(color_role))}{label}{_RESET}"
    if preview:
        head += f" {_DIM}({preview}){_RESET}"
    return head


# ─────────────────────────── оценка опасности ───────────────────────────────
#: Команды, после которых «отменить» уже нельзя. Список намеренно грубый: цена
#: ложной тревоги — курсор на «запретить», цена пропуска — стёртый диск.
_DANGER_CMD = re.compile(
    r"""(?xi)
    \brm\s+(-\w*\s+)*-\w*[rf] | \bshred\b | \bmkfs | \bdd\s+if= | \bfdisk\b
    | :\(\)\s*\{ | \bchmod\s+-?R?\s*777 | \bchown\s+-R\b
    | >\s*/dev/(sd|nvme|disk) | \bkill\s+-9\b | \bpkill\b | \bkillall\b
    | \bsudo\b | \bsu\s+-\b
    | \bgit\s+(push\s+.*--force|reset\s+--hard|clean\s+-\w*[fd])
    | \b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|)sh
    | \bshutdown\b | \breboot\b | \bsystemctl\s+(stop|disable|mask)\b
    | \bdocker\s+(rm|rmi|system\s+prune)\b | \bnpm\s+publish\b
    | \bDROP\s+(TABLE|DATABASE)\b | \bTRUNCATE\s+TABLE\b
    | \bhistory\s+-c\b | \bcrontab\s+-r\b
    """
)

#: Каталоги, запись в которые ломает не проект, а машину.
_SYSTEM_DIRS = ("/etc", "/usr", "/bin", "/sbin", "/boot", "/lib", "/var/lib",
                "/System", "/Library", "/Windows")


def _short_path(path: str, limit: int = 42) -> str:
    """Путь в человеческом виде: домашний каталог — тильдой, длинный — хвостом."""
    p = str(path or "")
    home = os.path.expanduser("~")
    if home and p.startswith(home):
        p = "~" + p[len(home):]
    if len(p) > limit:
        p = "…" + p[-(limit - 1):]
    return p


def _display_path(path: str, limit: int = 58) -> str:
    """Путь так, как его читает человек: внутри проекта — от корня проекта.

    Абсолютный путь оставляем ровно тогда, когда он ведёт НАРУЖУ рабочего
    каталога: в этом случае «куда именно» и есть главная новость.
    """
    p = str(path or "")
    try:
        full = os.path.realpath(os.path.expanduser(p))
        cwd = os.path.realpath(os.getcwd())
        if full.startswith(cwd + os.sep):
            return "./" + full[len(cwd) + 1:]
    except OSError:
        pass
    return _short_path(p, limit)


def _arg_paths(args: dict) -> list[str]:
    p = args.get("path")
    if isinstance(p, (list, tuple)):
        return [str(x) for x in p]
    return [str(p)] if p else []


def _is_destructive(call: tools.ToolCall) -> bool:
    """Нужно ли ставить курсор на «запретить» и красить шапку красным."""
    name = call.tool_name
    args = call.args or {}
    if name == "shell":
        cmd = str(args.get("command") or call.command or "")
        return bool(_DANGER_CMD.search(cmd))
    if name in ("create_file", "patch_file", "docx_writer", "create_docx"):
        cwd = os.path.realpath(os.getcwd())
        for raw in _arg_paths(args):
            full = os.path.realpath(os.path.expanduser(raw))
            if any(full == d or full.startswith(d + os.sep) for d in _SYSTEM_DIRS):
                return True
            # Запись мимо рабочего каталога — почти всегда не то, чего ждали.
            if not full.startswith(cwd + os.sep) and full != cwd:
                return True
            if os.sep + ".ssh" + os.sep in full or full.endswith("/.env"):
                return True
    return False


# ───────────────────────────── превью вызова ────────────────────────────────
def _pv(text: str, width: int, role: str = "", *, dim: bool = False,
        prefix: str = "", prefix_role: str = "") -> str:
    """Строка превью: отступ, приглушённый префикс, содержимое, обрезка по ширине."""
    head = "    " + (paint(prefix, prefix_role, dim=not prefix_role) if prefix else "")
    body = clip(" ".join(str(text).split()), max(8, width - 5 - cell_width(prefix)))
    return head + paint(body, role, dim=dim)


# ────────────────────────── семь пунктов решения ────────────────────────────
def _options() -> list[dict]:
    """Пять пунктов. ПОРЯДОК ФИКСИРОВАН: на индексы завязан маппинг решений."""
    return [
        {"label": _t("perm.allow_once"), "hint": _t("perm.allow_once_hint"),
         "role": "success", "reach": 1},
        {"label": _t("perm.allow_session"), "hint": _t("perm.allow_session_hint"),
         "role": "success", "reach": 2},
        {"label": _t("perm.allow_process"), "hint": _t("perm.allow_process_hint"),
         "role": "success", "reach": 3},
        {"label": _t("perm.allow_forever"), "hint": _t("perm.allow_forever_hint"),
         "role": "success", "reach": 4},
        {"label": _t("perm.deny_once"), "hint": "",
         "role": "error", "reach": 0},
    ]


#: Индекс, после которого «разрешить» сменяется на «запретить» — там пустая строка.
_GROUP_BREAK = 4


def _reach_meter(level: int, role: str) -> str:
    """Шкала последствий: раз ● ○ ○ ○ … навсегда ● ● ● ●.

    Заполненная часть на «навсегда» красится в warning независимо от того,
    разрешение это или отказ: именно эти два пункта пишут решение в config.json
    и переживут перезапуск.
    """
    filled_role = "warning" if level >= _REACH_STEPS else role
    return (paint("●" * level, filled_role)
            + paint("○" * (_REACH_STEPS - level), "muted"))


class PermissionOverlay(Overlay):
    """Виджет разрешения: компактная шапка + пять пунктов.

    Возвращает индекс пункта либо None (esc) — ровно то, что раньше отдавал
    `select_menu`, поэтому маппинг решений в `_ask_menu` не тронут.
    """

    top_margin_rows = 1

    def __init__(self, call: tools.ToolCall, emoji: str, label: str,
                 color_role: str) -> None:
        super().__init__()
        self.call = call
        self.emoji = emoji
        self.label = label
        self.color_role = color_role
        self.danger = _is_destructive(call)
        self.options = _options()
        # Опасный вызов встречает пользователя курсором на отказе: человека
        # перебили посреди чтения ответа, и первый Enter не должен ничего снести.
        self.selected = _GROUP_BREAK if self.danger else 0
        self._label_w = min(34, max(cell_width(o["label"]) for o in self.options))

    # ── шапка ──
    def _head(self, width: int) -> str:
        left = ""
        if self.emoji:
            left += paint(self.emoji, self.color_role) + " "
        left += paint(self.label, self.color_role, bold=True)
        preview = _smart_preview(self.call)
        if preview:
            left += f" {DIM}({preview}){RESET}"
        # Обрезаем сами: у окна оверлея wrap_lines=False, и лишний хвост на
        # узком терминале просто съел бы правый край рамки.
        return clip(left, max(10, width - 1))

    # ── список пунктов ──
    def _option_rows(self, width: int, budget: int) -> list[str]:
        n = len(self.options)
        gap = budget >= n + 1          # пустая строка между «разрешить»/«запретить»
        if gap:
            start, end, above, below = 0, n, 0, 0
        else:
            start, end, above, below = scroll_window(n, self.selected, budget)
        out: list[str] = []
        if above:
            out.append(more_note(above, up=True))
        for i in range(start, end):
            if gap and i == _GROUP_BREAK:
                out.append(spacer())
            opt = self.options[i]
            reach = int(opt["reach"])
            out.append(row(
                opt["label"], opt["hint"],
                selected=(i == self.selected), width=width, role=opt["role"],
                mark=str(i + 1), badge=_reach_meter(reach, opt["role"]) if reach else "",
                label_width=self._label_w,
            ))
        if below:
            out.append(more_note(below, up=False))
        return out

    def render(self, width: int) -> str:
        try:
            budget = self.shell.overlay_budget() if self.shell else 16
        except Exception:
            budget = 16
        lines = [self._head(width), spacer()]
        lines.extend(self._option_rows(width, max(1, budget - 2)))
        return "\n".join(lines)

    def hint(self) -> str:
        return ""

    def handle_key(self, key: str, event) -> bool:
        total = len(self.options)
        if key in ("up", "k"):
            self.selected = (self.selected - 1) % total
        elif key in ("down", "j"):
            self.selected = (self.selected + 1) % total
        elif key == "home":
            self.selected = 0
        elif key == "end":
            self.selected = total - 1
        elif key == "enter":
            self.finish(self.selected)
        elif key in ("escape", "c-c"):
            self.finish(None)
        elif len(key) == 1 and key.isdigit() and 1 <= int(key) <= total:
            # Цифра только ПЕРЕНОСИТ курсор: это гейт разрешений, и случайная
            # клавиша не должна одним нажатием выполнить опасный вызов.
            self.selected = int(key) - 1
        return True


def _ask_telegram(call: tools.ToolCall, label: str, preview: str) -> bool | None:
    """Telegram-режим: разрешение инлайн-кнопками в чате.

    Работает даже в headless (нет TTY) — решение приходит из TG. None означает
    «этот путь не применим» (TG выключен, бридж не поднят, сбой); таймаут и
    отсутствие ответа сам bridge отдаёт как отказ.

    Вызов блокирующий (до 5 минут), поэтому держим его СИНХРОННЫМ и никогда не
    зовём прямо из loop'а: там он заморозил бы весь UI.
    """
    try:
        import config as _cfg
        if not (_cfg.get_telegram_enabled() and _cfg.get_telegram_approve()):
            return None
        import html as _html

        from apis.telegram import get_bridge
        bridge = get_bridge()
        if not bridge.is_running:
            return None
        q = (
            f"⚠ <b>{_html.escape(label)}</b>\n"
            f"<code>{_html.escape(preview)}</code>\n\n"
            f"{_t('perm.run_q', tool=label)}"
        )
        allowed = bool(bridge.request_approval(q))
    except Exception:
        import logging as _lg
        _lg.getLogger(__name__).debug("tg approval failed, falling back", exc_info=True)
        return None
    if not _is_headless():
        icon = "✓" if allowed else "✗"
        print_static(
            f"  [{t('success') if allowed else t('error')}]"
            f"{icon} TG: {label} {'allowed' if allowed else 'denied'}"
            f"[/{t('success') if allowed else t('error')}]"
        )
    return allowed


async def _ask_choice(call: tools.ToolCall, emoji: str, label: str,
                      color_role: str, preview: str) -> int | None:
    """Показать виджет и вернуть индекс пункта (или None)."""
    shell = get_shell()
    if shell is None:
        # Application не поднят (ранний старт) или снят на время вызова из
        # самого loop'а — рисуем прежним синхронным меню, оно ничего не ждёт.
        from ui import overlays
        items = [{"label": _color(o["label"], o["role"]), "hint": o["hint"]}
                 for o in _options()]
        return await overlays.select_menu(
            items,
            current=_GROUP_BREAK if _is_destructive(call) else 0,
            title=_menu_title(emoji, label, color_role, preview),
        )
    return await shell.run_overlay(PermissionOverlay(call, emoji, label, color_role))


async def _ask_menu(call: tools.ToolCall, emoji: str, label: str,
                    color_role: str, preview: str) -> bool:
    """Пять пунктов и маппинг выбора на решение."""
    choice = await _ask_choice(call, emoji, label, color_role, preview)

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
    return False


def _deny_headless(call: tools.ToolCall) -> bool:
    print_static(
        f"  [{t('error')}]"
        f"{_t('perm.headless_denied', tool=call.tool_name)}"
        f"[/{t('error')}]"
    )
    return False


async def confirm_tool_call(call: tools.ToolCall) -> bool:
    """Спрашивает у пользователя разрешение на выполнение call.

    Возвращает True если разрешено, False если запрещено.
    Побочно: может записать decision на выбранный scope.
    В headless-режиме (нет TTY) сразу возвращает False.
    """
    emoji, label, color_role = _tool_display(call)
    preview = _smart_preview(call)

    import asyncio as _aio
    # to_thread: ждать ответа в чате до 5 минут прямо на loop'е нельзя —
    # замёрзли бы и ввод, и отрисовка ответа агента.
    decision = await _aio.to_thread(_ask_telegram, call, label, preview)
    if decision is not None:
        return decision

    if _is_headless():
        return _deny_headless(call)
    return await _ask_menu(call, emoji, label, color_role, preview)


def confirm_tool_call_sync(call: tools.ToolCall) -> bool:
    """Синхронная обёртка для `executor._execute_single`.

    Executor остался синхронным и зовётся из двух контекстов: рабочего потока
    (native tool calls) и самого loop'а (fenced-блоки исполняются в колбэке
    стрима). Мост в `ui/menu.py` различает их сам.

    Telegram и headless разруливаем ДО моста и синхронно: они и так блокирующие,
    и в них нет ни одного await — иначе во втором контексте (внутри loop'а)
    ожидание ответа из чата было бы некому обслужить.

    Когда `_execute_single` станет корутиной, это место схлопнется в
    `await confirm_tool_call(call)`.
    """
    emoji, label, color_role = _tool_display(call)
    preview = _smart_preview(call)

    decision = _ask_telegram(call, label, preview)
    if decision is not None:
        return decision
    if _is_headless():
        return _deny_headless(call)

    from ui.menu import run_ui_sync
    try:
        return bool(run_ui_sync(_ask_menu(call, emoji, label, color_role, preview)))
    except Exception:
        # Это гейт разрешений: сломанный виджет должен запрещать, а не валить ход
        # агента и не пропускать вызов без спроса.
        import logging as _lg
        _lg.getLogger(__name__).warning("permission menu failed → deny", exc_info=True)
        return False
