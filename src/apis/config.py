"""Управление конфигурацией API-провайдеров. Хранение в .data/apis.json."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Optional

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
    _apis_cache = data
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=APIS_FILE.parent,
        delete=False,
    ) as fh:
        fh.write(payload)
        tmp_name = fh.name
    os.replace(tmp_name, APIS_FILE)


def _get_store() -> list[dict]:
    return _load_apis().get("providers", [])


def _save_store(store: list[dict]) -> None:
    data = _load_apis()
    data["providers"] = store
    _save_apis(data)


def _get_keys() -> dict[str, list[str]]:
    return _load_apis().get("keys", {})


def _save_keys(keys: dict) -> None:
    data = _load_apis()
    data["keys"] = keys
    _save_apis(data)


def list_api_configs() -> list[dict]:
    return _get_store()


def get_api_config(provider_id: str) -> Optional[dict]:
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


def set_api_key(provider_id: str, key: str) -> None:
    """Устанавливает API-ключ для провайдера. Поддерживает несколько ключей через запятую."""
    keys = _get_keys()
    key_list = [k.strip() for k in key.split(",") if k.strip()]
    keys[provider_id] = key_list
    _save_keys(keys)


def get_api_key(provider_id: str) -> str:
    """Возвращает первый доступный ключ. Для ротации используй get_api_keys."""
    keys = _get_keys()
    key_list = keys.get(provider_id, [])
    return key_list[0] if key_list else ""


def get_api_keys(provider_id: str) -> list[str]:
    """Возвращает все ключи провайдера."""
    return _get_keys().get(provider_id, [])


def get_tool_format(provider_id: str) -> str:
    """Возвращает формат tool calls для провайдера: 'native' или 'text'. Default 'text' (fenced)."""
    for p in _get_store():
        if p.get("id") == provider_id:
            fmt = p.get("tool_format", "text")
            return fmt if fmt in ("native", "text") else "text"
    return "text"


def set_tool_format(provider_id: str, fmt: str) -> bool:
    """Устанавливает формат tool calls для провайдера ('native' | 'text')."""
    if fmt not in ("native", "text"):
        logger.error(f"Invalid tool_format '{fmt}', expected 'native' or 'text'")
        return False
    store = _get_store()
    for p in store:
        if p.get("id") == provider_id:
            p["tool_format"] = fmt
            _save_store(store)
            _invalidate_provider_instances(provider_id)
            logger.info(f"Tool format for {provider_id} set to {fmt}")
            return True
    return False


def _invalidate_provider_instances(provider_id: str) -> None:
    """Инвалидирует кэш инстансов и определений реестра для провайдера.

    Импорт отложенный — apis.registry зависит от apis.config (избегаем цикла).
    """
    try:
        from apis.registry import reload_providers
        reload_providers()
    except Exception:
        logger.warning(
            f"failed to invalidate registry cache for {provider_id}", exc_info=True
        )

def toggle_api(provider_id: str, enabled: bool) -> bool:
    """Включает/выключает провайдера."""
    store = _get_store()
    for p in store:
        if p.get("id") == provider_id:
            p["enabled"] = enabled
            _save_store(store)
            _invalidate_provider_instances(provider_id)
            return True
    return False


def add_model_to_provider(
    provider_id: str,
    model_id: str,
    display_name: str = "",
    context_window: int = 128_000,
    input_price: float = 0.0,
    output_price: float = 0.0,
) -> bool:
    """Добавляет модель в список моделей провайдера."""
    store = _get_store()
    entry = {
        "id": model_id,
        "display_name": display_name or model_id,
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
