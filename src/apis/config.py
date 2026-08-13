"""Управление конфигурацией API-провайдеров. Хранение в .data/apis.json."""

from __future__ import annotations

import copy
import json
import os
import threading
from functools import wraps
from typing import Any

from config._atomic import atomic_write_json
from config.paths import APIS_FILE
from config.settings import get, set_value
from logger import logger

_apis_cache: dict | None = None
_apis_load_failed: bool = False
_APIS_LOCK = threading.RLock()


def _locked(func):
    """Serialize access to the mutable in-memory APIs config and its file."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        with _APIS_LOCK:
            return func(*args, **kwargs)

    return wrapper


@_locked
def reset_apis_cache() -> None:
    """Сбрасывает in-memory кэш конфигурации API. Следующее чтение перечитает файл."""
    global _apis_cache
    _apis_cache = None


@_locked
def _load_apis() -> dict:
    global _apis_cache, _apis_load_failed
    if _apis_cache is not None:
        return copy.deepcopy(_apis_cache)

    data: dict = {"providers": [], "keys": {}}
    if APIS_FILE.exists():
        try:
            loaded = json.loads(APIS_FILE.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("root must be a JSON object")
            data = loaded
            _apis_load_failed = False
        except (json.JSONDecodeError, OSError, ValueError) as e:
            _apis_load_failed = True
            logger.error(
                f"Failed to load APIs config {APIS_FILE}: {type(e).__name__}: {e}. "
                f"Using empty config in memory; saving is disabled until file is fixed."
            )
    else:
        # Миграция из config.json
        providers = get("api_providers", [])
        keys = get("api_keys", {})
        if providers or keys:
            data = {"providers": providers, "keys": keys}
            _save_apis(data)
            # Очищаем из config.json
            set_value("api_providers", [])
            set_value("api_keys", {})

    _apis_cache = copy.deepcopy(data)
    return copy.deepcopy(_apis_cache)


@_locked
def _save_apis(data: dict) -> None:
    global _apis_cache
    if _apis_load_failed:
        logger.error(
            f"Refusing to save APIs config: previous load of {APIS_FILE} failed. "
            f"Fix the file manually or remove it to recreate."
        )
        return
    # Disk first, cache second: an I/O failure must not make in-memory state
    # claim a value that was never persisted. atomic_write_json also cleans
    # the temporary file on failure.
    atomic_write_json(APIS_FILE, data)
    _apis_cache = copy.deepcopy(data)


@_locked
def _get_store() -> list[dict]:
    store = _load_apis().get("providers", [])
    return copy.deepcopy(store) if isinstance(store, list) else []


@_locked
def _save_store(store: list[dict]) -> None:
    data = _load_apis()
    data["providers"] = store
    _save_apis(data)


@_locked
def _get_keys() -> dict[str, Any]:
    keys = _load_apis().get("keys", {})
    return copy.deepcopy(keys) if isinstance(keys, dict) else {}


@_locked
def _save_keys(keys: dict) -> None:
    data = _load_apis()
    data["keys"] = keys
    _save_apis(data)


@_locked
def list_api_configs() -> list[dict]:
    return copy.deepcopy(_get_store())


def _normalize_router_route(route: Any) -> dict[str, str] | None:
    if not isinstance(route, dict):
        return None
    provider_id = str(route.get("provider_id") or "").strip()
    model_id = str(route.get("model_id") or "").strip()
    if not provider_id or not model_id or provider_id == "routers":
        return None
    return {"provider_id": provider_id, "model_id": model_id}


@_locked
def list_routers() -> list[dict]:
    raw = _load_apis().get("routers", [])
    if not isinstance(raw, list):
        return []
    routers: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        router_id = str(item.get("id") or "").strip()
        if not router_id:
            continue
        routes = [
            normalized
            for route in item.get("routes", [])
            if (normalized := _normalize_router_route(route)) is not None
        ]
        routers.append(
            {
                "id": router_id,
                "name": str(item.get("name") or router_id).strip() or router_id,
                "routes": routes,
                "enabled": bool(item.get("enabled", True)),
            }
        )
    return routers


@_locked
def get_router(router_id: str) -> dict | None:
    return next((router for router in list_routers() if router["id"] == router_id), None)


@_locked
def save_router(router_id: str, name: str, routes: list[dict]) -> dict:
    router_id = str(router_id or "").strip()
    if not router_id:
        raise ValueError("Router ID is required")
    normalized = [
        value for route in routes if (value := _normalize_router_route(route)) is not None
    ]
    if not normalized:
        raise ValueError("Router must contain at least one model")
    data = _load_apis()
    routers = data.get("routers")
    if not isinstance(routers, list):
        routers = []
        data["routers"] = routers
    entry = {
        "id": router_id,
        "name": str(name or router_id).strip() or router_id,
        "routes": normalized,
        "enabled": True,
    }
    for index, router in enumerate(routers):
        if isinstance(router, dict) and router.get("id") == router_id:
            entry["enabled"] = bool(router.get("enabled", True))
            routers[index] = entry
            break
    else:
        routers.append(entry)
    _save_apis(data)
    return copy.deepcopy(entry)


@_locked
def remove_router(router_id: str) -> bool:
    data = _load_apis()
    routers = data.get("routers")
    if not isinstance(routers, list):
        return False
    filtered = [
        router
        for router in routers
        if not isinstance(router, dict) or router.get("id") != router_id
    ]
    if len(filtered) == len(routers):
        return False
    data["routers"] = filtered
    _save_apis(data)
    return True


@_locked
def move_router_route(router_id: str, index: int, offset: int) -> bool:
    router = get_router(router_id)
    if router is None:
        return False
    target = index + offset
    if index < 0 or index >= len(router["routes"]) or target < 0 or target >= len(router["routes"]):
        return False
    router["routes"][index], router["routes"][target] = (
        router["routes"][target],
        router["routes"][index],
    )
    save_router(router["id"], router["name"], router["routes"])
    return True


@_locked
def get_router_balance(router_id: str) -> float:
    router = get_router(router_id)
    if router is None:
        return 0.0
    provider_ids = {route["provider_id"] for route in router["routes"]}
    return sum(get_provider_balance(provider_id) for provider_id in provider_ids)


def normalize_base_url(base_url: str) -> str:
    """Normalize a pasted API base or chat-completions endpoint."""
    value = str(base_url or "").strip().rstrip("/")
    suffix = "/chat/completions"
    if value.lower().endswith(suffix):
        value = value[: -len(suffix)].rstrip("/")
    return value


@_locked
def add_api_config(
    provider_id: str,
    name: str,
    base_url: str,
    api_key: str = "",
    provider_type: str = "openai_compatible",
    api_format: str = "openai",
    models: list[dict] | None = None,
    default_model: str = "",
    **extra,
) -> dict:
    """Добавляет/обновляет провайдера и ключ одним атомарным save."""
    data = _load_apis()
    store = data.get("providers")
    if not isinstance(store, list):
        store = []
        data["providers"] = store

    config_entry = {
        "id": provider_id,
        "name": name,
        "type": provider_type,
        "base_url": normalize_base_url(base_url),
        "api_format": api_format,
        "models": copy.deepcopy(models) if isinstance(models, list) else [],
        "default_model": default_model,
        "enabled": True,
        **copy.deepcopy(extra),
    }

    found = False
    for i, provider in enumerate(store):
        if isinstance(provider, dict) and provider.get("id") == provider_id:
            store[i] = config_entry
            found = True
            break
    if not found:
        store.append(config_entry)

    if api_key:
        keys = data.get("keys")
        if not isinstance(keys, dict):
            keys = {}
            data["keys"] = keys
        entries = _parse_api_key_entries(api_key)
        if any(entry.get("proxy") for entry in entries):
            keys[provider_id] = entries
        else:
            keys[provider_id] = [entry["key"] for entry in entries]

    _save_apis(data)
    logger.info(f"API provider {'updated' if found else 'added'}: {provider_id} ({name})")
    return copy.deepcopy(config_entry)


@_locked
def remove_api_config(provider_id: str) -> bool:
    data = _load_apis()
    store = data.get("providers")
    if not isinstance(store, list):
        return False
    new_store = [
        provider
        for provider in store
        if not isinstance(provider, dict) or provider.get("id") != provider_id
    ]
    if len(new_store) == len(store):
        return False
    data["providers"] = new_store
    keys = data.get("keys")
    if isinstance(keys, dict):
        keys.pop(provider_id, None)
    _save_apis(data)

    logger.info(f"API provider removed: {provider_id}")
    return True


def _parse_balance(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_rpm(value) -> float:
    try:
        rpm = float(value)
    except (TypeError, ValueError):
        return 0.0
    return rpm if rpm > 0 else 0.0


def _parse_api_key_entry(entry) -> dict[str, Any] | None:
    if isinstance(entry, str):
        raw = entry.strip()
        if not raw:
            return None
        api_key, _sep, proxy = raw.partition("|")
        api_key = api_key.strip()
        if not api_key:
            return None
        return {
            "key": api_key,
            "proxy": proxy.strip(),
            "main": False,
            "name": "",
            "balance": 0.0,
            "rpm": 0.0,
        }
    if isinstance(entry, dict):
        api_key = str(entry.get("key") or entry.get("api_key") or "").strip()
        if not api_key:
            return None
        return {
            "key": api_key,
            "proxy": str(entry.get("proxy") or "").strip(),
            "main": bool(entry.get("main")),
            "name": str(entry.get("name") or "").strip(),
            "balance": _parse_balance(entry.get("balance")),
            "rpm": _parse_rpm(entry.get("rpm")),
        }
    return None


def _parse_api_key_entries(value: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for part in value.replace("\n", ",").split(","):
        entry = _parse_api_key_entry(part)
        if entry:
            entries.append(entry)
    return entries


def _normalize_api_credentials(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    main_seen = False
    for item in entries:
        api_key = str(item.get("key", "")).strip()
        if not api_key:
            continue
        is_main = bool(item.get("main")) and not main_seen
        normalized.append(
            {
                "key": api_key,
                "proxy": str(item.get("proxy", "")).strip(),
                "main": is_main,
                "name": str(item.get("name", "")).strip(),
                "balance": _parse_balance(item.get("balance")),
                "rpm": _parse_rpm(item.get("rpm")),
            }
        )
        main_seen = main_seen or is_main
    if normalized and not main_seen:
        normalized[0]["main"] = True
    return normalized


@_locked
def get_api_credentials(provider_id: str) -> list[dict[str, Any]]:
    """Возвращает все ключи провайдера с опциональными per-key proxy.

    Формат в .data/apis.json обратно совместим:
    - "keys": {"pid": ["key1", "key2"]}
    - "keys": {"pid": [{"key": "key1", "proxy": "http://..."}, ...]}
    При ручном/CLI вводе можно писать: key1, key2|http://proxy:port.
    """
    raw_entries = _get_keys().get(provider_id, [])
    if isinstance(raw_entries, str):
        raw_entries = [raw_entries]
    if not isinstance(raw_entries, list):
        raw_entries = []
    credentials = _normalize_api_credentials(
        [entry for item in raw_entries if (entry := _parse_api_key_entry(item))]
    )
    if provider_id == "anthropic":
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if auth_token:
            return [{"key": auth_token, "proxy": "", "main": True, "name": "ANTHROPIC_AUTH_TOKEN"}]
    return credentials


@_locked
def set_api_credentials(provider_id: str, credentials: list[dict[str, Any]]) -> None:
    keys = _get_keys()
    entries = _normalize_api_credentials(credentials)
    if entries:
        keys[provider_id] = [_compact_credential(entry) for entry in entries]
    else:
        keys.pop(provider_id, None)
    _save_keys(keys)


def _compact_credential(entry: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {"key": entry["key"]}
    if entry.get("proxy"):
        compact["proxy"] = entry["proxy"]
    if entry.get("main"):
        compact["main"] = True
    if entry.get("name"):
        compact["name"] = entry["name"]
    balance = _parse_balance(entry.get("balance"))
    if balance:
        compact["balance"] = round(balance, 6)
    rpm = _parse_rpm(entry.get("rpm"))
    if rpm:
        compact["rpm"] = round(rpm, 3)
    return compact


@_locked
def add_api_credential(provider_id: str, api_key: str, proxy: str = "", name: str = "") -> None:
    entries = get_api_credentials(provider_id)
    entries.append({"key": api_key.strip(), "proxy": proxy.strip(), "name": name.strip()})
    set_api_credentials(provider_id, entries)


@_locked
def update_api_credential_proxy(provider_id: str, index: int, proxy: str) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["proxy"] = proxy.strip()
    set_api_credentials(provider_id, entries)


@_locked
def set_main_api_credential(provider_id: str, index: int) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    for i, entry in enumerate(entries):
        entry["main"] = i == index
    set_api_credentials(provider_id, entries)


@_locked
def set_api_credential_name(provider_id: str, index: int, name: str) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["name"] = name.strip()
    set_api_credentials(provider_id, entries)


@_locked
def set_api_credential_balance(provider_id: str, index: int, balance: float) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["balance"] = _parse_balance(balance)
    set_api_credentials(provider_id, entries)


@_locked
def set_api_credential_rpm(provider_id: str, index: int, rpm: float) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["rpm"] = _parse_rpm(rpm)
    set_api_credentials(provider_id, entries)


@_locked
def spend_api_credential(provider_id: str, key: str, amount: float) -> float | None:
    """Списывает amount с баланса ключа провайдера.

    Возвращает новый баланс ключа, либо None, если ключ не найден
    или amount <= 0. Баланс по умолчанию 0 и уходит в минус при перерасходе.
    """
    if amount <= 0:
        return None
    keys = _get_keys()
    raw = keys.get(provider_id)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return None
    raw_list: list = raw
    for i, item in enumerate(raw_list):
        entry = _parse_api_key_entry(item)
        if not entry or entry["key"] != key:
            continue
        new_balance = round(_parse_balance(entry.get("balance")) - amount, 6)
        if isinstance(item, dict):
            if new_balance:
                item["balance"] = new_balance
            else:
                item.pop("balance", None)
        else:
            # строковый ключ: переводим в dict, чтобы хранить баланс
            raw_list[i] = (
                {"key": entry["key"], "balance": new_balance}
                if new_balance
                else {"key": entry["key"]}
            )
        _save_keys(keys)
        return new_balance
    return None


@_locked
def get_provider_balance(provider_id: str) -> float:
    """Суммарный баланс всех ключей провайдера."""
    if provider_id == "routers":
        provider_ids = {
            route["provider_id"] for router in list_routers() for route in router["routes"]
        }
        return sum(get_provider_balance(item) for item in provider_ids)
    return sum(_parse_balance(cred.get("balance")) for cred in get_api_credentials(provider_id))


@_locked
def remove_api_credential(provider_id: str, index: int) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    del entries[index]
    set_api_credentials(provider_id, entries)


@_locked
def set_api_key(provider_id: str, key: str) -> None:
    """Устанавливает API-ключи. Формат: key1, key2|http://proxy:port."""
    keys = _get_keys()
    entries = _parse_api_key_entries(key)
    if any(entry.get("proxy") for entry in entries):
        keys[provider_id] = entries
    else:
        keys[provider_id] = [entry["key"] for entry in entries]
    _save_keys(keys)


@_locked
def set_provider_prompt_cache(provider_id: str, enabled: bool) -> bool:
    """Включает/выключает отправку prompt cache параметров для провайдера."""
    store = _get_store()
    for p in store:
        if p.get("id") != provider_id:
            continue
        extra = p.get("extra")
        if not isinstance(extra, dict):
            extra = {}
        extra["prompt_cache"] = "on" if enabled else "off"
        p["extra"] = extra
        _save_store(store)
        return True
    return False


@_locked
def get_api_key(provider_id: str) -> str:
    """Возвращает первый доступный ключ. Для ротации используй get_api_credentials."""
    credentials = get_api_credentials(provider_id)
    return credentials[0]["key"] if credentials else ""


@_locked
def add_model_to_provider(
    provider_id: str,
    model_id: str,
    display_name: str = "",
    context_window: int = 128_000,
    input_price: float = 0.0,
    output_price: float = 0.0,
) -> bool:
    """Добавляет модель в список моделей провайдера."""
    from models import normalize_model_name

    store = _get_store()
    entry = {
        "id": model_id,
        "display_name": display_name or normalize_model_name(model_id),
        "context_window": context_window,
        "input_price": input_price,
        "output_price": output_price,
    }
    for p in store:
        if p.get("id") == provider_id:
            models = p.get("models", [])
            replaced = False
            for i, m in enumerate(models):
                if m.get("id") == model_id:
                    models[i] = entry
                    replaced = True
                    break
            if not replaced:
                models.append(entry)
            p["models"] = models
            _save_store(store)
            return True
    return False


@_locked
def remove_model_from_provider(provider_id: str, model_id: str) -> bool:
    """Удаляет модель из списка моделей провайдера."""
    store = _get_store()
    for p in store:
        if p.get("id") == provider_id:
            models = p.get("models", [])
            new_models = [m for m in models if m.get("id") != model_id]
            if len(new_models) == len(models):
                return False
            p["models"] = new_models
            _save_store(store)
            return True
    return False
