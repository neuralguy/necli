from __future__ import annotations

import json
import logging
import numbers
import os
import tempfile
from typing import TypeVar, overload

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
    "telegram_bot_token": "",
    "telegram_chat_id": "",
    "telegram_enabled": False,
    "think_enabled": False,
    "tool_format_force_native": True,
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
}

_config_cache: dict | None = None

# Инкрементируется при любом изменении конфига. Позволяет внешним кэшам
# (например agent/think.py:_think_enabled) дешёво понять, что значение
# могло измениться, без re-чтения JSON-файла.
_settings_version: int = 0


def _load_config() -> dict:
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    data: dict = {}
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("config load failed for %s: %s", CONFIG_FILE, e)
            data = {}

    merged = {**_DEFAULT_CONFIG, **data}
    _config_cache = merged
    return merged


def _save_config(data: dict) -> None:
    global _config_cache, _settings_version
    _config_cache = data
    _settings_version += 1
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=CONFIG_FILE.parent,
            delete=False,
        ) as fh:
            fh.write(payload)
            tmp_name = fh.name
        os.replace(tmp_name, CONFIG_FILE)
    except OSError as e:
        logger.error("config save failed for %s: %s", CONFIG_FILE, e)
        raise


@overload
def get(key: str) -> object: ...


@overload
def get(key: str, default: T) -> T: ...


def get(key: str, default: object = None) -> object:
    value = _load_config().get(key, default)
    if default is not None and value is not None and not _type_ok(value, default):
        return default
    return value

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
    cfg = _load_config()
    cfg[key] = value
    _save_config(cfg)
    logger.debug("config set: %s", key)


def get_all() -> dict:
    return dict(_load_config())


def reset() -> None:
    _save_config(dict(_DEFAULT_CONFIG))
