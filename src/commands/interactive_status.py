import os
import re
import subprocess
import threading
import time

from wcwidth import wcswidth

import config
from models import DEFAULT_CONTEXT_LIMIT, get_context_limit
from ui import format_cost, format_tokens
from ui.formatting import (
    BAR_EMPTY_END,
    BAR_EMPTY_START,
    BAR_FILLED_END,
    BAR_FILLED_START,
    progress_bar,
)

_MARKER_RE = re.compile(
    re.escape(BAR_FILLED_START)
    + "|"
    + re.escape(BAR_FILLED_END)
    + "|"
    + re.escape(BAR_EMPTY_START)
    + "|"
    + re.escape(BAR_EMPTY_END)
)


def _visible_len(s: str) -> int:
    clean = _MARKER_RE.sub("", s)
    n = wcswidth(clean)
    return n if n >= 0 else len(clean)


def _term_width() -> int:
    try:
        return os.get_terminal_size().columns
    except Exception:
        return 80


# Git-статус — декоративная часть UI, поэтому он не имеет права блокировать
# основной asyncio loop. Executor вызывает refresh_status после каждого tool,
# а _query_git делает несколько subprocess.run с timeout=2s. При серии native
# tool calls это раньше ставило в loop очередь синхронных git-сканов и могло
# задерживать следующий API request на десятки секунд/минуты.
_GIT_CACHE_TTL_SEC = 2.0
_GIT_CACHE_MAX_ENTRIES = 64
_GIT_CACHE: dict[str, tuple[float, str]] = {}
_GIT_REFRESHING: set[str] = set()
_GIT_CACHE_LOCK = threading.Lock()


def _git_cache_key(workdir: str | None) -> str:
    return os.path.abspath(workdir or os.getcwd())


def _refresh_git_cache(key: str, workdir: str | None) -> None:
    try:
        value = _query_git(workdir)
        with _GIT_CACHE_LOCK:
            _GIT_CACHE[key] = (time.monotonic(), value)
            while len(_GIT_CACHE) > _GIT_CACHE_MAX_ENTRIES:
                oldest = min(_GIT_CACHE, key=lambda k: _GIT_CACHE[k][0])
                if oldest == key and len(_GIT_CACHE) > 1:
                    oldest = min(
                        (k for k in _GIT_CACHE if k != key),
                        key=lambda k: _GIT_CACHE[k][0],
                    )
                _GIT_CACHE.pop(oldest, None)
    finally:
        with _GIT_CACHE_LOCK:
            _GIT_REFRESHING.discard(key)


def _git_section(workdir: str | None) -> str:
    """Вернуть cached Git-секцию и при необходимости обновить её в фоне.

    Никогда не запускает git синхронно из build_status_line: статус строится в
    event loop, поэтому даже один медленный subprocess замораживал SSE, UI и
    переход к следующему запросу. При протухшем кэше сразу возвращаем последнее
    значение и запускаем максимум один daemon-refresh на workdir.
    """
    key = _git_cache_key(workdir)
    now = time.monotonic()
    with _GIT_CACHE_LOCK:
        cached = _GIT_CACHE.get(key)
        if cached is not None and (now - cached[0]) < _GIT_CACHE_TTL_SEC:
            return cached[1]
        stale = cached[1] if cached is not None else ""
        if key in _GIT_REFRESHING:
            return stale
        _GIT_REFRESHING.add(key)

    threading.Thread(
        target=_refresh_git_cache,
        args=(key, key),
        name="necli-git-status",
        daemon=True,
    ).start()
    return stale


def _numstat(workdir: str | None, extra: list[str]) -> list[tuple[int, int]]:
    """Пары (added, deleted) по файлам из `git diff --numstat`."""
    try:
        out = subprocess.run(
            ["git", "diff", "--numstat", *extra],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    if out.returncode != 0:
        return []
    pairs: list[tuple[int, int]] = []
    for ln in out.stdout.splitlines():
        parts = ln.split("\t")
        if len(parts) < 3:
            continue
        try:
            pairs.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return pairs


def _count_file_lines(workdir: str, path: str) -> int:
    try:
        with open(os.path.join(workdir, path), encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _query_git(workdir: str | None) -> str:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-b"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return ""
    if out.returncode != 0 or not out.stdout:
        return ""
    lines = out.stdout.splitlines()
    head = lines[0]
    if not head.startswith("## "):
        return ""
    branch = head[3:].split("...")[0]

    # Счёт по строкам, а не по файлам: + новые, ~ изменённые (заменённые),
    # - удалённые. Изменённая строка = строка, ушедшая и добавленная парой
    # (min(added, deleted) по файлу); остаток added/deleted — чистые + и -.
    pairs = _numstat(workdir, []) + _numstat(workdir, ["--cached"])
    added = sum(a for a, _ in pairs) - sum(min(a, d) for a, d in pairs)
    deleted = sum(d for _, d in pairs) - sum(min(a, d) for a, d in pairs)
    modified = sum(min(a, d) for a, d in pairs)

    # Untracked файлы в numstat не попадают — считаем их строки сами.
    untracked = 0
    for ln in lines[1:]:
        if ln.startswith("?? ") and workdir:
            untracked += _count_file_lines(workdir, ln[3:])
    added += untracked

    bits = []
    if added:
        bits.append(f"+{added}")
    if modified:
        bits.append(f"~{modified}")
    if deleted:
        bits.append(f"-{deleted}")
    return branch + (" " + " ".join(bits) if bits else "")


def build_status_line(state, extra: str = "") -> str:
    """Собирает статус для верхней линии рамки.

    `extra` — хвост, который знает только вызывающий цикл (режим агента и
    состояние очереди ходов). Он участвует в бюджете ширины: строка живёт
    внутри линии рамки, и вылезший хвост порвал бы её переносом.
    """
    s = state.session
    mc = s.message_count
    # ↑ — фактический суммарный input API-запросов. В отличие от
    # raw_input_tokens, provider usage включает system prompt и native
    # tool schemas (в том числе MCP). Полоса контекста ниже показывает
    # текущую историю + системный промт (session.system_prompt_tokens),
    # а не накопленный billing input.
    cache_tok = int(getattr(s, "cache_read_tokens", 0) or 0)
    in_tok = s.input_tokens
    out_tok = s.output_tokens
    total_tok = s.context_tokens

    ctx_limit = get_context_limit(state.cur_model) or DEFAULT_CONTEXT_LIMIT
    ctx_bar = progress_bar(total_tok, ctx_limit, width=10)
    cost_str = format_cost(s.total_cost)

    _api_id = config.get_active_api()
    if _api_id:
        # Читаем при каждой пересборке строки: списание за завершившийся запрос
        # сразу отражается в рамке поля ввода.
        from apis.config import get_provider_balance, get_router_balance
        from apis.registry import get_definition

        definition = get_definition(_api_id)
        if definition is not None and definition.type == "chatgpt":
            from apis.chatgpt_auth import chatgpt_auth_status
            from apis.chatgpt_usage import get_cached_chatgpt_usage
            from config.i18n import t as _

            usage = get_cached_chatgpt_usage()
            authenticated = bool(chatgpt_auth_status().get("authenticated"))
            if authenticated and usage:
                provider_balance = " · " + _(
                    "api.chatgpt_weekly_remaining",
                    percent=f"{float(usage['remaining_percent']):g}",
                )
            elif authenticated:
                provider_balance = " · " + _("api.chatgpt_weekly_unavailable")
            else:
                provider_balance = ""
        else:
            balance = (
                get_router_balance(config.get_active_api_model())
                if _api_id == "routers"
                else get_provider_balance(_api_id)
            )
            provider_balance = f" · {balance:g}$"
    else:
        provider_balance = ""

    # Секция 1 — провайдер и модель; секция 2 — usage (сообщения, токены,
    # стоимость, контекст). Вспомогательные индикаторы (think, extra) — вне.
    sec1_inner = (
        (f"⌁ {_api_id} · " if _api_id else "") + state.cur_model + provider_balance
    )
    ctx_str = f"{ctx_bar} {format_tokens(total_tok)}/{format_tokens(ctx_limit)}"

    msg_str = f"{mc}msg" if mc > 0 else ""
    io_str = (
        f"↑{format_tokens(cache_tok)}/{format_tokens(in_tok)} ↓{format_tokens(out_tok)}"
        if mc > 0
        else ""
    )
    cost_str = f"≈{cost_str}" if mc > 0 else ""

    # Порядок показа: msg, io, cost, ctx; порядок удаления: cost, io, msg.
    usage = [u for u in (msg_str, io_str, cost_str, ctx_str) if u]
    removables = [cost_str, io_str, msg_str]

    think_str = "⋯" if getattr(state, "think_enabled", False) else ""
    extra_str = extra or ""

    # Бюджет: ширина терминала минус префикс "─── ", суффикс " " + хвост ─
    # (минимум 3 символа на хвост, чтобы не выглядело обрезанным)
    budget = max(0, _term_width() - len("─── ") - len(" ") - 3)

    def render(cur_usage: list[str], think: str, extra_s: str, git_s: str) -> str:
        parts = [f"[{sec1_inner}]", "[" + " · ".join(cur_usage) + "]"]
        if git_s:
            parts.append(f"[{git_s}]")
        if think:
            parts.append(think)
        if extra_s:
            parts.append(extra_s)
        return " · ".join(parts)

    cur_usage = list(usage)
    think = think_str
    git_s = _git_section(getattr(state, "workdir", None))
    line = render(cur_usage, think, extra_str, git_s)

    # Поэтапно сокращаем по приоритету (наименее важное → наиболее важное)
    # 1) убрать think-индикатор
    if _visible_len(line) > budget and think:
        think = ""
        line = render(cur_usage, think, extra_str, git_s)

    # 2) стоимость → 3) I/O → 4) счётчик сообщений
    for r in removables:
        if _visible_len(line) <= budget:
            break
        if r in cur_usage:
            cur_usage.remove(r)
            line = render(cur_usage, think, extra_str, git_s)

    # 5) git-секцию сносим последней из значащих
    if _visible_len(line) > budget and git_s:
        git_s = ""
        line = render(cur_usage, think, extra_str, git_s)

    # 6) минимальный fallback: модель с лимитом/балансом + прогресс контекста.
    if _visible_len(line) > budget:
        line = f"[{state.cur_model}{provider_balance}] · [{ctx_str}]"

    return line
