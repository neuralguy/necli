"""Dataclasses для описания API-провайдера."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ApiModelInfo:
    """Описание одной модели у провайдера."""
    id: str               # model id для API (e.g. "gpt-4o")
    display_name: str     # имя для UI (e.g. "GPT-4o")
    context_window: int = 128_000
    input_price: float = 0.0   # per 1M tokens
    output_price: float = 0.0  # per 1M tokens


@dataclass
class ApiProviderDefinition:
    """Полное описание API-провайдера из JSON."""
    id: str
    name: str
    type: str                    # "openai_compatible" | "anthropic" | "google" | "custom"
    base_url: str                # e.g. "https://api.openai.com/v1"
    api_format: str = "openai"   # "openai" | "anthropic" | "google" | "custom"
    models: list[ApiModelInfo] = field(default_factory=list)
    default_model: str = ""
    default_headers: dict[str, str] = field(default_factory=dict)
    requires_auth: bool = True
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer"
    max_retries: int = 3
    timeout: int = 120
    proxy: str = ""
    extra: dict = field(default_factory=dict)
    enabled: bool = True

    def get_model_info(self, model_id: str) -> Optional[ApiModelInfo]:
        for m in self.models:
            if m.id == model_id or m.display_name == model_id:
                return m
        return None

    def list_model_ids(self) -> list[str]:
        return [m.id for m in self.models]

    def list_display_names(self) -> list[str]:
        return [m.display_name for m in self.models]
