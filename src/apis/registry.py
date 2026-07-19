"""Реестр API-провайдеров: загрузка, создание инстансов, кеширование.

Аналог sites/registry.py но для API-провайдеров.
Загружает из:
  1. JSON-файлы из apis/definitions/*.json (встроенные шаблоны)
  2. Пользовательские конфиги из config.json["api_providers"]
"""

from __future__ import annotations

import json
from dataclasses import replace

from apis.base import BaseProvider
from apis.config import get_api_key, list_api_configs
from apis.models import ApiModelInfo, ApiProviderDefinition
from config.paths import resource_path
from logger import logger

_DEFINITIONS_DIR = resource_path("apis", "definitions")

# Кеш: definition_id -> ApiProviderDefinition
_definitions: dict[str, ApiProviderDefinition] = {}

# Кеш инстансов: (provider_id, model_id) -> BaseProvider
_instances: dict[tuple[str, str], BaseProvider] = {}

_loaded = False


def _parse_model(raw: dict) -> ApiModelInfo:
    return ApiModelInfo(
        id=raw["id"],
        display_name=raw.get("display_name", raw["id"]),
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
        base_url=data.get("base_url", ""),
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
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Failed to load API definition {json_path.name}: {e}")
            continue
        if "id" not in data:
            logger.error(f"API definition {json_path.name} missing 'id'")
            continue
        defn = _parse_definition(data)
        _definitions[defn.id] = defn
        logger.debug(f"Loaded builtin API definition: {defn.id}")


def _load_user_configs() -> None:
    """Загружает пользовательские конфиги из config.json."""
    for raw in list_api_configs():
        if "id" not in raw:
            continue
        defn = _parse_definition(raw)
        _definitions[defn.id] = defn
        logger.debug(f"Loaded user API config: {defn.id}")


def load_all() -> None:
    global _loaded
    if _loaded:
        return
    _load_builtin_definitions()
    _load_user_configs()
    _loaded = True


def reload_providers() -> None:
    """Полная перезагрузка определений и очистка кеша инстансов."""
    global _loaded
    from apis.config import reset_apis_cache
    reset_apis_cache()
    _definitions.clear()
    _instances.clear()
    _loaded = False
    load_all()

def get_definition(provider_id: str) -> ApiProviderDefinition | None:
    load_all()
    return _definitions.get(provider_id)

def get_definitions() -> dict[str, ApiProviderDefinition]:
    """Публичный доступ к загруженным определениям провайдеров.

    Возвращает живой словарь (не копию) — вызывающий код не должен его
    мутировать. Гарантирует, что definitions загружены.
    """
    load_all()
    return _definitions


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


def get_provider(provider_id: str, model_id: str, **kwargs) -> BaseProvider:
    """Возвращает LLM-провайдер. Кеширует инстанс для одинаковых (provider, model, kwargs)."""
    load_all()

    defn = _definitions.get(provider_id)
    if defn is None:
        raise KeyError(f"API provider '{provider_id}' not found. Available: {', '.join(_definitions.keys())}")

    if not defn.enabled:
        raise ValueError(f"API provider '{provider_id}' is disabled")

    # Ключ кеша — (provider_id, model_id, замороженные kwargs). Важно кешировать
    # даже с kwargs, иначе каждый запрос создаёт новый инстанс с новым session_id,
    # и шлюзы теряют привязку prompt-cache к сессии → Cache write 0.
    kwargs_key = tuple(sorted(kwargs.items())) if kwargs else ()
    cache_key = (provider_id, model_id, kwargs_key)
    cached = _instances.get(cache_key)
    if cached is not None:
        return cached

    instance = _create_instance(defn, model_id, **kwargs)
    _instances[cache_key] = instance
    return instance


def list_providers() -> list[dict]:
    """Список всех провайдеров с мета-инфо."""
    load_all()
    result = []
    for defn in _definitions.values():
        has_key = bool(get_api_key(defn.id))
        result.append({
            "id": defn.id,
            "name": defn.name,
            "type": defn.type,
            "base_url": defn.base_url,
            "enabled": defn.enabled,
            "has_key": has_key,
            "models": [m.display_name for m in defn.models],
            "default_model": defn.default_model,
        })
    return result


def list_api_models() -> list[dict]:
    """Плоский список всех моделей всех активных провайдеров."""
    load_all()
    result = []
    for defn in _definitions.values():
        if not defn.enabled:
            continue
        has_key = bool(get_api_key(defn.id))
        for m in defn.models:
            result.append({  # noqa: PERF401
                "provider_id": defn.id,
                "provider_name": defn.name,
                "model_id": m.id,
                "display_name": m.display_name,
                "context_window": m.context_window,
                "input_price": m.input_price,
                "output_price": m.output_price,
                "has_key": has_key,
            })
    return result


def resolve_api_model(query: str) -> tuple[str, str] | None:
    """Находит (provider_id, model_id) по имени модели.

    Поиск: точное совпадение по model_id или display_name,
    затем fuzzy по подстроке.
    """
    load_all()
    query_lower = query.lower().strip()

    # Точное совпадение
    for defn in _definitions.values():
        if not defn.enabled:
            continue
        for m in defn.models:
            if m.id.lower() == query_lower or m.display_name.lower() == query_lower:
                return (defn.id, m.id)

    # Подстрока
    matches = []
    for defn in _definitions.values():
        if not defn.enabled:
            continue
        for m in defn.models:
            if query_lower in m.id.lower() or query_lower in m.display_name.lower():
                matches.append((defn.id, m.id))  # noqa: PERF401

    if len(matches) == 1:
        return matches[0]

    return None
