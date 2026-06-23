"""
Реестр моделей: canonical catalog (неизменяемый), цены, fuzzy-поиск.

Site-available модели (мутабельные) хранятся в sites/models.py.
"""

from __future__ import annotations

from dataclasses import dataclass

from logger import logger

@dataclass(frozen=True, slots=True)
class ModelPricing:
    input: float
    output: float

# Каноническое имя → display name в UI (неизменяемый каталог).
# NOTE: значения сейчас идентичны ключам (display == canonical) и нигде не
# читаются — каталог итерируется только по ключам (`for c in CANONICAL_MODELS`,
# `list_models`). Идентичные значения оставлены намеренно как точка расширения,
# если display-имя когда-нибудь разойдётся с каноническим ключом.
CANONICAL_MODELS: dict[str, str] = {
    "GPT-5.5":                 "GPT-5.5",
    "GPT-5.4":                 "GPT-5.4",
    "GPT-5.4 Mini":            "GPT-5.4 Mini",
    "Claude Opus 4.8":         "Claude Opus 4.8",
    "Claude Sonnet 4.6":       "Claude Sonnet 4.6",
    "Claude Haiku 4.5":        "Claude Haiku 4.5",
    "Gemini 3.1 Pro":          "Gemini 3.1 Pro",
    "Gemini 3.5 Flash":        "Gemini 3.5 Flash",
    "Gemini 3 Flash":          "Gemini 3 Flash",
    "Grok 4.3":                "Grok 4.3",
    "Grok 4.20":               "Grok 4.20",
    "Grok 4.1 Fast":           "Grok 4.1 Fast",
}

MODEL_PRICING: dict[str, ModelPricing] = {
    # OpenAI
    "GPT-5.5":                 ModelPricing(5.00,   30.00),
    "GPT-5.4":                 ModelPricing(2.50,   15.00),
    "GPT-5.4 Mini":            ModelPricing(0.75,   4.50),
    # Anthropic
    "Claude Opus 4.8":         ModelPricing(5.00,   25.00),
    "Claude Sonnet 4.6":       ModelPricing(3.00,   15.00),
    "Claude Haiku 4.5":        ModelPricing(1.00,   5.00),
    # Google Gemini
    "Gemini 3.1 Pro":          ModelPricing(2.00,   12.00),
    "Gemini 3.5 Flash":        ModelPricing(1.50,   9.00),
    "Gemini 3 Flash":          ModelPricing(0.50,   3.00),
    # xAI Grok
    "Grok 4.3":                ModelPricing(1.25,   2.50),
    "Grok 4.20":               ModelPricing(2.00,   6.00),
    "Grok 4.1 Fast":           ModelPricing(0.20,   0.50),
    # Groq (hosted open models)
    "Kimi K2":                 ModelPricing(0.15,   0.60),
}

_NO_PRICING = ModelPricing(0.0, 0.0)

_DEFAULT_CONTEXT_LIMIT = 200_000

def _lookup_api_context_window(model: str) -> int | None:
    if not model:
        return None
    try:
        from apis.config import list_api_configs
    except Exception:
        logger.debug("_lookup_api_context_window: import list_api_configs failed", exc_info=True)
        return None
    try:
        configs = list_api_configs()
    except Exception:
        logger.debug("_lookup_api_context_window: list_api_configs() failed", exc_info=True)
        return None
    target = model.strip().lower()
    for cfg in configs:
        for m in cfg.get("models") or []:
            mid = str(m.get("id", "")).strip().lower()
            mname = str(m.get("display_name", "")).strip().lower()
            if target == mid or (mname and target == mname):
                cw = m.get("context_window")
                if cw:
                    try:
                        return int(cw)
                    except (TypeError, ValueError):
                        return None
    return None

MODEL_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude", ("claude", "opus", "sonnet", "haiku")),
    ("grok", ("grok",)),
    ("gpt", ("gpt", "openai")),
    ("qwen", ("qwen",)),
    ("kimi", ("kimi", "moonshot")),
    ("gemini", ("gemini",)),
)


def model_group(name: str) -> str:
    low = (name or "").lower()
    for group, aliases in MODEL_GROUPS:
        if any(a in low for a in aliases):
            return group
    return "other"


def model_group_order(group: str) -> int:
    for i, (g, _a) in enumerate(MODEL_GROUPS):
        if g == group:
            return i
    return len(MODEL_GROUPS)


def get_context_limit(model: str) -> int:
    cw = _lookup_api_context_window(model)
    if cw and cw > 0:
        return cw
    return _DEFAULT_CONTEXT_LIMIT

def _normalize(s: str) -> str:
    return s.lower().replace(" ", "").replace("-", "").replace("_", "")

def resolve_model(name: str) -> str | None:
    if not name or not name.strip():
        return None

    query = name.strip()
    query_lower = query.lower()

    for canonical in CANONICAL_MODELS:
        if canonical.lower() == query_lower:
            return canonical

    matches = [c for c in CANONICAL_MODELS if query_lower in c.lower()]
    if len(matches) == 1:
        return matches[0]

    if not matches:
        matches = [c for c in CANONICAL_MODELS if c.lower() in query_lower]
        if len(matches) == 1:
            return matches[0]

    query_words = query_lower.split()
    word_matches = [
        c for c in CANONICAL_MODELS
        if all(w in c.lower() for w in query_words)
    ]
    if len(word_matches) == 1:
        return word_matches[0]
    if word_matches:
        matches = word_matches

    query_norm = _normalize(query)
    for canonical in CANONICAL_MODELS:
        if _normalize(canonical) == query_norm:
            return canonical

    norm_matches = [c for c in CANONICAL_MODELS if query_norm in _normalize(c)]
    if len(norm_matches) == 1:
        return norm_matches[0]

    all_matches = matches or norm_matches
    if all_matches:
        return min(all_matches, key=len)

    return None

def list_models() -> list[str]:
    return sorted(CANONICAL_MODELS.keys())

def _lookup_api_pricing(model: str) -> ModelPricing | None:
    """Ищет цены модели среди API-провайдеров (.data/apis.json).

    Сопоставление по model id и display_name (case-insensitive).
    """
    if not model:
        return None
    try:
        from apis.config import list_api_configs
    except Exception:
        logger.debug("_lookup_api_pricing: import list_api_configs failed", exc_info=True)
        return None
    try:
        configs = list_api_configs()
    except Exception:
        logger.debug("_lookup_api_pricing: list_api_configs() failed", exc_info=True)
        return None
    target = model.strip().lower()
    for cfg in configs:
        for m in cfg.get("models") or []:
            mid = str(m.get("id", "")).strip().lower()
            mname = str(m.get("display_name", "")).strip().lower()
            if target == mid or (mname and target == mname):
                inp = float(m.get("input_price", 0.0) or 0.0)
                out = float(m.get("output_price", 0.0) or 0.0)
                return ModelPricing(inp, out)
    return None

def get_pricing(model: str) -> tuple[float, float]:
    if model in MODEL_PRICING:
        p = MODEL_PRICING[model]
        return (p.input, p.output)
    api_p = _lookup_api_pricing(model)
    if api_p is not None:
        return (api_p.input, api_p.output)
    return (_NO_PRICING.input, _NO_PRICING.output)