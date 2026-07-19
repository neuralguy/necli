from .constants import (  # noqa: F401
    IGNORE_DIRS,
    READ_ONLY_TOOLS,
    RESPONSE_TIMEOUT,
    TARGET_MODEL,
    is_ignored_dir,
)
from .i18n import LANG_DISPLAY, SUPPORTED_LANGS, get_lang, set_lang, t  # noqa: F401
from .paths import (  # noqa: F401
    BASE_DIR,
    CONFIG_FILE,
    SESSIONS_DIR,
    SKILLS_DIR,
    UI_FILE,
    ensure_dirs,
)
from .settings import get, get_all, reset, set_value  # noqa: F401
from .ui import ui  # noqa: F401

ensure_dirs()


def get_active_api() -> str:
    return get("active_api", "")


def set_active_api(provider_id: str) -> None:
    set_value("active_api", provider_id)


def get_active_api_model() -> str:
    return get("active_api_model", "")


def set_active_api_model(model_id: str) -> None:
    set_value("active_api_model", model_id)


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

def get_telegram_show_thinking() -> bool:
    return bool(get("telegram_show_thinking", False))

def set_telegram_show_thinking(enabled: bool) -> None:
    set_value("telegram_show_thinking", bool(enabled))

def get_telegram_tool_io() -> bool:
    return bool(get("telegram_tool_io", True))

def set_telegram_tool_io(enabled: bool) -> None:
    set_value("telegram_tool_io", bool(enabled))

def get_telegram_assistant_header() -> bool:
    return bool(get("telegram_assistant_header", False))

def set_telegram_assistant_header(enabled: bool) -> None:
    set_value("telegram_assistant_header", bool(enabled))

def get_telegram_approve() -> bool:
    return bool(get("telegram_approve", False))

def set_telegram_approve(enabled: bool) -> None:
    set_value("telegram_approve", bool(enabled))
