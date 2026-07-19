"""
apis -- система API-провайдеров.

Позволяет подключать LLM через прямые API вместо браузера.
Каждый провайдер описан JSON-определением (URL, модели, формат)
и классом-провайдером (наследник BaseProvider или LangChain wrapper).

Публичный API:
    from apis import get_provider, list_providers, list_api_models
    from apis import add_api_config, remove_api_config
"""

from apis.config import (
    add_api_config,
    get_api_config,
    get_api_key,
    list_api_configs,
    remove_api_config,
    set_api_key,
)
from apis.registry import (
    get_provider,
    list_api_models,
    list_providers,
    reload_providers,
)

__all__ = [
    "add_api_config",
    "get_api_config",
    "get_api_key",
    "get_provider",
    "list_api_configs",
    "list_api_models",
    "list_providers",
    "reload_providers",
    "remove_api_config",
    "set_api_key",
]
