"""Подсчёт токенов для каждой модели.

Стратегия:
  - OpenAI / o3: tiktoken с encoding o200k_base — точный подсчёт
  - Claude:      tiktoken cl100k_base — близкая аппроксимация (~95% точность,
                 Claude использует похожий BPE)
  - Gemini:      эвристика на основе SentencePiece характеристик
                 (~4 символа/токен для латиницы, ~1.5 для CJK/кириллицы)
  - Grok:        tiktoken o200k_base — Grok использует аналогичный BPE

Для точного подсчёта Claude и Gemini нужен API key соответствующего
провайдера. Здесь используется офлайн-подсчёт без внешних зависимостей
кроме tiktoken.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# ВНИМАНИЕ: _MODEL_ENCODING_MAP / _MODEL_ALIAS_MAP связаны с display name из
# models.py — при добавлении/переименовании модели там нужно вручную обновить
# и эти карты (автосинхронизации нет).
_MODEL_ENCODING_MAP: dict[str, str] = {
    # OpenAI GPT-5 family — все на o200k_base
    "GPT-5.4":           "o200k_base",
    "GPT-5.4 Mini":      "o200k_base",
    "GPT-5.4 Nano":      "o200k_base",
    "GPT-5.4 Pro":       "o200k_base",
    "GPT-5.2 Pro":       "o200k_base",
    "GPT-5.5":           "o200k_base",
    "o3-pro":            "o200k_base",

    # Anthropic Claude — cl100k_base как ближайшее приближение.
    # Claude использует собственный BPE, но cl100k_base даёт ~95% точность
    # на английском и ~90% на кириллице.
    "Claude Opus 4.6":   "cl100k_base",
    "Claude Opus 4.7":   "cl100k_base",
    "Claude Sonnet 4.6": "cl100k_base",
    "Claude Haiku 4.5":  "cl100k_base",

    # Gemini — SentencePiece, обрабатывается через _is_gemini_model()
    # Записи здесь не нужны, но добавим для явности маппинга

    # Grok — использует BPE, o200k_base как приближение
    "Grok 4.20 Reasoning": "o200k_base",
    "Grok 4.20":            "o200k_base",
}


def _is_gemini_model(model: str) -> bool:
    """Определяет Gemini по имени вместо хардкода набора моделей."""
    return model.lower().startswith("gemini")


@lru_cache(maxsize=4)
def _get_encoding(encoding_name: str):
    """Загружает и кеширует tiktoken encoding."""
    try:
        import tiktoken
        return tiktoken.get_encoding(encoding_name)
    except Exception as e:
        logger.warning("tiktoken encoding %s недоступен: %s", encoding_name, e)
        return None


def _count_tiktoken(text: str, encoding_name: str) -> Optional[int]:
    """Считает токены через tiktoken. Возвращает None если недоступен."""
    enc = _get_encoding(encoding_name)
    if enc is None:
        return None
    try:
        return len(enc.encode(text))
    except Exception as e:
        logger.warning("tiktoken encode error: %s", e)
        return None


def _count_gemini_heuristic(text: str) -> int:
    """
    Эвристика для Gemini (SentencePiece-based).

    Gemini использует SentencePiece tokenizer, аналогичный Gemma.
    Характеристики:
      - Латиница: ~4 символа = 1 токен (как BPE)
      - Кириллица/CJK: ~1.5-2 символа = 1 токен (SentencePiece более
        гранулярный для non-Latin)
      - Пробелы считаются частью следующего токена (▁ prefix)
      - Числа: каждая цифра часто отдельный токен
    """
    if not text:
        return 0

    tokens = 0.0
    for ch in text:
        code = ord(ch)
        if code <= 127:
            # ASCII: латиница, цифры, пунктуация
            if ch.isdigit():
                tokens += 0.7  # цифры чаще отдельные токены
            elif ch.isspace():
                tokens += 0.1  # пробелы объединяются с next token
            else:
                tokens += 0.25  # ~4 символа = 1 токен
        elif 0x0400 <= code <= 0x04FF:
            # Кириллица
            tokens += 0.6  # ~1.7 символа = 1 токен
        elif 0x4E00 <= code <= 0x9FFF:
            # CJK
            tokens += 0.7  # ~1.4 символа = 1 токен
        else:
            # Прочий Unicode
            tokens += 0.5

    return max(1, int(tokens))


def _fallback_estimate(text: str) -> int:
    """
    Запасная эвристика если tiktoken недоступен.
    ~4 ASCII символа = 1 токен, ~2 кириллицы = 1 токен.
    """
    if not text:
        return 0

    non_ascii = sum(1 for c in text if ord(c) > 127)
    ascii_chars = len(text) - non_ascii

    estimate = int(ascii_chars / 4.0 + non_ascii / 2.0)
    return max(1, estimate)


def _normalize_model_id(model: str) -> str:
    """Убирает пробелы/дефисы/подчёркивания/точки и приводит к lower."""
    if not model:
        return ""
    return (
        model.lower()
        .replace(" ", "")
        .replace("-", "")
        .replace("_", "")
        .replace(".", "")
    )


# Алиасы провайдерских id → канонический display name из _MODEL_ENCODING_MAP.
# Нужен потому что API-провайдеры передают модель как 'claude-opus-4-7' и т.п.,
# а энкодинг и множитель привязаны к display name 'Claude Opus 4.7'.
# Ключи — нормализованные через _normalize_model_id формы.
_MODEL_ALIAS_MAP: dict[str, str] = {
    "claudeopus47":    "Claude Opus 4.7",
    "claudeopus46":    "Claude Opus 4.6",
    "claudesonnet46":  "Claude Sonnet 4.6",
    "claudehaiku45":   "Claude Haiku 4.5",
}


def _resolve_model_name(model: str) -> str:
    """Возвращает каноническое display name, если для model нашёлся алиас."""
    if not model:
        return model
    if model in _MODEL_ENCODING_MAP:
        return model
    norm = _normalize_model_id(model)
    return _MODEL_ALIAS_MAP.get(norm, model)


def _is_opus_47(model: str) -> bool:
    """Определяет Claude Opus 4.7 по любому варианту имени/id.

    Систематический недосчёт ~1.2x против cl100k_base.
    """
    norm = _normalize_model_id(model)
    return norm == "claudeopus47" or norm.endswith("opus47")


# Множитель для моделей, чей реальный токенайзер сильно отличается
# от используемой аппроксимации. Для Claude Opus 4.7 наблюдаем
# систематический недосчёт ~1.2x против cl100k_base.
_TOKEN_MULTIPLIERS: list[tuple[Callable, float]] = [
    (_is_opus_47, 1.2),
]


def _apply_multiplier(model: str, tokens: int) -> int:
    for predicate, mult in _TOKEN_MULTIPLIERS:
        if predicate(model):
            return int(tokens * mult)
    return tokens


def count_tokens(text: str, model: str = "") -> int:
    """
    Считает количество токенов в тексте для указанной модели.

    Для OpenAI/o3/Grok — точный подсчёт через tiktoken.
    Для Claude — аппроксимация через cl100k_base (~95% точность).
    Для Gemini — SentencePiece эвристика.
    Для неизвестных моделей — общая эвристика.

    Для отдельных моделей (см. _TOKEN_MULTIPLIERS) к результату
    применяется поправочный коэффициент.

    Возвращает целое число токенов (минимум 1 для непустого текста).
    """
    if not text:
        return 0

    # Алиасы id → display name (claude-opus-4-7 → Claude Opus 4.7),
    # чтобы и энкодинг, и множитель применялись одинаково.
    model = _resolve_model_name(model)

    # 1. Tiktoken-based (OpenAI, Claude, Grok)
    encoding_name = _MODEL_ENCODING_MAP.get(model)
    if encoding_name:
        result = _count_tiktoken(text, encoding_name)
        if result is None:
            result = _fallback_estimate(text)
        return _apply_multiplier(model, result)

    # 2. Gemini — SentencePiece heuristic
    if _is_gemini_model(model):
        return _apply_multiplier(model, _count_gemini_heuristic(text))

    # 3. Неизвестная модель — пробуем o200k_base, иначе эвристика
    result = _count_tiktoken(text, "o200k_base")
    if result is None:
        result = _fallback_estimate(text)
    return _apply_multiplier(model, result)


def estimate_tokens(text: str) -> int:
    """
    Обратно-совместимая функция (без модели).
    Использует o200k_base или fallback.
    """
    return count_tokens(text, model="")

