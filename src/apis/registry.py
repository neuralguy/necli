"""Реестр API-провайдеров: загрузка, создание инстансов, кеширование.

Аналог sites/registry.py но для API-провайдеров.
Загружает из:
  1. JSON-файлы из apis/definitions/*.json (встроенные шаблоны)
  2. Пользовательские конфиги из config.json["api_providers"]
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace

from apis.base import BaseProvider
from apis.config import get_api_key, list_api_configs, list_routers, normalize_base_url
from apis.models import ApiModelInfo, ApiProviderDefinition
from config.paths import resource_path
from logger import logger

_DEFINITIONS_DIR = resource_path("apis", "definitions")

# Кеш: definition_id -> ApiProviderDefinition
_definitions: dict[str, ApiProviderDefinition] = {}

# Кеш инстансов: (provider_id, model_id, frozen kwargs) -> BaseProvider
_instances: dict[tuple[str, str, tuple], BaseProvider] = {}

_loaded = False
_REGISTRY_LOCK = threading.RLock()


def _parse_model(raw: dict) -> ApiModelInfo:
    from models import normalize_model_name

    raw_id = raw["id"]
    raw_dn = raw.get("display_name", "")
    display_name = raw_dn if raw_dn else normalize_model_name(raw_id)
    return ApiModelInfo(
        id=raw_id,
        display_name=display_name,
        context_window=raw.get("context_window", 128_000),
        input_price=raw.get("input_price", 0.0),
        output_price=raw.get("output_price", 0.0),
    )


def _parse_definition(data: dict) -> ApiProviderDefinition:
    models = [_parse_model(m) for m in data.get("models", [])]
    return ApiProviderDefinition(
        id=data["id"],
        name=data.get("name", data["id"]),
        type=data.get("type", "openai_compatible"),
        base_url=normalize_base_url(data.get("base_url", "")),
        api_format=data.get("api_format", "openai"),
        models=models,
        default_model=data.get("default_model", ""),
        default_headers=data.get("default_headers", {}),
        requires_auth=data.get("requires_auth", True),
        auth_header=data.get("auth_header", "Authorization"),
        auth_prefix=data.get("auth_prefix", "Bearer"),
        max_retries=data.get("max_retries", 3),
        timeout=data.get("timeout", 300),
        proxy=data.get("proxy", ""),
        extra=data.get("extra", {}),
        enabled=data.get("enabled", True),
    )


def _load_builtin_definitions() -> None:
    """Загружает встроенные JSON-определения из apis/definitions/."""
    if not _DEFINITIONS_DIR.exists():
        return
    for json_path in sorted(_DEFINITIONS_DIR.glob("*.json")):
        if json_path.name.startswith("_"):
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("root must be a JSON object")
            if "id" not in data:
                raise ValueError("missing 'id'")
            defn = _parse_definition(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as e:
            logger.error(f"Failed to load API definition {json_path.name}: {e}")
            continue
        _definitions[defn.id] = defn
        logger.debug(f"Loaded builtin API definition: {defn.id}")


def _load_user_configs() -> None:
    """Загружает пользовательские конфиги из config.json."""
    for raw in list_api_configs():
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        try:
            defn = _parse_definition(raw)
        except (KeyError, TypeError, ValueError) as e:
            logger.error("Failed to load user API config {!r}: {}", raw.get("id"), e)
            continue
        _definitions[defn.id] = defn
        logger.debug(f"Loaded user API config: {defn.id}")


def _load_routers_definition() -> None:
    models: list[ApiModelInfo] = []
    for router in list_routers():
        if not router.get("enabled") or not router.get("routes"):
            continue
        route_infos: list[ApiModelInfo] = []
        for route in router["routes"]:
            definition = _definitions.get(route["provider_id"])
            model = definition.get_model_info(route["model_id"]) if definition else None
            if model is not None:
                route_infos.append(model)
        first = route_infos[0] if route_infos else None
        models.append(
            ApiModelInfo(
                id=router["id"],
                display_name=router["name"],
                context_window=min(
                    (model.context_window for model in route_infos), default=128_000
                ),
                input_price=first.input_price if first else 0.0,
                output_price=first.output_price if first else 0.0,
            )
        )
    if models:
        _definitions["routers"] = ApiProviderDefinition(
            id="routers",
            name="routers",
            type="router",
            base_url="",
            models=models,
            default_model=models[0].id,
            requires_auth=False,
        )
    else:
        _definitions.pop("routers", None)


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    with _REGISTRY_LOCK:
        if _loaded:
            return
        _load_builtin_definitions()
        _load_user_configs()
        _load_routers_definition()
        _loaded = True


def reload_providers() -> None:
    """Полная перезагрузка определений и очистка кеша инстансов."""
    global _loaded
    from apis.config import reset_apis_cache

    with _REGISTRY_LOCK:
        reset_apis_cache()
        _definitions.clear()
        _instances.clear()
        _loaded = False
        load_all()


def invalidate_provider_instances() -> None:
    """Drop cached provider objects without reloading definitions."""
    with _REGISTRY_LOCK:
        _instances.clear()


def get_definition(provider_id: str) -> ApiProviderDefinition | None:
    load_all()
    with _REGISTRY_LOCK:
        return _definitions.get(provider_id)


def get_definitions() -> dict[str, ApiProviderDefinition]:
    """Возвращает снимок загруженных definitions без права менять registry."""
    load_all()
    with _REGISTRY_LOCK:
        return dict(_definitions)


def _create_instance(defn: ApiProviderDefinition, model_id: str, **kwargs) -> BaseProvider:
    """Создаёт LLM-инстанс по типу провайдера."""
    # Глобальный прокси из конфига применяется, если у провайдера нет своего.
    if not defn.proxy:
        try:
            import config

            global_proxy = str(config.get("proxy", "") or "").strip()
            if global_proxy:
                defn = replace(defn, proxy=global_proxy)
        except Exception:
            logger.debug("apply global proxy failed", exc_info=True)

    ptype = defn.type.lower()
    fmt = defn.api_format.lower()

    if ptype == "router":
        from apis.config import get_router
        from apis.router_provider import RouterProvider

        router = get_router(model_id)
        if router is None or not router.get("routes"):
            raise ValueError(f"Router '{model_id}' is not configured")
        return RouterProvider(model_id, router["routes"], **kwargs)

    if ptype == "chatgpt":
        from apis.providers.chatgpt_provider import create_chatgpt_provider

        return create_chatgpt_provider(defn, model_id, **kwargs)

    if ptype == "anthropic" or fmt == "anthropic":
        from apis.providers.anthropic_provider import create_anthropic_provider

        return create_anthropic_provider(defn, model_id, **kwargs)

    if ptype == "google" or fmt == "google":
        from apis.providers.google_provider import create_google_provider

        return create_google_provider(defn, model_id, **kwargs)

    if ptype in ("openai_compatible", "openai") or fmt == "openai":
        from apis.providers.openai_provider import create_openai_provider

        return create_openai_provider(defn, model_id, **kwargs)

    # Fallback: custom HTTP provider
    from apis.providers.custom_provider import create_custom_provider

    return create_custom_provider(defn, model_id, **kwargs)


def _freeze_cache_value(value):
    """Convert nested kwargs into a deterministic hashable cache-key value."""
    if isinstance(value, dict):
        return tuple(sorted((str(k), _freeze_cache_value(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_cache_value(v) for v in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze_cache_value(v) for v in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def get_provider(provider_id: str, model_id: str, **kwargs) -> BaseProvider:
    """Возвращает LLM-провайдер. Кеширует инстанс для одинаковых (provider, model, kwargs)."""
    load_all()

    # Ключ кеша — (provider_id, model_id, замороженные kwargs). Важно кешировать
    # даже с kwargs, иначе каждый запрос создаёт новый инстанс с новым session_id,
    # и шлюзы теряют привязку prompt-cache к сессии → Cache write 0.
    kwargs_key = (
        tuple(sorted((key, _freeze_cache_value(value)) for key, value in kwargs.items()))
        if kwargs
        else ()
    )
    cache_key = (provider_id, model_id, kwargs_key)
    with _REGISTRY_LOCK:
        defn = _definitions.get(provider_id)
        if defn is None:
            raise KeyError(
                f"API provider '{provider_id}' not found. "
                f"Available: {', '.join(_definitions.keys())}"
            )
        if not defn.enabled:
            raise ValueError(f"API provider '{provider_id}' is disabled")
        cached = _instances.get(cache_key)
        if cached is not None:
            return cached
        # Создание тоже под lock: иначе два параллельных task оба создают
        # instance с разными stable session-id и один молча перезаписывает другой.
        instance = _create_instance(defn, model_id, **kwargs)
        _instances[cache_key] = instance
        return instance


def list_providers() -> list[dict]:
    """Список всех провайдеров с мета-инфо."""
    load_all()
    with _REGISTRY_LOCK:
        definitions = list(_definitions.values())
    result = []
    for defn in definitions:
        if defn.type == "router":
            continue
        if defn.type == "chatgpt":
            from apis.chatgpt_auth import chatgpt_auth_status
            from apis.chatgpt_usage import get_cached_chatgpt_usage

            has_key = bool(chatgpt_auth_status().get("authenticated"))
            weekly_usage = get_cached_chatgpt_usage() if has_key else None
        else:
            has_key = bool(get_api_key(defn.id))
            weekly_usage = None
        result.append(
            {
                "id": defn.id,
                "name": defn.name,
                "type": defn.type,
                "base_url": defn.base_url,
                "enabled": defn.enabled,
                "has_key": has_key,
                "models": [m.display_name for m in defn.models],
                "default_model": defn.default_model,
                "weekly_usage": weekly_usage,
            }
        )
    return result


def resolve_api_model(query: str) -> tuple[str, str] | None:
    """Находит (provider_id, model_id) по имени модели.

    Поиск: точное совпадение по model_id или display_name,
    затем fuzzy по подстроке.
    """
    load_all()
    query_lower = query.lower().strip()
    with _REGISTRY_LOCK:
        definitions = list(_definitions.values())

    # Точное совпадение
    for defn in definitions:
        if not defn.enabled:
            continue
        for m in defn.models:
            if m.id.lower() == query_lower or m.display_name.lower() == query_lower:
                return (defn.id, m.id)

    # Подстрока
    matches = []
    for defn in definitions:
        if not defn.enabled:
            continue
        for m in defn.models:
            if query_lower in m.id.lower() or query_lower in m.display_name.lower():
                matches.append((defn.id, m.id))

    if len(matches) == 1:
        return matches[0]

    return None
