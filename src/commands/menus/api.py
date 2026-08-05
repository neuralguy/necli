"""Меню /api: провайдеры → детали → ключи → действия над ключом, плюс модели.

Четыре уровня вложенности, поэтому важнее всего две вещи.

* Шапка каждого уровня живёт ВНУТРИ виджета (`CardMenu`), а не печатается в
  scrollback на каждом витке цикла: раньше карточка провайдера дублировалась
  там после каждого нажатия клавиши.
* В статику уходят только события — «сохранено», «удалено», «ошибка сети».
"""

from functools import partial

import config
from commands.menus._style import (
    card_menu,
    confirm_delete,
    facts_line,
    with_spinner,
)
from config.i18n import t as _
from logger import logger
from ui import overlays


def _shorten_url(url: str, max_len: int = 36) -> str:
    if not url:
        return "—"
    u = url.replace("https://", "").replace("http://", "")
    if len(u) <= max_len:
        return u
    return u[: max_len - 1] + "…"


def _ctx_short(n: int) -> str:
    return f"{n // 1_000_000}M" if n >= 1_000_000 else f"{n // 1000}K"


# ─────────────────────── проверки для полей ввода ───────────────────────────
# Раньше int()/float() стояли ПОСЛЕ чтения строки, и опечатка роняла меню
# ValueError'ом. Теперь та же проверка живёт в validate: ошибка показывается
# в самом поле, значение можно поправить, не выходя из потока.
def _validate_int(raw: str) -> str | None:
    if not raw:
        return None
    try:
        int(raw)
    except ValueError:
        return _("params.invalid_int")
    return None


def _validate_float(raw: str) -> str | None:
    if not raw:
        return None
    try:
        float(raw)
    except ValueError:
        return _("params.invalid_number")
    return None


def _provider_rows(providers: list, active_api: str) -> list[dict]:
    """Строки списка провайдеров: имя, URL, число моделей, баланс, статус.

    Статика не зависит ни от курсора, ни от запроса поиска — считаем один раз
    и фильтруем только индексы при смене `query` (как в /models и /sessions).
    """
    from apis.config import get_provider_balance

    rows = []
    for p in providers:
        models_count = len(p["models"])
        if p["id"] == active_api:
            badge, style = "● " + _("common.active"), "success"
        elif p["has_key"] and models_count:
            badge, style = _("common.ready"), "dim"
        elif not p["has_key"]:
            badge, style = _("api.status_no_key"), "error"
        else:
            badge, style = _("api.status_no_models"), "warning"
        balance = get_provider_balance(p["id"])
        rows.append({
            "label": p["name"],
            "hint": _shorten_url(p.get("base_url") or ""),
            "models": str(models_count) if models_count else "—",
            "balance": f"{balance:g}$" if balance else "—",
            "badge": badge,
            "badge_style": style,
            "haystack": f'{p["name"]} {p.get("base_url", "")}'.casefold(),
        })
    return rows


async def _provider_menu(providers: list, active_api: str) -> int | None:
    """Список провайдеров с поиском по имени и URL, как /models и /sessions.

    Возвращает индекс в ИСХОДНОМ списке `providers` либо `len(providers)` —
    пункт «Добавить провайдера», либо None при отмене.
    """
    from ui.menu import (
        ROW_INDENT,
        Palette,
        _normalize_panel_key,
        _run_panel,
        cell,
        cell_width,
        more_note,
        overlay_rows,
        render_width,
        row_line,
        scroll_window,
        search_line,
        section_line,
    )
    from ui.overlays import key_hints

    rows = _provider_rows(providers, active_api)
    total_providers = len(rows)
    query = ""

    # Позиция → индекс в rows; последний элемент всегда «Добавить провайдера».
    order: list[int] = list(range(total_providers))
    version = 0
    layout_cache: dict = {}

    def _refilter(keep: int):
        nonlocal order, version
        q = query.casefold()
        order = [i for i in range(total_providers) if not q or q in rows[i]["haystack"]]
        version += 1
        total = len(order) + 1  # + строка «Добавить провайдера»
        return (True, min(keep, max(0, total - 1)), total)

    def _layout(width: int) -> tuple[int, int, int, int, int]:
        key = (width, version)
        cached = layout_cache.get(key)
        if cached is not None:
            return cached
        label_w = hint_w = 0
        models_w = cell_width(_("api.col_models"))
        balance_w = cell_width(_("api.col_balance"))
        badge_w = 0
        for i in order:
            r = rows[i]
            label_w = max(label_w, cell_width(r["label"]))
            hint_w = max(hint_w, cell_width(r["hint"]))
            models_w = max(models_w, cell_width(r["models"]))
            balance_w = max(balance_w, cell_width(r["balance"]))
            badge_w = max(badge_w, cell_width(r["badge"]))
        label_w = min(max(label_w, 1), 24)
        free = width - ROW_INDENT - 1 - (badge_w + 2 if badge_w else 0)
        hint_w = max(0, min(hint_w, free - label_w - models_w - balance_w - 16))
        if hint_w < 8:
            hint_w = 0
        res = (label_w, hint_w, models_w, balance_w, badge_w)
        layout_cache[key] = res
        return res

    def render_fn(sel: int) -> str:
        pal = Palette()
        width = render_width()
        label_w, hint_w, models_w, balance_w, badge_w = _layout(width)
        total = len(order) + 1
        budget = max(3, overlay_rows(reserve=3))
        start, end, above, below = scroll_window(total, sel, budget)

        lines = [
            section_line(_("api.title"), width, bold=True, pal=pal,
                         right=f"{sel + 1}/{total}" if total else ""),
            search_line(query, width, _("api.search_hint"), pal),
        ]
        headings = [("  " + cell(_("api.col_name"), label_w), pal.dim)]
        if hint_w:
            headings.append(("  " + cell(_("api.col_url"), hint_w), pal.dim))
        headings.extend([
            ("  " + cell(_("api.col_models"), models_w, "right"), pal.dim),
            ("  " + cell(_("api.col_balance"), balance_w, "right"), pal.dim),
        ])
        if badge_w:
            headings.append(("  " + cell(_("api.col_status"), badge_w, "right"), pal.dim))
        lines.append(row_line(headings, width, pal=pal))
        if total == 1 and not order:
            lines.append(f"  {pal.dim}{_('common.no_data')}{pal.reset}")
            return "\n".join(lines)

        if above:
            lines.append(more_note(above, up=True))
        for pos in range(start, end):
            if pos == len(order):
                # Последний пункт — «Добавить провайдера».
                lines.append(row_line(
                    [(_("api.add_provider"), ""),
                     ("  " + _("api.add_hint"), pal.dim)],
                    width, selected=pos == sel, pal=pal))
                continue
            orig = order[pos]
            r = rows[orig]
            is_active = orig < total_providers and providers[orig]["id"] == active_api
            cells = [("● " if is_active else "  ", pal.success),
                     (cell(r["label"], label_w), pal.success if is_active else "")]
            if hint_w:
                cells.append(("  " + cell(r["hint"], hint_w), pal.dim))
            cells.append(("  " + cell(r["models"], models_w, "right"), pal.dim))
            cells.append(("  " + cell(r["balance"], balance_w, "right"), pal.dim))
            if badge_w:
                cells.append(("  " + cell(r["badge"], badge_w, "right"),
                              getattr(pal, r["badge_style"], pal.dim)))
            lines.append(row_line(cells, width, selected=pos == sel, pal=pal))
        if below:
            lines.append(more_note(below, up=False))
        return "\n".join(lines)

    def on_key(key: str, sel: int):
        nonlocal query
        key = _normalize_panel_key(key)

        if key == "backspace":
            if query:
                query = query[:-1]
            return _refilter(sel)
        if key == "escape":
            if query:
                query = ""
                return _refilter(sel)
            return (False, sel, len(order) + 1)
        if len(key) == 1 and key.isprintable():
            query += key
            return _refilter(0)
        return None

    total = len(order) + 1
    result_pos = await _run_panel(
        render_fn,
        key_hints(("type", "to search"), ("↑↓", "move"), ("enter", "open"),
                  ("esc", "close")),
        total, 0,
        on_key=on_key,
    )
    if result_pos is None:
        return None
    if result_pos >= len(order):
        return total_providers  # «Добавить провайдера»
    return order[result_pos]


async def api_interactive():
    """Интерактивное меню управления API-провайдерами. Возвращает SlashResult."""
    from apis.registry import list_providers, reload_providers
    from commands.slash import SlashResult

    r = SlashResult()

    while True:
        providers = list_providers()
        active_api = config.get("active_api", "")
        active_model = config.get("active_api_model", "")

        choice = await _provider_menu(providers, active_api)

        if choice is None:
            return r

        if choice == len(providers):
            await _api_add_menu()
            reload_providers()
            continue

        provider = providers[choice]
        result = await _api_provider_detail(provider, active_api, active_model)
        if result is not None:
            return result
        continue


async def _api_model_add(provider_id: str):
    """Добавление новой text-модели."""
    from apis.config import add_model_to_provider

    model_id = await overlays.ask_text(f"{_('api.field_model_id')}:")
    if not model_id:
        return
    # None (esc) = отмена всего добавления, как прежний Ctrl+C.
    display_name = await overlays.ask_text(f"{_('api.field_display_name')}:")
    if display_name is None:
        return
    ctx_str = await overlays.ask_text(
        f"{_('api.field_context_window')}:", default="128000", validate=_validate_int
    )
    if ctx_str is None:
        return
    in_str = await overlays.ask_text(
        f"{_('api.field_input_price')}:", default="0", validate=_validate_float
    )
    if in_str is None:
        return
    out_str = await overlays.ask_text(
        f"{_('api.field_output_price')}:", default="0", validate=_validate_float
    )
    if out_str is None:
        return

    add_model_to_provider(
        provider_id, model_id, display_name or model_id,
        int(ctx_str), float(in_str), float(out_str),
    )


async def _api_model_edit(provider_id: str, model):
    """Редактирование параметров существующей модели.

    Контекст («что правим») уходит в подпись поля, а не в scrollback: прежняя
    шапка печаталась заново при каждом заходе в редактор.
    """
    from apis.config import add_model_to_provider

    head = f"{model.display_name} · "
    # default= показывает текущее значение и подставляет его на пустой Enter —
    # то самое «Enter — оставить как есть».
    display_name = await overlays.ask_text(
        f"{head}{_('api.field_display_name')}:", default=model.display_name
    )
    if display_name is None:
        return
    ctx_str = await overlays.ask_text(
        f"{head}{_('api.field_context_window')}:",
        default=str(model.context_window), validate=_validate_int,
    )
    if ctx_str is None:
        return
    in_str = await overlays.ask_text(
        f"{head}{_('api.field_input_price')}:",
        default=str(model.input_price), validate=_validate_float,
    )
    if in_str is None:
        return
    out_str = await overlays.ask_text(
        f"{head}{_('api.field_output_price')}:",
        default=str(model.output_price), validate=_validate_float,
    )
    if out_str is None:
        return

    add_model_to_provider(
        provider_id,
        model.id,
        display_name or model.display_name,
        int(ctx_str) if ctx_str else model.context_window,
        float(in_str) if in_str else model.input_price,
        float(out_str) if out_str else model.output_price,
    )


async def _api_provider_edit(provider_id: str):
    """Редактирование параметров провайдера."""
    from apis.config import add_api_config
    from apis.registry import get_definition, reload_providers

    defn = get_definition(provider_id)
    if not defn:
        return

    head = f"{defn.name} · "
    name = await overlays.ask_text(f"{head}{_('api.field_name')}:", default=defn.name)
    if name is None:
        return
    base_url = await overlays.ask_text(
        f"{head}{_('api.field_base_url')}:", default=defn.base_url)
    if base_url is None:
        return
    ptype = await overlays.ask_text(f"{head}{_('api.field_type')}:", default=defn.type)
    if ptype is None:
        return

    add_api_config(
        provider_id=provider_id,
        name=name or defn.name,
        base_url=base_url or defn.base_url,
        provider_type=ptype or defn.type,
        api_format=getattr(defn, 'api_format', None) or "openai",
        models=[{
            "id": m.id,
            "display_name": m.display_name,
            "context_window": m.context_window,
            "input_price": m.input_price,
            "output_price": m.output_price,
        } for m in defn.models],
        default_model=defn.default_model or "",
        default_headers=dict(getattr(defn, "default_headers", None) or {}),
        requires_auth=getattr(defn, "requires_auth", True),
        auth_header=getattr(defn, "auth_header", "Authorization"),
        auth_prefix=getattr(defn, "auth_prefix", "Bearer"),
        max_retries=getattr(defn, "max_retries", 3),
        timeout=getattr(defn, "timeout", 120),
        proxy=getattr(defn, "proxy", ""),
        extra=dict(getattr(defn, "extra", None) or {}),
    )
    reload_providers()


def _mask_api_key(api_key: str) -> str:
    if len(api_key) <= 10:
        return "•" * len(api_key)
    return f"{api_key[:6]}…{api_key[-4:]}"


def _key_badge(item: dict) -> str:
    balance = float(item.get("balance") or 0)
    parts = [f"{balance:g}$" if balance else "без баланса"]
    if item.get("proxy"):
        parts.append(item["proxy"])
    return " · ".join(parts)


def _refresh_active_api_session(pid: str, active_api: str) -> None:
    if pid != active_api:
        return
    from apis.agent_adapter import create_api_session, get_api_session

    existing = get_api_session()
    if existing:
        create_api_session(pid, existing.model_id)


def _prompt_cache_enabled(defn) -> bool:
    extra = getattr(defn, "extra", None) or {}
    mode = str(extra.get("prompt_cache", extra.get("prompt_caching", "auto"))).lower()
    if mode in {"off", "false", "none", "disabled"}:
        return False
    if mode in {"anthropic", "anthropic_cache_control", "cache_control", "on", "true"}:
        return True
    model_ids = [getattr(m, "id", "") for m in getattr(defn, "models", [])]
    return any("claude" in mid.lower() or "anthropic/" in mid.lower() for mid in model_ids)


async def _api_keys_menu(provider_id: str, active_api: str) -> None:
    from apis.config import (
        add_api_credential,
        get_api_credentials,
        remove_api_credential,
        set_api_credential_balance,
        set_api_credential_name,
        set_main_api_credential,
        update_api_credential_proxy,
    )
    from apis.registry import reload_providers

    while True:
        credentials = get_api_credentials(provider_id)
        items = [
            {
                "icon": "★" if item.get("main") else " ",
                "icon_style": "warning",
                "label": item.get("name") or _mask_api_key(item["key"]),
                "hint": _mask_api_key(item["key"]) if item.get("name") else "",
                "badge": _key_badge(item),
                "badge_style": "dim",
            }
            for item in credentials
        ]
        items.append({"label": "Добавить ключ", "hint": "ключ и optional proxy"})
        items.append({"label": _("common.back")})

        choice = await card_menu(
            items,
            title=f"Ключи: {provider_id}",
            facts=[f"{len(credentials)} key(s) · ★ — главный, с него начинаются запросы"],
        )
        if choice is None or choice == len(credentials) + 1:
            return

        if choice == len(credentials):
            # password=True: ключ больше не светится в терминале открытым
            # текстом и не остаётся в scrollback.
            api_key = await overlays.ask_text("API key:", password=True)
            if not api_key:
                continue
            name = await overlays.ask_text("Имя (optional):")
            if name is None:
                continue
            proxy = await overlays.ask_text("Proxy (optional):")
            if proxy is None:
                continue
            add_api_credential(provider_id, api_key, proxy, name)
            reload_providers()
            _refresh_active_api_session(provider_id, active_api)
            continue

        current = credentials[choice]
        actions = [
            {"label": "Переименовать", "hint": current["name"] or "без имени"},
            {"label": "Изменить proxy", "hint": current["proxy"] or "сейчас без proxy"},
            {"label": "Сделать главным", "hint": "запросы будут начинаться с него"},
            {"label": "Баланс", "hint": _key_badge(current)},
            {"label": "Показать ключ полностью", "hint": "вывести ключ без маскировки"},
            {"label": "Удалить ключ", "hint": "убрать только этот ключ"},
            {"label": _("common.back")},
        ]
        action = await card_menu(
            actions,
            title=current["name"] or _mask_api_key(current["key"]),
            status="★ главный" if current.get("main") else "",
            status_style="warning",
            facts=[facts_line(_mask_api_key(current["key"]),
                              _key_badge(current))],
        )
        if action is None or action == 6:
            continue

        if action == 0:
            # Без default: пустой ввод здесь означает «ничего не менять»,
            # а '-' — очистить имя.
            name = await overlays.ask_text(
                f"Имя ({current['name'] or 'без имени'}, '-' убрать):")
            if not name:
                continue
            set_api_credential_name(provider_id, choice, "" if name == "-" else name)
            reload_providers()
            continue

        if action == 1:
            proxy = await overlays.ask_text(
                f"Proxy ({current['proxy'] or 'без proxy'}, '-' убрать):")
            if not proxy:
                continue
            update_api_credential_proxy(provider_id, choice, "" if proxy == "-" else proxy)
            reload_providers()
            _refresh_active_api_session(provider_id, active_api)
            continue

        if action == 2:
            set_main_api_credential(provider_id, choice)
            reload_providers()
            _refresh_active_api_session(provider_id, active_api)
            continue

        if action == 3:
            bal = await overlays.ask_text(
                f"Баланс ({_key_badge(current)}, '.' = 0):")
            if not bal:
                continue
            try:
                value = float(bal.replace(",", "."))
            except ValueError:
                continue
            set_api_credential_balance(provider_id, choice, value)
            reload_providers()
            continue

        if action == 4:
            # Ключ показываем внутри оверлея, а не печатаем в scrollback:
            # статику стереть нельзя, и секрет остался бы в истории терминала.
            await card_menu([{"label": _("common.back")}],
                            title="API key", facts=[current["key"]])
            continue

        if action == 5 and await confirm_delete(
                f"Удалить ключ {_mask_api_key(current['key'])}?"):
            remove_api_credential(provider_id, choice)
            reload_providers()
            _refresh_active_api_session(provider_id, active_api)


async def _api_provider_detail(provider: dict, active_api: str, active_model: str):
    """Меню детали провайдера. Возвращает SlashResult или None."""
    from apis.config import get_api_credentials, remove_api_config
    from apis.registry import get_definition, reload_providers
    from commands.slash import SlashResult

    r = SlashResult()
    pid = provider["id"]

    while True:
        defn = get_definition(pid)
        if not defn:
            return None

        is_active = pid == active_api
        credentials = get_api_credentials(pid)
        has_key = bool(credentials)
        cache_enabled = _prompt_cache_enabled(defn)
        cache_status = _("api.prompt_cache_on") if cache_enabled else _("api.prompt_cache_off")
        key_status = (f"{len(credentials)} key(s)" if has_key
                      else f"API key: {_('common.not_set')}")
        balance_status = f"{sum(float(c.get('balance') or 0) for c in credentials):g}$" \
            if any(c.get("balance") for c in credentials) else "без баланса"
        model_names = ", ".join(m.display_name for m in defn.models) or _("common.none")

        default_model = defn.default_model or (defn.models[0].id if defn.models else "")
        # Ключа api.col_model в словаре нет — t() вернул бы саму строку ключа,
        # и в подсказке светилось «api.col_model: …».
        hint = (f"{_('menu.col_model').lower()}: {default_model}" if default_model
                else _("api.status_no_models"))
        actions = [
            {"label": _("api.switch_model") if is_active else _("api.use"), "hint": hint},
            {"label": "Управление ключами", "hint": f"{len(credentials)} key(s) · баланс: {balance_status}"},
            {"label": _("api.edit_provider"), "hint": _("api.edit_provider_hint")},
            {"label": _("api.manage_models"),
             "hint": f"{len(defn.models)} {_('api.col_models').lower()}"},
            {"label": _("api.prompt_cache"), "hint": cache_status},
            {"label": _("api.refresh_models"), "hint": _("api.refresh_hint")},
            {"label": _("api.delete"), "hint": _("api.delete_permanent")},
            {"label": _("common.back")},
        ]

        choice = await card_menu(
            actions,
            title=defn.name,
            status="● " + _("common.active") if is_active else _("common.inactive"),
            status_style="success" if is_active else "muted",
            facts=[
                facts_line(f"id: {pid}", f"type: {defn.type}", defn.base_url),
                facts_line(key_status, f"баланс: {balance_status}"),
                facts_line(f"{_('api.prompt_cache')}: {cache_status}",
                           f"{_('api.col_models')}: {model_names}"),
            ],
        )

        if choice is None or choice == 7:
            return None

        if choice == 0:
            if not has_key and defn.requires_auth:
                continue
            model_id = defn.default_model or (defn.models[0].id if defn.models else "")
            if not model_id:
                continue

            config.set_active_api(pid)
            config.set_active_api_model(model_id)
            r.switch_api = pid
            r.switch_api_model = model_id
            return r

        if choice == 1:
            await _api_keys_menu(pid, active_api)
            reload_providers()
            continue

        if choice == 2:
            await _api_provider_edit(pid)
            reload_providers()
            continue

        if choice == 3:
            await _api_models_menu(pid)
            reload_providers()
            continue

        if choice == 4:
            from apis.config import set_provider_prompt_cache

            if set_provider_prompt_cache(pid, not cache_enabled):
                reload_providers()
                _refresh_active_api_session(pid, active_api)
            continue

        if choice == 5:
            await _api_sync_models(pid)
            reload_providers()
            continue

        if choice == 6:
            if await confirm_delete(_("api.delete_provider_q", name=pid)):
                if is_active:
                    config.set_active_api("")
                    config.set_active_api_model("")
                    r.switch_api = ""
                remove_api_config(pid)
                reload_providers()
                if r.switch_api is not None:
                    return r
            return None


async def _api_add_menu():
    """Добавление нового провайдера: сразу запрос имени и URL."""
    import re as _re

    from apis.config import add_api_config

    name = await overlays.ask_text(f"{_('api.field_name')}:")
    if not name:
        return
    base_url = await overlays.ask_text(f"{_('api.field_base_url')}:")
    if not base_url:
        return
    pid = _re.sub(r'[^a-z0-9_-]', '', name.lower().replace(' ', '_'))
    if not pid:
        pid = "custom"
    add_api_config(
        provider_id=pid, name=name, base_url=base_url,
        provider_type="openai_compatible", api_format="openai",
    )


async def _api_sync_models(provider_id: str):
    """Auto-discovery of models via {base_url}/models."""
    from apis.model_discovery import sync_models

    try:
        # Запрос сети синхронный: уводим в поток со спиннером в динамической
        # зоне, иначе на время discovery замирает весь Application.
        await with_spinner(
            _("api.fetching", provider=provider_id),
            partial(sync_models, provider_id, replace=False),
        )
    except ValueError:
        return
    except (OSError, RuntimeError):
        return
    except Exception as e:
        logger.debug("sync_models failed: {}", e)
        return


async def _api_models_menu(provider_id: str):
    """Меню управления моделями провайдера.

    Пробел отмечает модели чекбоксом `[x]`, пункт «Удалить выбранные» сносит
    все отмеченные разом. Enter по модели — как раньше: редактирование/удаление
    одной.
    """
    from apis.config import remove_model_from_provider
    from apis.registry import get_definition, reload_providers

    while True:
        reload_providers()
        defn = get_definition(provider_id)
        if not defn:
            return

        items = [
            {
                "label": m.display_name,
                "hint": m.id,
                "cols": [f"${m.input_price:.2f}", f"${m.output_price:.2f}",
                         _ctx_short(m.context_window)],
            }
            for m in defn.models
        ]
        items.append({"label": _("api.add_model"), "hint": _("api.add_model_hint")})
        items.append({"label": _("api.delete_selected"),
                      "hint": _("api.delete_selected_hint")})

        choice, checked = await card_menu(
            items,
            title=_("api.models_title", name=defn.name),
            facts=[f"{len(defn.models)} · {_('menu.model_subtitle')}"],
            multi=True,
        )
        if choice is None:
            return

        if choice == len(defn.models):
            await _api_model_add(provider_id)
            continue

        if choice == len(defn.models) + 1:
            # «Удалить выбранные»: сносим все отмеченные пробелом модели.
            if checked and await confirm_delete(
                    _("api.delete_selected_q", n=len(checked))):
                for idx in sorted(checked, reverse=True):
                    remove_model_from_provider(provider_id, defn.models[idx].id)
            continue

        model = defn.models[choice]
        actions = [
            {"label": _("api.edit"), "hint": _("api.edit_hint")},
            {"label": _("api.delete"), "hint": _("api.delete_remove")},
            {"label": _("common.back")},
        ]
        a = await card_menu(
            actions,
            title=model.display_name,
            facts=[facts_line(model.id,
                              f"${model.input_price:.2f}/${model.output_price:.2f}",
                              _ctx_short(model.context_window))],
        )
        if a == 0:
            await _api_model_edit(provider_id, model)
        elif a == 1 and await confirm_delete(
                _("api.delete_model_q", name=model.display_name)):
            remove_model_from_provider(provider_id, model.id)
        continue
