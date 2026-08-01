"""Меню /params: temperature, max_tokens, reasoning effort.

Текущие значения показываются в шапке виджета и в колонке значений, а не
печатаются в scrollback перед каждым показом меню. Факт изменения живёт в
динамическом notice.
"""

import config
from apis.agent_adapter import invalidate_api_llm
from commands.menus._style import card_menu, facts_line
from config import t as _
from ui import overlays

# Слова, которыми пользователь просит «не передавать temperature вообще».
_TEMP_OFF = ("off", "-", "none", "x")


def _fmt_max_tokens(v: int) -> str:
    return f"{v}" if v > 0 else _("params.default_provider")


def _temp_disabled(v) -> bool:
    """True, если temperature не нужно передавать в запросе (off / нечисло)."""
    return isinstance(v, bool) or not isinstance(v, (int, float))


def _fmt_temp(v) -> str:
    if _temp_disabled(v):
        return _("params.default_provider")
    return f"{float(v):.2f}"


def _fmt_reasoning_effort(v: str) -> str:
    return {
        "low": _("params.effort_low"),
        "medium": _("params.effort_medium"),
        "high": _("params.effort_high"),
        "xhigh": _("params.effort_xhigh"),
        "max": _("params.effort_max"),
    }.get(v, _("params.default_provider"))


def _validate_temp(raw: str) -> str | None:
    """Проверка живёт в оверлее, а не после него: пользователь видит ошибку
    прямо в поле и правит значение, вместо того чтобы меню закрылось."""
    if not raw or raw.lower() in _TEMP_OFF:
        return None
    try:
        val = float(raw)
    except ValueError:
        return _("params.invalid_number")
    if val < 0 or val > 2:
        return _("params.out_of_range_temp")
    return None


def _validate_max_tokens(raw: str) -> str | None:
    if not raw:
        return None
    try:
        val = int(raw)
    except ValueError:
        return _("params.invalid_int")
    if val < 0 or val > 200000:
        return _("params.out_of_range_max")
    return None


async def params_interactive() -> None:
    while True:
        temp = config.get("temperature", 0.7)
        max_tok = int(config.get("max_tokens", 0) or 0)
        effort = str(config.get("reasoning_effort", "") or "")

        items = [
            {"label": _("params.temperature"), "hint": _("params.temp_hint"),
             "badge": _fmt_temp(temp), "badge_style": "warning"},
            {"label": _("params.max_tokens"), "hint": _("params.max_tokens_hint"),
             "badge": _fmt_max_tokens(max_tok), "badge_style": "warning"},
            {"label": _("params.reasoning_effort"),
             "hint": _("params.reasoning_effort_hint"),
             "badge": _fmt_reasoning_effort(effort), "badge_style": "warning"},
            {"label": _("common.back")},
        ]
        choice = await card_menu(
            items,
            title=_("params.title"),
            facts=[facts_line(_("params.header"),
                              f"temperature={_fmt_temp(temp)}",
                              f"max_tokens={_fmt_max_tokens(max_tok)}",
                              f"effort={_fmt_reasoning_effort(effort)}")],
        )
        if choice is None or choice == 3:
            return

        if choice == 0:
            raw = await overlays.ask_text(
                f"{_('params.new_temp')} ({_fmt_temp(temp)}, "
                f"off = {_('params.default_provider')}):",
                validate=_validate_temp,
            )
            if not raw:
                continue
            if raw.lower() in _TEMP_OFF:
                config.set_value("temperature", None)
                invalidate_api_llm()
                continue
            val = float(raw)
            config.set_value("temperature", val)
            invalidate_api_llm()
            continue

        if choice == 1:
            raw = await overlays.ask_text(
                f"{_('params.new_max_tokens')} ({_fmt_max_tokens(max_tok)}, "
                f"0 = {_('params.default_provider')}):",
                validate=_validate_max_tokens,
            )
            if not raw:
                continue
            val = int(raw)
            config.set_value("max_tokens", val)
            invalidate_api_llm()
            continue

        if choice == 2:
            vals = ["", "low", "medium", "high", "xhigh", "max"]
            items_effort = [
                {"label": _("params.effort_default"), "hint": _("params.effort_default_hint"),
                 "active": effort == ""},
                {"label": _("params.effort_low"), "hint": _("params.effort_low_hint"),
                 "active": effort == "low"},
                {"label": _("params.effort_medium"), "hint": _("params.effort_medium_hint"),
                 "active": effort == "medium"},
                {"label": _("params.effort_high"), "hint": _("params.effort_high_hint"),
                 "active": effort == "high"},
                {"label": _("params.effort_xhigh"), "hint": _("params.effort_xhigh_hint"),
                 "active": effort == "xhigh"},
                {"label": _("params.effort_max"), "hint": _("params.effort_max_hint"),
                 "active": effort == "max"},
                {"label": _("common.back")},
            ]
            # «Назад» — последний пункт (6), а не 4: раньше здесь стояла
            # четвёрка, из-за чего xhigh был недостижим, а Back падал в
            # IndexError по vals[6].
            sub_choice = await card_menu(
                items_effort, title=_("params.reasoning_effort_title"),
                current=vals.index(effort) if effort in vals else 0,
            )
            if sub_choice is None or sub_choice == 6:
                continue

            new_val = vals[sub_choice]
            config.set_value("reasoning_effort", new_val)
            invalidate_api_llm()
            continue
