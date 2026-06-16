"""OpenAI-совместимый провайдер (на httpx).

Заменяет langchain-openai. Использует тот же CustomHttpProvider — формат
запросов идентичен (chat/completions). Различие лишь в дефолтных заголовках
и базовом URL, которые приходят из ApiProviderDefinition.
"""

from __future__ import annotations

from typing import Any

from apis.providers.custom_provider import CustomHttpProvider, create_custom_provider
from apis.models import ApiProviderDefinition
from apis.config import get_api_key
from logger import logger


def create_openai_provider(
    definition: ApiProviderDefinition,
    model_id: str,
    **kwargs: Any,
) -> CustomHttpProvider:
    """Создаёт OpenAI-совместимый HTTP провайдер."""
    api_key = get_api_key(definition.id)
    if not api_key and definition.requires_auth:
        raise ValueError(
            f"API key not set for provider '{definition.id}'. "
            "Use /api → provider → Set key."
        )

    provider = create_custom_provider(definition, model_id, **kwargs)
    logger.debug(f"Created OpenAI-compatible provider: {definition.name} / {model_id}")
    return provider