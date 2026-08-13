"""Small synchronisation helpers shared by mutable configuration modules."""

from __future__ import annotations

from collections.abc import Callable, MutableMapping, Sequence
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")


def synchronized(lock: Any) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Return a decorator that serialises calls with the supplied re-entrant lock."""

    def decorate(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with lock:
                return func(*args, **kwargs)

        return wrapper

    return decorate


def set_enabled(items: Sequence[MutableMapping[str, Any]], item_id: str, enabled: bool) -> bool:
    """Set an item's enabled flag, returning whether an item with the id existed."""
    for item in items:
        if item.get("id") == item_id:
            item["enabled"] = bool(enabled)
            return True
    return False
