"""Меню /notifications: переключатель desktop-уведомлений."""

from commands.menus._style import card_menu
from config.i18n import t
from config.settings import get as config_get
from config.settings import set_value as config_set


async def notifications_interactive() -> None:
    enabled = bool(config_get("notifications_enabled", True))
    items = [
        {"label": t("notifications.on"), "active": enabled},
        {"label": t("notifications.off"), "active": not enabled},
    ]
    items.append({"label": t("common.back")})

    choice = await card_menu(
        items,
        title=t("notifications.title"),
        current=0 if enabled else 1,
    )
    if choice is None or choice == 2:
        return

    config_set("notifications_enabled", choice == 0)
    if choice == 0:
        from ui.notifications import notify_test

        notify_test()
