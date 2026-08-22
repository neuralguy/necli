"""ChatGPT subscription rate-limit lookup and in-process cache."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

import httpx

from apis.chatgpt_auth import get_chatgpt_access
from logger import logger

CHATGPT_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
_WEEK_SECONDS = 7 * 24 * 60 * 60
_CACHE_TTL = 30.0

_snapshot: dict[str, Any] | None = None
_fetched_at = 0.0
_refresh_lock = asyncio.Lock()
_background_tasks: set[asyncio.Task] = set()
_listeners: set[Callable[[dict[str, Any]], None]] = set()


def parse_chatgpt_weekly_usage(data: Any) -> dict[str, Any] | None:
    """Extract the weekly window without assuming primary/secondary placement."""
    if not isinstance(data, dict):
        return None
    rate_limit = data.get("rate_limit")
    if not isinstance(rate_limit, dict):
        return None

    windows: list[dict[str, Any]] = []
    for name in ("primary_window", "secondary_window"):
        window = rate_limit.get(name)
        if not isinstance(window, dict):
            continue
        try:
            seconds = int(window.get("limit_window_seconds") or 0)
            used = float(window["used_percent"])
        except (KeyError, TypeError, ValueError):
            continue
        if seconds >= _WEEK_SECONDS * 6 / 7:
            windows.append(
                {**window, "limit_window_seconds": seconds, "used_percent": used}
            )

    if not windows:
        secondary = rate_limit.get("secondary_window")
        if isinstance(secondary, dict) and not secondary.get("limit_window_seconds"):
            try:
                windows.append(
                    {**secondary, "used_percent": float(secondary["used_percent"])}
                )
            except (KeyError, TypeError, ValueError):
                pass
    if not windows:
        return None

    weekly = max(windows, key=lambda item: int(item.get("limit_window_seconds") or 0))
    used = min(100.0, max(0.0, float(weekly["used_percent"])))
    return {
        "used_percent": used,
        "remaining_percent": 100.0 - used,
        "reset_at": weekly.get("reset_at"),
        "window_seconds": int(weekly.get("limit_window_seconds") or 0),
        "plan_type": data.get("plan_type"),
    }


def get_cached_chatgpt_usage() -> dict[str, Any] | None:
    return dict(_snapshot) if _snapshot is not None else None


def clear_chatgpt_usage_cache() -> None:
    global _fetched_at, _snapshot

    _snapshot = None
    _fetched_at = 0.0


def subscribe_chatgpt_usage(
    listener: Callable[[dict[str, Any]], None],
) -> Callable[[], None]:
    """Notify UI consumers after a fresh subscription-limit snapshot."""
    _listeners.add(listener)

    def unsubscribe() -> None:
        _listeners.discard(listener)

    return unsubscribe


def _notify_listeners(snapshot: dict[str, Any]) -> None:
    for listener in tuple(_listeners):
        try:
            listener(dict(snapshot))
        except Exception as exc:
            logger.debug("ChatGPT usage listener failed: {}", exc)


async def refresh_chatgpt_usage(
    *, force: bool = False, proxy: str = ""
) -> dict[str, Any] | None:
    """Refresh usage best-effort; failures never break provider requests or menus."""
    global _fetched_at, _snapshot

    now = time.monotonic()
    if _snapshot is not None and not force and now - _fetched_at < _CACHE_TTL:
        return dict(_snapshot)

    async with _refresh_lock:
        now = time.monotonic()
        if _snapshot is not None and now - _fetched_at < (2.0 if force else _CACHE_TTL):
            return dict(_snapshot)
        try:
            kwargs: dict[str, Any] = {"timeout": httpx.Timeout(10.0, connect=5.0)}
            if proxy:
                kwargs["proxy"] = proxy
            async with httpx.AsyncClient(**kwargs) as client:
                response: httpx.Response | None = None
                for refresh_token in (False, True):
                    token, account_id = await get_chatgpt_access(
                        force_refresh=refresh_token
                    )
                    headers = {
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Originator": "necli",
                        "User-Agent": "necli/1.0",
                    }
                    if account_id:
                        headers["ChatGPT-Account-Id"] = account_id
                    response = await client.get(CHATGPT_USAGE_URL, headers=headers)
                    if response.status_code != 401 or refresh_token:
                        break
                assert response is not None
                response.raise_for_status()
                parsed = parse_chatgpt_weekly_usage(response.json())
                if parsed is not None:
                    _snapshot = parsed
                    _fetched_at = time.monotonic()
                    _notify_listeners(parsed)
        except Exception as exc:
            logger.debug("ChatGPT usage refresh failed: {}", exc)
        return dict(_snapshot) if _snapshot is not None else None


def schedule_chatgpt_usage_refresh(*, proxy: str = "") -> None:
    """Refresh after a response without adding latency to streamed output."""
    task = asyncio.create_task(refresh_chatgpt_usage(force=True, proxy=proxy))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def refresh_connected_chatgpt_usage(
    *, force: bool = False
) -> dict[str, Any] | None:
    """Refresh the connected ChatGPT provider without duplicating UI lookup logic."""
    from apis.chatgpt_auth import chatgpt_auth_status
    from apis.registry import get_definitions

    if not chatgpt_auth_status().get("authenticated"):
        return None
    definition = next(
        (item for item in get_definitions().values() if item.type == "chatgpt"),
        None,
    )
    if definition is None:
        return None
    return await refresh_chatgpt_usage(force=force, proxy=definition.proxy)


def schedule_connected_chatgpt_usage_refresh(*, force: bool = False) -> None:
    """Refresh the connected ChatGPT limit without blocking the current UI."""
    task = asyncio.create_task(
        refresh_connected_chatgpt_usage(force=force),
        name="chatgpt-usage-refresh",
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
