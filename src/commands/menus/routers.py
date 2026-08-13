"""Creation and ordering UI for model fallback routers."""

from __future__ import annotations

from config.i18n import t as _
from ui import overlays

from ._style import card_menu, confirm_delete, facts_line


def _reload_routes() -> None:
    from apis.agent_adapter import invalidate_api_llm
    from apis.registry import reload_providers

    reload_providers()
    invalidate_api_llm()


def _available_routes() -> list[tuple[dict, object, str]]:
    from apis.registry import get_definitions

    rows = []
    for provider_id, definition in get_definitions().items():
        if provider_id == "routers" or not definition.enabled:
            continue
        rows.extend(
            (
                {"provider_id": provider_id, "model_id": model.id},
                model,
                definition.name,
            )
            for model in definition.models
        )
    return rows


async def _pick_routes(selected: list[dict] | None = None) -> list[dict] | None:
    available = _available_routes()
    selected_keys = {(route["provider_id"], route["model_id"]) for route in selected or []}
    items = [
        {
            "label": model.display_name,
            "hint": f"{provider_name} · {model.id}",
            "cols": [f"${model.input_price:.2f}", f"${model.output_price:.2f}"],
        }
        for _route, model, provider_name in available
    ]
    choice, checked = await card_menu(
        items,
        title=_("routers.pick_models"),
        facts=[_("routers.pick_hint")],
        multi=True,
        expand=True,
        split_focus=True,
        checked={
            index
            for index, (route, _model, _provider) in enumerate(available)
            if (route["provider_id"], route["model_id"]) in selected_keys
        },
    )
    if choice is None:
        return None
    routes = [
        route for index, (route, _model, _provider) in enumerate(available) if index in checked
    ]
    if len(routes) < 2:
        return None
    return routes


def _next_router_id(routers: list[dict]) -> str:
    used = {router["id"] for router in routers}
    index = 1
    while f"router-{index}" in used:
        index += 1
    return f"router-{index}"


async def _create_router() -> None:
    from apis.config import list_routers, save_router

    name = await overlays.ask_text(f"{_('routers.field_name')}:")
    if not name or not name.strip():
        return
    routes = await _pick_routes()
    if not routes:
        return
    save_router(_next_router_id(list_routers()), name.strip(), routes)
    _reload_routes()


def _route_title(route: dict) -> tuple[str, str]:
    from apis.registry import get_definition

    definition = get_definition(route["provider_id"])
    model = definition.get_model_info(route["model_id"]) if definition else None
    name = model.display_name if model else route["model_id"]
    provider = definition.name if definition else route["provider_id"]
    return name, f"{provider} · {route['model_id']}"


async def _router_detail(router_id: str) -> None:
    import config
    from apis.config import (
        get_router,
        get_router_balance,
        move_router_route,
        remove_router,
        save_router,
    )

    while True:
        router = get_router(router_id)
        if router is None:
            return
        items = []
        for index, route in enumerate(router["routes"]):
            label, hint = _route_title(route)
            items.append(
                {
                    "label": f"{index + 1}. {label}",
                    "hint": hint,
                }
            )
        is_active = (
            config.get_active_api() == "routers" and config.get_active_api_model() == router_id
        )
        items.extend(
            [
                {
                    "icon": "☷",
                    "icon_style": "info",
                    "action": True,
                    "label": _("routers.change_models"),
                    "hint": _("routers.change_models_hint"),
                },
                {
                    "icon": "×",
                    "icon_style": "error",
                    "role": "error",
                    "action": True,
                    "label": _("routers.delete"),
                    "hint": _("routers.delete_hint"),
                },
            ]
        )
        if is_active:
            items[-1]["hint"] = _("routers.delete_active_hint")
            items[-1]["skip"] = True
        choice = await card_menu(
            items,
            title=router["name"],
            status=f"{_('routers.balance')}: {get_router_balance(router_id):g}$",
            facts=[_("routers.order_hint")],
        )
        if choice is None:
            return
        route_count = len(router["routes"])
        if choice == route_count:
            routes = await _pick_routes(router["routes"])
            if routes:
                existing_order = {
                    (route["provider_id"], route["model_id"]): index
                    for index, route in enumerate(router["routes"])
                }
                routes.sort(
                    key=lambda route: existing_order.get(
                        (route["provider_id"], route["model_id"]), len(existing_order)
                    )
                )
                save_router(router_id, router["name"], routes)
                _reload_routes()
            continue
        if choice == route_count + 1:
            if await confirm_delete(_("routers.delete_q", name=router["name"])):
                remove_router(router_id)
                _reload_routes()
                return
            continue

        actions = [
            {"label": _("routers.move_up"), "action": True},
            {"label": _("routers.move_down"), "action": True},
            {"label": _("routers.remove_model"), "role": "error", "action": True},
        ]
        action = await card_menu(
            actions,
            title=_route_title(router["routes"][choice])[0],
            facts=[
                facts_line(_("routers.position", n=choice + 1), _("routers.total", n=route_count))
            ],
        )
        if action == 0:
            move_router_route(router_id, choice, -1)
            _reload_routes()
        elif action == 1:
            move_router_route(router_id, choice, 1)
            _reload_routes()
        elif action == 2 and route_count > 2:
            routes = list(router["routes"])
            del routes[choice]
            save_router(router_id, router["name"], routes)
            _reload_routes()


async def routers_interactive() -> None:
    from apis.config import get_router_balance, list_routers

    while True:
        routers = list_routers()
        items = [
            {
                "label": router["name"],
                "hint": " → ".join(_route_title(route)[0] for route in router["routes"]),
                "cols": [f"{get_router_balance(router['id']):g}$"],
            }
            for router in routers
        ]
        items.append(
            {
                "icon": "+",
                "icon_style": "accent",
                "role": "accent",
                "action": True,
                "label": _("routers.create"),
                "hint": _("routers.create_hint"),
            }
        )
        choice = await card_menu(items, title=_("routers.title"))
        if choice is None:
            return
        if choice == len(routers):
            await _create_router()
        else:
            await _router_detail(routers[choice]["id"])
