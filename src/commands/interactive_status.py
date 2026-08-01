import os
import re

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
    api_part = f"🔌 {_api_id} · " if _api_id else ""

    think_part = "💭 · " if getattr(state, "think_enabled", False) else ""

    ctx_full = f"{ctx_bar} {format_tokens(total_tok)}/{format_tokens(ctx_limit)}"
    model_part = f"{state.cur_model} · "

    if mc > 0:
        msg_part = f"{mc}msg · "
        up_part = f"↑{format_tokens(in_tok)}"
        io_part = f"{up_part} ↓{format_tokens(out_tok)} · "
        cost_part = f"≈{cost_str} · "
    else:
        msg_part = io_part = cost_part = ""

    # Бюджет: ширина терминала минус префикс "─── ", суффикс " " + хвост ─
    # (минимум 3 символа на хвост, чтобы не выглядело обрезанным)
    budget = max(0, _term_width() - len("─── ") - len(" ") - 3)

    extra_part = f" · {extra}" if extra else ""

    # Секция 1 — провайдер и модель; секция 2 — сообщения, токены, стоимость;
    # дальше контекст и вспомогательные индикаторы.
    parts = [api_part, model_part, msg_part, io_part, cost_part, ctx_full,
             think_part, extra_part]
    line = "".join(parts)

    if _visible_len(line) <= budget:
        return line

    # Индексы parts: 0=api, 1=model, 2=msg, 3=io, 4=cost, 5=ctx, 6=think, 7=extra
    # Поэтапно сокращаем по приоритету (наименее важное → наиболее важное)
    # 1) убрать think-индикатор
    if _visible_len(line) > budget and think_part:
        parts[6] = ""
        line = "".join(parts)

    # 2) убрать стоимость
    if _visible_len(line) > budget and cost_part:
        parts[4] = ""
        line = "".join(parts)

    # 3) убрать I/O
    if _visible_len(line) > budget and io_part:
        parts[3] = ""
        line = "".join(parts)

    # 4) убрать счётчик сообщений
    if _visible_len(line) > budget and msg_part:
        parts[2] = ""
        line = "".join(parts)

    # 5) минимальный fallback: модель + прогресс контекста (хвост тоже отбрасываем)
    if _visible_len(line) > budget:
        line = f"{state.cur_model} · {ctx_full}"

    return line
