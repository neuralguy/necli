"""Desktop-уведомления о ходе агента.

Linux:   notify-send (libnotify)
macOS:   osascript
Windows: PowerShell toast через Windows Runtime API (без сторонних модулей)

Условия отправки (см. notify_turn_finished): уведомления включены в
/settings, ход длился дольше MIN_TURN_SECONDS, терминал не в фокусе.
Отправка идёт фоновым потоком-демоном — subprocess с таймаутом никогда не
блокирует UI-цикл; любая ошибка молчит и логируется в debug.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

# Ход короче минуты не уведомляем: быстрый ответ не стоит переключения
# контекста — пользователь, скорее всего, ещё смотрит в терминал.
MIN_TURN_SECONDS = 60.0

# Poll-инструмент в цикле агента может завершаться часто подряд — режем
# дубли, чтобы рабочий стол не залило одинаковыми тостами.
_RENOTIFY_GAP = 5.0

_APP_NAME = "necli"

_last_notify_monotonic = 0.0

# Запоминаем ОРИГИНАЛЬНЫЙ DISPLAY при импорте, ДО возможной подмены:
# notify-send должен идти к реальному рабочему столу, а не к Xvfb :99.
_ORIGINAL_DISPLAY: str | None = os.environ.get("DISPLAY")
_ORIGINAL_WAYLAND_DISPLAY: str | None = os.environ.get("WAYLAND_DISPLAY")


def _desktop_env() -> dict:
    """Окружение с реальными DISPLAY/WAYLAND_DISPLAY для notify-send."""
    env = os.environ.copy()
    if _ORIGINAL_DISPLAY is not None:
        env["DISPLAY"] = _ORIGINAL_DISPLAY
    elif env.get("DISPLAY") == ":99":
        env["DISPLAY"] = ":0"
    if _ORIGINAL_WAYLAND_DISPLAY is not None:
        env["WAYLAND_DISPLAY"] = _ORIGINAL_WAYLAND_DISPLAY
    return env


def _ps_escape(text: str) -> str:
    """Экранирование строки для вставки в PowerShell-стринг в single quotes."""
    return text.replace("'", "''")


def _windows_toast_command(title: str, body: str) -> list[str]:
    xml = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{title}</text><text>{body}</text>"
        "</binding></visual></toast>"
    )
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType = WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType = WindowsRuntime] | Out-Null;"
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        f"$xml.LoadXml('{_ps_escape(xml)}');"
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml;"
        "$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe';"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId)"
        ".Show($toast)"
    )
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        script,
    ]


def send_desktop_notification(title: str, body: str) -> bool:
    """Отправить системное уведомление. Блокирующая — звать из потока.

    Возвращает True, если уведомление ушло (найдена утилита и код возврата 0).
    """
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                _windows_toast_command(title, body),
                capture_output=True,
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        if sys.platform == "darwin":
            if shutil.which("osascript") is None:
                return False
            quoted = body.replace("\\", "\\\\").replace('"', '\\"')
            titled = title.replace("\\", "\\\\").replace('"', '\\"')
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    f'display notification "{quoted}" with title "{titled}"',
                ],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return result.returncode == 0
        if shutil.which("notify-send") is None:
            return False
        result = subprocess.run(
            [
                "notify-send",
                "-a",
                _APP_NAME,
                "-t",
                "5000",
                title,
                body,
            ],
            capture_output=True,
            timeout=5,
            check=False,
            env=_desktop_env(),
        )
        return result.returncode == 0
    except Exception:
        logger.debug("desktop notification failed", exc_info=True)
        return False


def _send_in_thread(title: str, body: str) -> None:
    threading.Thread(
        target=send_desktop_notification,
        args=(title, body),
        daemon=True,
        name="desktop-notification",
    ).start()


def notifications_enabled() -> bool:
    try:
        from config.settings import get as config_get

        return bool(config_get("notifications_enabled", True))
    except Exception:
        logger.debug("notifications_enabled read failed", exc_info=True)
        return False


def _passes_gates(elapsed: float | None) -> bool:
    if not notifications_enabled():
        return False
    if elapsed is None or elapsed < MIN_TURN_SECONDS:
        return False
    from ui.focus import is_terminal_focused

    # None (терминал не отчитывается о фокусе) трактуем как «не в фокусе»:
    # шум срезает фильтр длительности, а неугаданное «в фокусе» молчанием.
    if is_terminal_focused():
        return False
    global _last_notify_monotonic
    now = time.monotonic()
    if now - _last_notify_monotonic < _RENOTIFY_GAP:
        return False
    _last_notify_monotonic = now
    return True


def notify_turn_finished(
    elapsed: float | None,
    *,
    cancelled: bool = False,
    poll: bool = False,
) -> bool:
    """Уведомить о завершении хода (или poll-ожидания), если проходят фильтры.

    elapsed — длительность хода в секундах; cancelled — ход прерван по Esc;
    poll — завершилось poll-ожидание (агент продолжает работу). Возвращает
    True, если уведомление запланировано к отправке.
    """
    try:
        if not _passes_gates(elapsed):
            return False
        from config.i18n import format_duration, t

        if poll:
            body = t("notifications.poll_body", time=format_duration(elapsed or 0.0))
        elif cancelled:
            body = t("notifications.cancelled_body", time=format_duration(elapsed or 0.0))
        else:
            body = t("notifications.done_body", time=format_duration(elapsed or 0.0))
        _send_in_thread(_APP_NAME, body)
        return True
    except Exception:
        logger.debug("turn notification skipped", exc_info=True)
        return False


def notify_test() -> None:
    """Пробное уведомление из /settings: фильтры не применяются."""
    from config.i18n import t

    _send_in_thread(_APP_NAME, t("notifications.test_body"))
