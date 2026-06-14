from .paths import BASE_DIR, SESSIONS_DIR, SKILLS_DIR, CONFIG_FILE, UI_FILE, ensure_dirs  # noqa: F401
from .ui import ui  # noqa: F401
from .settings import get, set_value, get_all, reset  # noqa: F401
from .i18n import t, get_lang, set_lang, SUPPORTED_LANGS, LANG_DISPLAY  # noqa: F401
from .constants import (  # noqa: F401
    RESPONSE_TIMEOUT,
    TARGET_MODEL,
    IGNORE_DIRS,
    READ_ONLY_TOOLS,
    is_ignored_dir,
)

ensure_dirs()


def get_active_api() -> str:
    return get("active_api", "")


def set_active_api(provider_id: str) -> None:
    set_value("active_api", provider_id)


def get_active_api_model() -> str:
    return get("active_api_model", "")


def set_active_api_model(model_id: str) -> None:
    set_value("active_api_model", model_id)


def is_api_mode() -> bool:
    """В API-only сборке всегда True (браузера нет)."""
    return True

def get_telegram_bot_token() -> str:
    return get("telegram_bot_token", "")

def set_telegram_bot_token(token: str) -> None:
    set_value("telegram_bot_token", token)

def get_telegram_chat_id() -> str:
    return get("telegram_chat_id", "")

def set_telegram_chat_id(chat_id: str) -> None:
    set_value("telegram_chat_id", chat_id)

def get_telegram_enabled() -> bool:
    return bool(get("telegram_enabled", False))

def set_telegram_enabled(enabled: bool) -> None:
    set_value("telegram_enabled", bool(enabled))


