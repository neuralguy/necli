"""Управление конфигурацией API-провайдеров. Хранение в .data/apis.json."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from config.paths import APIS_FILE
from config.settings import get, set_value
from logger import logger

_apis_cache: dict | None = None
_apis_load_failed: bool = False

def reset_apis_cache() -> None:
    """Сбрасывает in-memory кэш конфигурации API. Следующее чтение перечитает файл."""
    global _apis_cache
    _apis_cache = None


def _load_apis() -> dict:
    global _apis_cache, _apis_load_failed
    if _apis_cache is not None:
        return _apis_cache

    data: dict = {"providers": [], "keys": {}}
    if APIS_FILE.exists():
        try:
            data = json.loads(APIS_FILE.read_text(encoding="utf-8"))
            _apis_load_failed = False
        except (json.JSONDecodeError, OSError) as e:
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

    _apis_cache = data
    return data


def _save_apis(data: dict) -> None:
    global _apis_cache
    if _apis_load_failed:
        logger.error(
            f"Refusing to save APIs config: previous load of {APIS_FILE} failed. "
            f"Fix the file manually or remove it to recreate."
        )
        _apis_cache = data
        return
    APIS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    # Сначала надёжно пишем на диск, и только при успехе обновляем кэш —
    # иначе при ошибке I/O in-memory кэш расходился бы с файлом, а
    # temp-файл утекал бы в директорию данных.
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=APIS_FILE.parent,
            delete=False,
        ) as fh:
            fh.write(payload)
            tmp_name = fh.name
        os.replace(tmp_name, APIS_FILE)
        tmp_name = None  # успешно перемещён
        _apis_cache = data
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                logger.debug("failed to clean up temp apis file %s", tmp_name)


def _get_store() -> list[dict]:
    return _load_apis().get("providers", [])


def _save_store(store: list[dict]) -> None:
    data = _load_apis()
    data["providers"] = store
    _save_apis(data)


def _get_keys() -> dict[str, Any]:
    return _load_apis().get("keys", {})


def _save_keys(keys: dict) -> None:
    data = _load_apis()
    data["keys"] = keys
    _save_apis(data)


def list_api_configs() -> list[dict]:
    return _get_store()


def get_api_config(provider_id: str) -> dict | None:
    for p in _get_store():
        if p.get("id") == provider_id:
            return p
    return None


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
    """Добавляет или обновляет конфигурацию API-провайдера."""
    store = _get_store()

    config_entry = {
        "id": provider_id,
        "name": name,
        "type": provider_type,
        "base_url": base_url.rstrip("/"),
        "api_format": api_format,
        "models": models or [],
        "default_model": default_model,
        "enabled": True,
        **extra,
    }

    # Обновляем или добавляем
    found = False
    for i, p in enumerate(store):
        if p.get("id") == provider_id:
            store[i] = config_entry
            found = True
            break
    if not found:
        store.append(config_entry)

    _save_store(store)

    # Сохраняем ключ отдельно
    if api_key:
        set_api_key(provider_id, api_key)

    logger.info(f"API provider {'updated' if found else 'added'}: {provider_id} ({name})")
    return config_entry


def remove_api_config(provider_id: str) -> bool:
    store = _get_store()
    new_store = [p for p in store if p.get("id") != provider_id]
    if len(new_store) == len(store):
        return False
    _save_store(new_store)

    # Удаляем ключи
    keys = _get_keys()
    if provider_id in keys:
        del keys[provider_id]
        _save_keys(keys)

    logger.info(f"API provider removed: {provider_id}")
    return True


def _parse_api_key_entry(entry) -> dict[str, Any] | None:
    if isinstance(entry, str):
        raw = entry.strip()
        if not raw:
            return None
        api_key, _sep, proxy = raw.partition("|")
        api_key = api_key.strip()
        if not api_key:
            return None
        return {"key": api_key, "proxy": proxy.strip(), "main": False, "name": ""}
    if isinstance(entry, dict):
        api_key = str(entry.get("key") or entry.get("api_key") or "").strip()
        if not api_key:
            return None
        return {
            "key": api_key,
            "proxy": str(entry.get("proxy") or "").strip(),
            "main": bool(entry.get("main")),
            "name": str(entry.get("name") or "").strip(),
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
        normalized.append({
            "key": api_key,
            "proxy": str(item.get("proxy", "")).strip(),
            "main": is_main,
            "name": str(item.get("name", "")).strip(),
        })
        main_seen = main_seen or is_main
    if normalized and not main_seen:
        normalized[0]["main"] = True
    return normalized


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
    credentials = _normalize_api_credentials([entry for item in raw_entries if (entry := _parse_api_key_entry(item))])
    if provider_id == "anthropic":
        auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
        if auth_token:
            return [{"key": auth_token, "proxy": "", "main": True, "name": "ANTHROPIC_AUTH_TOKEN"}]
    return credentials


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
    return compact


def add_api_credential(provider_id: str, api_key: str, proxy: str = "", name: str = "") -> None:
    entries = get_api_credentials(provider_id)
    entries.append({"key": api_key.strip(), "proxy": proxy.strip(), "name": name.strip()})
    set_api_credentials(provider_id, entries)


def update_api_credential_proxy(provider_id: str, index: int, proxy: str) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["proxy"] = proxy.strip()
    set_api_credentials(provider_id, entries)


def set_main_api_credential(provider_id: str, index: int) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    for i, entry in enumerate(entries):
        entry["main"] = i == index
    set_api_credentials(provider_id, entries)


def set_api_credential_name(provider_id: str, index: int, name: str) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    entries[index]["name"] = name.strip()
    set_api_credentials(provider_id, entries)


def remove_api_credential(provider_id: str, index: int) -> None:
    entries = get_api_credentials(provider_id)
    if index < 0 or index >= len(entries):
        raise IndexError("API key index out of range")
    del entries[index]
    set_api_credentials(provider_id, entries)


def set_api_key(provider_id: str, key: str) -> None:
    """Устанавливает API-ключи. Формат: key1, key2|http://proxy:port."""
    keys = _get_keys()
    entries = _parse_api_key_entries(key)
    if any(entry.get("proxy") for entry in entries):
        keys[provider_id] = entries
    else:
        keys[provider_id] = [entry["key"] for entry in entries]
    _save_keys(keys)


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


def get_api_key(provider_id: str) -> str:
    """Возвращает первый доступный ключ. Для ротации используй get_api_credentials."""
    credentials = get_api_credentials(provider_id)
    return credentials[0]["key"] if credentials else ""


def get_api_keys(provider_id: str) -> list[str]:
    """Возвращает все ключи провайдера без proxy."""
    return [entry["key"] for entry in get_api_credentials(provider_id)]


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
