"""Автообнаружение моделей через GET {base_url}/models.

Поддерживает OpenAI-совместимый формат (большинство провайдеров),
Ollama (свой формат), и заглушки для Anthropic/Google (у них нет /models endpoint
в публичном API — список моделей хардкод).
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from apis.config import get_api_key, add_model_to_provider, remove_model_from_provider
from apis.registry import get_definition, reload_providers
from logger import logger


# Анти-перезапись цен: модели в этом списке имеют ручные цены в JSON-определениях,
# при синке цены НЕ обновляются (источник истины — JSON).
_PRESERVE_PRICES_PROVIDERS = {"openai", "anthropic", "google", "xai"}


async def _fetch_openai_compatible(base_url: str, headers: dict[str, str], timeout: int) -> list[dict]:
    """GET {base_url}/models → список моделей в OpenAI-формате."""
    url = base_url.rstrip("/") + "/models"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        resp = await client.get(url, headers=headers)
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError(f"Unexpected response format: {str(data)[:300]}")

    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("model") or item.get("name")
        if not model_id:
            continue
        ctx = (
            item.get("context_length")
            or item.get("context_window")
            or (item.get("top_provider", {}) or {}).get("context_length")
            or 128_000
        )
        # OpenRouter возвращает цены в pricing.prompt/completion (per token, не per 1M)
        pricing = item.get("pricing") or {}
        input_price = 0.0
        output_price = 0.0
        if pricing:
            try:
                p_in = float(pricing.get("prompt", 0) or 0)
                p_out = float(pricing.get("completion", 0) or 0)
                # Разные провайдеры отдают цены в разном формате: OpenRouter —
                # per-token (0.000003 = $3/1M), другие — уже за 1M токенов.
                # Эвристика по величине: очень малые значения (< 0.001)
                # трактуем как per-token и домножаем на 1M; крупные оставляем
                # как $/1M. (Это сознательный dual-format handler.)
                input_price = p_in * 1_000_000 if 0 < p_in < 0.001 else p_in
                output_price = p_out * 1_000_000 if 0 < p_out < 0.001 else p_out
            except (TypeError, ValueError):
                pass
        display_name = item.get("name") or item.get("display_name") or model_id
        result.append({
            "id": model_id,
            "display_name": display_name,
            "context_window": int(ctx),
            "input_price": float(input_price),
            "output_price": float(output_price),
        })
    return result


async def _fetch_ollama(base_url: str, timeout: int) -> list[dict]:
    """Ollama: GET /api/tags → список локальных моделей."""
    api_base = base_url.rstrip("/")
    if api_base.endswith("/v1"):
        api_base = api_base[:-3]
    url = api_base + "/api/tags"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        resp = await client.get(url)
        if resp.status_code != 200:
            raise ValueError(f"HTTP {resp.status_code}: {resp.text[:300]}")
        data = resp.json()

    models = data.get("models", [])
    result = []
    for m in models:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model")
        if not name:
            continue
        # context_length из details или дефолт
        details = m.get("details") or {}
        ctx = details.get("context_length") or 32768
        size_gb = (m.get("size") or 0) / (1024 ** 3)
        hint = f"{size_gb:.1f}GB" if size_gb else ""
        result.append({
            "id": name,
            "display_name": f"{name} ({hint})" if hint else name,
            "context_window": int(ctx),
            "input_price": 0.0,
            "output_price": 0.0,
        })
    return result


_LOCAL_PROVIDER_IDS = {"ollama", "lmstudio"}


def _is_local_url(base_url: str) -> bool:
    return ("localhost" in base_url) or ("127.0.0.1" in base_url) or ("0.0.0.0" in base_url)


async def _discover_async(provider_id: str) -> list[dict]:
    defn = get_definition(provider_id)
    if defn is None:
        raise KeyError(f"Provider '{provider_id}' not found")

    base_url = defn.base_url
    timeout = defn.timeout or 30

    # Ollama — отдельный endpoint /api/tags. Детектим по явному маркеру
    # (type/api_format == "ollama" или extra["api"] == "ollama"), затем по
    # provider_id и, как последний fallback, по дефолтному порту 11434.
    explicit_ollama = (
        (getattr(defn, "type", "") or "").lower() == "ollama"
        or (getattr(defn, "api_format", "") or "").lower() == "ollama"
        or str((getattr(defn, "extra", None) or {}).get("api", "")).lower() == "ollama"
    )
    if explicit_ollama or provider_id == "ollama" or "11434" in base_url:
        return await _fetch_ollama(base_url, timeout)

    is_local = provider_id in _LOCAL_PROVIDER_IDS or _is_local_url(base_url)

    if not is_local and provider_id in ("anthropic", "google"):
        raise ValueError(
            f"Провайдер '{provider_id}' не предоставляет endpoint /models. "
            f"Модели нужно добавлять вручную."
        )

    # Стандартный OpenAI-совместимый
    headers = {"Content-Type": "application/json"}
    if defn.default_headers:
        headers.update(defn.default_headers)
    if defn.requires_auth and not is_local:
        api_key = get_api_key(provider_id)
        if not api_key:
            raise ValueError(f"API ключ для '{provider_id}' не установлен")
        prefix = (defn.auth_prefix + " ") if defn.auth_prefix else ""
        headers[defn.auth_header or "Authorization"] = f"{prefix}{api_key}"

    try:
        return await _fetch_openai_compatible(base_url, headers, timeout)
    except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as e:
        if is_local:
            raise ValueError(
                f"Local server not reachable at {base_url}. "
                f"Start the server (e.g. 'ollama serve' or LM Studio) and retry."
            ) from e
        raise


def discover_models(provider_id: str) -> list[dict]:
    """Синхронная обёртка. Возвращает список dict-моделей.

    Если уже есть работающий event loop в текущем потоке — гоняем корутину
    в отдельном потоке (asyncio.run нельзя вызывать из работающего loop),
    иначе просто asyncio.run.
    """
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None

    if running is not None:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, _discover_async(provider_id))
            return future.result(timeout=60)

    return asyncio.run(_discover_async(provider_id))


def sync_models(provider_id: str, *, replace: bool = False) -> dict[str, Any]:
    """Обновляет модели провайдера из удалённого API.

    replace=False: добавляет новые, не трогает существующие (сохраняет ручные правки).
    replace=True: полная замена — удаляет всё и пишет заново.

    Возвращает: {"added": [ids], "kept": [ids], "removed": [ids], "total": n}.
    """
    defn = get_definition(provider_id)
    if defn is None:
        raise KeyError(f"Provider '{provider_id}' not found")

    discovered = discover_models(provider_id)
    discovered_ids = {m["id"] for m in discovered}
    existing_ids = {m.id for m in defn.models}

    added: list[str] = []
    kept: list[str] = []
    removed: list[str] = []

    preserve_prices = provider_id in _PRESERVE_PRICES_PROVIDERS

    if replace:
        for old_id in existing_ids:
            remove_model_from_provider(provider_id, old_id)
            removed.append(old_id)
        for m in discovered:
            add_model_to_provider(
                provider_id, m["id"], m["display_name"],
                m["context_window"], m["input_price"], m["output_price"],
            )
            added.append(m["id"])
    else:
        existing_by_id = {m.id: m for m in defn.models}
        for m in discovered:
            if m["id"] in existing_by_id:
                kept.append(m["id"])
                continue
            # Если для провайдера фиксированы цены — для НОВЫХ моделей ставим 0.0,
            # пользователь сам пропишет если нужно
            in_price = 0.0 if preserve_prices else m["input_price"]
            out_price = 0.0 if preserve_prices else m["output_price"]
            add_model_to_provider(
                provider_id, m["id"], m["display_name"],
                m["context_window"], in_price, out_price,
            )
            added.append(m["id"])

    reload_providers()
    logger.info(
        f"Model sync for {provider_id}: added={len(added)}, kept={len(kept)}, removed={len(removed)}"
    )
    return {
        "added": added,
        "kept": kept,
        "removed": removed,
        "total": len(discovered_ids),
    }