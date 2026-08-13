"""Model routing for isolated helper and image-understanding requests."""

from __future__ import annotations

from dataclasses import dataclass

import config
from apis.registry import get_definition


@dataclass(frozen=True)
class ModelRoute:
    provider_id: str
    model_id: str


def configured_route(kind: str) -> ModelRoute | None:
    """Return a valid configured route, or ``None`` to use the main model."""
    provider_id = str(config.get(f"{kind}_provider", "") or "").strip()
    model_id = str(config.get(f"{kind}_model", "") or "").strip()
    if not provider_id or not model_id:
        return None
    definition = get_definition(provider_id)
    if definition is None or not definition.enabled:
        return None
    if not any(model.id == model_id for model in definition.models):
        return None
    return ModelRoute(provider_id, model_id)


def effective_route(kind: str, main_provider: str, main_model: str) -> ModelRoute:
    return configured_route(kind) or ModelRoute(main_provider, main_model)


def set_route(kind: str, provider_id: str, model_id: str) -> None:
    config.set_value(f"{kind}_provider", provider_id)
    config.set_value(f"{kind}_model", model_id)


def clear_route(kind: str) -> None:
    set_route(kind, "", "")


def route_label(kind: str) -> str:
    route = configured_route(kind)
    if route is None:
        return ""
    definition = get_definition(route.provider_id)
    if definition is None:
        return ""
    model = definition.get_model_info(route.model_id)
    return f"{definition.name} / {model.display_name if model else route.model_id}"
