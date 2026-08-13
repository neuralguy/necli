from __future__ import annotations

import copy
import json
import logging
import numbers
import threading
from typing import TypeVar, overload

from ._atomic import atomic_write_json
from .paths import CONFIG_FILE

logger = logging.getLogger(__name__)

T = TypeVar("T")

_DEFAULT_CONFIG: dict[str, object] = {
    "model": "Claude Opus 4.6",
    "response_timeout": 180,
    "api_providers": [],
    "api_keys": {},
    "active_api": "",
    "active_api_model": "",
    "helper_provider": "",
    "helper_model": "",
    "image_provider": "",
    "image_model": "",
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_enabled": False,
    "think_enabled": False,
    "tool_format_force_native": True,
    "disabled_tools": [],
    # Авто-резюм агента при завершении фоновой shell-задачи: если задача
    # завершилась, пока агент ждёт ввода пользователя, он сам продолжит работу
    # с её результатом (не прерывая пользователя, если тот печатает).
    "background_autoresume": True,
    "temperature": 0.7,
    "max_tokens": 0,
    "reasoning_effort": "",
    "thinking": False,
    # 0 = trust Rich/wcwidth (emoji = 2 cells). 1 = принудительно считать emoji
    # как 1 cell — если в твоём терминале/шрифте emoji рендерятся узкими и
    # правая граница панелей съезжает влево. См. ui/_emoji_width.py.
    "emoji_width": 0,
    "language": "en",
    # Глобальный прокси для всех исходящих запросов к API-провайдерам.
    # Поддерживаются схемы http://, https://, socks5://, socks5h://
    # (с опциональным user:pass@). Пустая строка = без прокси.
    # Используется, если у конкретного провайдера не задан свой proxy.
    "proxy": "",
    # Autoprune — режим управления контекстом для провайдеров БЕЗ prompt
    # caching. Активируется автоматически когда у активного провайдера
    # выключен prompt cache (cache-off). Включает: прунинг старых read/tool
    # выводов, дедуп чтений файлов по диапазонам, сворачивание tool-выводов
    # старше N раундов, авто-сжатие истории каждые N раундов / при 200k токенов.
    # Каждый пункт можно вкл/выкл отдельно через /autoprune.
    "autoprune_file_dedup": True,
    "autoprune_tool_folding": True,
    "autoprune_round_compression": True,
    "autoprune_safety_compress": True,
    "autoprune_compress_every_rounds": 10,
    "autoprune_compress_at_tokens": 200_000,
    "autoprune_tool_fold_rounds": 5,
}

_config_cache: dict | None = None
_CONFIG_LOCK = threading.RLock()

# Инкрементируется при любом изменении конфига. Позволяет внешним кэшам
# (например agent/think.py:_think_enabled) дешёво понять, что значение
# могло измениться, без re-чтения JSON-файла.
_settings_version: int = 0


def _load_config() -> dict:
    global _config_cache
    with _CONFIG_LOCK:
        if _config_cache is not None:
            return _config_cache

        data: dict = {}
        if CONFIG_FILE.exists():
            try:
                loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    logger.error(
                        "config load failed for %s: root must be a JSON object", CONFIG_FILE
                    )
                    data = {}
                else:
                    data = loaded
            except (json.JSONDecodeError, OSError) as e:
                logger.error("config load failed for %s: %s", CONFIG_FILE, e)
                data = {}

        merged = copy.deepcopy(_DEFAULT_CONFIG)
        merged.update(data)
        _config_cache = merged
        return merged


def _save_config(data: dict) -> None:
    global _config_cache, _settings_version
    with _CONFIG_LOCK:
        try:
            atomic_write_json(CONFIG_FILE, data)
            # Только после успешного replace публикуем новый snapshot. Не
            # сохраняем ссылку на объект caller'а, иначе его последующая
            # мутация снова обойдёт persistence.
            _config_cache = copy.deepcopy(data)
            _settings_version += 1
        except OSError as e:
            logger.error("config save failed for %s: %s", CONFIG_FILE, e)
            raise


@overload
def get(key: str) -> object: ...


@overload
def get(key: str, default: T) -> T: ...


def get(key: str, default: object = None) -> object:
    with _CONFIG_LOCK:
        value = _load_config().get(key, default)
        if default is not None and value is not None and not _type_ok(value, default):
            value = default
        # Не выдаём наружу live list/dict из глобального config cache.
        return copy.deepcopy(value) if isinstance(value, (dict, list, set)) else value


def _type_ok(value: object, default: object) -> bool:
    """Совместим ли тип хранимого value с типом default.

    bool трактуем строго (bool — подкласс int): bool принимаем только для
    bool-default, и не принимаем int/float как bool. Для числовых default
    (int/float, но не bool) принимаем любое не-bool число (int↔float).
    Остальные типы — обычная проверка isinstance.
    """
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, numbers.Number):
        return isinstance(value, numbers.Number) and not isinstance(value, bool)
    return isinstance(value, type(default))


def set_value(key: str, value: object) -> None:
    with _CONFIG_LOCK:
        # Copy-on-write: не мутируем опубликованный cache до успешной записи.
        cfg = copy.deepcopy(_load_config())
        cfg[key] = copy.deepcopy(value)
        _save_config(cfg)
    logger.debug("config set: %s", key)
