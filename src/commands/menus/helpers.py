"""Configuration menu for cheap helper and image-understanding models."""

from __future__ import annotations

import config
from apis.helper_models import clear_route, route_label, set_route
from commands.menus._style import card_menu
from config.i18n import t as _
from ui.menu import select_api_model_menu


def _available_models():
    from apis.config import get_provider_balance
    from apis.registry import get_definitions

    active = config.get_active_api()
    definitions = get_definitions()
    provider_ids = ([active] if active else []) + [
        provider_id
        for provider_id, definition in definitions.items()
        if provider_id != active and definition.enabled
    ]
    models, providers, labels = [], [], []
    for provider_id in provider_ids:
        definition = definitions.get(provider_id)
        if definition is None or not definition.enabled:
            continue
        balance = get_provider_balance(provider_id)
        label = f"{definition.name} · {balance:g}$" if balance else definition.name
        for model in definition.models:
            models.append(model)
            providers.append(provider_id)
            labels.append(label)
    return models, providers, labels


async def _choose_route(kind: str) -> None:
    current = route_label(kind)
    action = await card_menu(
        [
            {
                "label": _("helpers.use_main"),
                "hint": _("helpers.use_main_hint"),
                "active": not current,
            },
            {"label": _("helpers.choose_model"), "active": bool(current)},
        ],
        title=_(f"helpers.{kind}_title"),
        status=current or _("helpers.main_model"),
    )
    if action is None:
        return
    if action == 0:
        clear_route(kind)
        return

    models, providers, labels = _available_models()
    if not models:
        return
    current_id = str(config.get(f"{kind}_model", "") or "")
    choice = await select_api_model_menu(
        models,
        current_id=current_id,
        group_labels=labels,
        title=_(f"helpers.{kind}_title"),
    )
    if choice is not None:
        set_route(kind, providers[choice], models[choice].id)


async def helpers_interactive() -> None:
    while True:
        helper = route_label("helper")
        image = route_label("image")
        choice = await card_menu(
            [
                {
                    "label": _("helpers.helper_model"),
                    "hint": _("helpers.helper_hint"),
                    "badge": helper or _("helpers.main_model"),
                    "badge_style": "accent" if helper else "dim",
                },
                {
                    "label": _("helpers.image_model"),
                    "hint": _("helpers.image_hint"),
                    "badge": image or _("helpers.main_model"),
                    "badge_style": "accent" if image else "dim",
                },
            ],
            title=_("helpers.title"),
            facts=[_("helpers.description")],
        )
        if choice is None:
            return
        await _choose_route("helper" if choice == 0 else "image")
