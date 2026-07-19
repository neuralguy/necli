"""HTTP-провайдер на httpx через BaseProvider.

Используется для любого OpenAI-совместимого API без библиотечной SDK.
"""

from __future__ import annotations

from typing import Any

from apis.base import BaseProvider
from apis.config import get_api_credentials
from apis.models import ApiProviderDefinition
from logger import logger


class CustomHttpProvider(BaseProvider):
    """HTTP-провайдер с конфигурацией из ApiProviderDefinition."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._requires_auth: bool = True
        self._auth_header: str = "Authorization"
        self._auth_prefix: str = "Bearer"
        self._default_headers: dict[str, str] = {}
        self._extra_body: dict[str, Any] = {}

    def _get_api_key(self) -> str:
        return super()._get_api_key()

    def _get_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._default_headers:
            headers.update(self._default_headers)
        if self._requires_auth:
            key = self._get_api_key()
            if key:
                prefix = (self._auth_prefix + " ") if self._auth_prefix else ""
                headers[self._auth_header] = f"{prefix}{key}"
        return headers

    def _build_params(self, **kwargs: Any) -> dict[str, Any]:
        params = super()._build_params(**kwargs)
        if self._extra_body:
            for k, v in self._extra_body.items():
                params.setdefault(k, v)
        return params


def create_custom_provider(
    definition: ApiProviderDefinition,
    model_id: str,
    **kwargs: Any,
) -> CustomHttpProvider:
    """Создаёт CustomHttpProvider из определения."""
    model_info = definition.get_model_info(model_id)
    actual_model = model_info.id if model_info else model_id

    base_url = definition.base_url.rstrip("/")
    api_url = base_url
    if not api_url.endswith("/chat/completions"):
        api_url = base_url + "/chat/completions"

    provider = CustomHttpProvider(
        model=actual_model,
        temperature=kwargs.get("temperature", 0.7),
        max_tokens=kwargs.get("max_tokens"),
        timeout=definition.timeout or 300,
        max_retries=definition.max_retries or 3,
        reasoning_effort=kwargs.get("reasoning_effort"),
        thinking=kwargs.get("thinking"),
    )
    provider._api_url = api_url
    provider._provider_name = definition.name
    provider._proxy = definition.proxy
    provider._api_credentials = get_api_credentials(definition.id)
    provider._requires_auth = definition.requires_auth
    provider._auth_header = definition.auth_header or "Authorization"
    provider._auth_prefix = definition.auth_prefix or ""
    provider._default_headers = dict(definition.default_headers or {})

    # reasoning-параметры берём из definition.extra (per-provider в JSON-конфиге),
    # либо из per-model override extra.reasoning_models = {"<model_id>": "high"}.
    extra = definition.extra or {}
    provider._prompt_cache_mode = str(extra.get("prompt_cache", extra.get("prompt_caching", "auto")))
    extra_body = dict(extra.get("extra_body") or {})
    reasoning_models = extra.get("reasoning_models") or {}
    if actual_model in reasoning_models:
        extra_body["reasoning"] = reasoning_models[actual_model]
    if extra_body:
        provider._extra_body = extra_body

    logger.debug(f"Created custom provider: {definition.name} / {actual_model} @ {api_url}")
    return provider
