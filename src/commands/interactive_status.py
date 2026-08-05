import os
import re
import subprocess

from wcwidth import wcswidth

import config
from models import get_context_limit
from ui import format_cost, format_tokens
from ui.formatting import (
    BAR_EMPTY_END,
    BAR_EMPTY_START,
    BAR_FILLED_END,
    BAR_FILLED_START,
    progress_bar,
)

_MARKER_RE = re.compile(
    re.escape(BAR_FILLED_START) + "|" + re.escape(BAR_FILLED_END)
    + "|" + re.escape(BAR_EMPTY_START) + "|" + re.escape(BAR_EMPTY_END)
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


def _git_section(workdir: str | None) -> str:
    """Секция «ветка +N ~N -N» (строки) или "" вне git-репо.

    Считается заново при каждой сборке статус-линии: build_status_line зовётся
    несколько раз за ход (постановка в очередь, старт, завершение), а не на
    каждый кадр рамки, поэтому git-подпроцессы здесь не дороги. Кэш на
    короткий TTL не нужен — на медленной машине быстрый ход завершается
    быстрее TTL, и секция показывала устаревшее состояние репозитория.
    """
    return _query_git(workdir)


def _numstat(workdir: str | None, extra: list[str]) -> list[tuple[int, int]]:
    """Пары (added, deleted) по файлам из `git diff --numstat`."""
    try:
        out = subprocess.run(
            ["git", "diff", "--numstat"] + extra,
            cwd=workdir, capture_output=True, text=True, timeout=2,
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
        with open(os.path.join(workdir, path), "r",
                  encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _query_git(workdir: str | None) -> str:
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain", "-b"],
            cwd=workdir, capture_output=True, text=True, timeout=2,
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
    in_tok = s.raw_input_tokens
    out_tok = s.output_tokens
    total_tok = s.context_tokens

    ctx_limit = get_context_limit(state.cur_model) or 200_000
    ctx_bar = progress_bar(total_tok, ctx_limit, width=10)
    cost_str = format_cost(s.total_cost)

    _api_id = config.get_active_api()
    if _api_id:
        # Читаем при каждой пересборке строки: списание за завершившийся запрос
        # сразу отражается в рамке поля ввода.
        from apis.config import get_provider_balance
        provider_balance = f" · {get_provider_balance(_api_id):g}$"
    else:
        provider_balance = ""

    # Секция 1 — провайдер и модель; секция 2 — usage (сообщения, токены,
    # стоимость, контекст). Вспомогательные индикаторы (think, extra) — вне.
    sec1_inner = (
        (f"🔌 {_api_id} · " if _api_id else "")
        + state.cur_model
        + provider_balance
    )
    ctx_str = f"{ctx_bar} {format_tokens(total_tok)}/{format_tokens(ctx_limit)}"

    msg_str = f"{mc}msg" if mc > 0 else ""
    io_str = f"↑{format_tokens(in_tok)} ↓{format_tokens(out_tok)}" if mc > 0 else ""
    cost_str = f"≈{cost_str}" if mc > 0 else ""

    # Порядок показа: msg, io, cost, ctx; порядок удаления: cost, io, msg.
    usage = [u for u in (msg_str, io_str, cost_str, ctx_str) if u]
    removables = [cost_str, io_str, msg_str]

    think_str = "💭" if getattr(state, "think_enabled", False) else ""
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

    # 6) минимальный fallback: модель + прогресс контекста (хвост тоже отбрасываем)
    if _visible_len(line) > budget:
        line = f"[{state.cur_model}] · [{ctx_str}]"

    return line
