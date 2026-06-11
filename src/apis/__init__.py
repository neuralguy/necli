"""
apis -- система API-провайдеров.

Позволяет подключать LLM через прямые API вместо браузера.
Каждый провайдер описан JSON-определением (URL, модели, формат)
и классом-провайдером (наследник BaseProvider или LangChain wrapper).

Публичный API:
    from apis import get_provider, list_providers, list_api_models
    from apis import add_api_config, remove_api_config
"""

from apis.registry import (
    get_provider,
    list_providers,
    list_api_models,
    reload_providers,
)
from apis.config import (
    add_api_config,
    remove_api_config,
    list_api_configs,
    get_api_config,
    set_api_key,
    get_api_key,
)

__all__ = [
    "get_provider",
    "list_providers",
    "list_api_models",
    "reload_providers",
    "add_api_config",
    "remove_api_config",
    "list_api_configs",
    "get_api_config",
    "set_api_key",
    "get_api_key",
]
