import os
import re

from wcwidth import wcswidth

import config
from config.ssh import get_host
from tools.ssh import get_active_connections
from models import get_context_limit
from ui import format_tokens, format_cost
from ui.formatting import (
    progress_bar,
    BAR_FILLED_START, BAR_FILLED_END,
    BAR_EMPTY_START, BAR_EMPTY_END,
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


def build_status_line(state) -> str:
    s = state.session
    mc = s.message_count
    in_tok = s.raw_input_tokens          # usage провайдера (может быть кривым)
    est_tok = s.estimated_input_tokens  # наша независимая оценка
    out_tok = s.output_tokens
    total_tok = s.context_tokens

    ctx_limit = get_context_limit(state.cur_model) or 200_000
    ctx_bar = progress_bar(total_tok, ctx_limit, width=10)
    cost_str = format_cost(s.total_cost)

    ssh_status = ""
    try:
        active_ssh = get_active_connections()
        if active_ssh:
            ssh_parts = []
            for a in active_ssh[:3]:
                h = get_host(a)
                if h:
                    ssh_parts.append(f"{a}({h['host']})")
                else:
                    ssh_parts.append(a)
            ssh_status = "🔗 " + ",".join(ssh_parts) + " · "
    except Exception:
        pass

    _api_id = config.get_active_api()
    api_part = f"🔌 {_api_id} · " if _api_id else ""
    think_part = "💭 · " if getattr(state, "think_enabled", False) else ""

    ctx_full = f"{ctx_bar} {format_tokens(total_tok)}/{format_tokens(ctx_limit)}"
    model_part = f"{state.cur_model} · "

    if mc > 0:
        msg_part = f"{mc}msg · "
        # Если usage провайдера заметно расходится с нашей оценкой (>1.5×) —
        # показываем оба: ↑<оценка>(prov~<провайдер>). Иначе одну цифру.
        if est_tok > 0 and in_tok > est_tok * 1.5:
            up_part = f"↑{format_tokens(est_tok)}(prov~{format_tokens(in_tok)})"
        else:
            up_part = f"↑{format_tokens(in_tok)}"
        io_part = f"{up_part} ↓{format_tokens(out_tok)} · "
        cost_part = f"≈{cost_str} · "
    else:
        msg_part = io_part = cost_part = ""

    # Бюджет: ширина терминала минус префикс "─── ", суффикс " " + хвост ─
    # (минимум 3 символа на хвост, чтобы не выглядело обрезанным)
    budget = max(0, _term_width() - len("─── ") - len(" ") - 3)

    parts = [api_part, think_part, msg_part, io_part, cost_part, ssh_status, model_part, ctx_full]
    line = "".join(parts)

    if _visible_len(line) <= budget:
        return line

    # Индексы parts: 0=api, 1=think, 2=msg, 3=io, 4=cost, 5=ssh, 6=model, 7=ctx
    # Поэтапно сокращаем по приоритету (наименее важное → наиболее важное)
    # 1) убрать api-индикатор
    if _visible_len(line) > budget and api_part:
        parts[0] = ""
        line = "".join(parts)

    # 2) убрать ssh
    if _visible_len(line) > budget and ssh_status:
        parts[5] = ""
        line = "".join(parts)

    # 3) убрать стоимость
    if _visible_len(line) > budget and cost_part:
        parts[4] = ""
        line = "".join(parts)

    # 4) убрать I/O
    if _visible_len(line) > budget and io_part:
        parts[3] = ""
        line = "".join(parts)

    # 5) убрать счётчик сообщений
    if _visible_len(line) > budget and msg_part:
        parts[2] = ""
        line = "".join(parts)

    # 6) минимальный fallback: модель + прогресс контекста
    if _visible_len(line) > budget:
        line = f"{state.cur_model} · {ctx_full}"

    return line