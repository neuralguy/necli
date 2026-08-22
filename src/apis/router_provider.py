"""Ordered failover across models from multiple API providers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from apis.base import BaseProvider
from apis.messages import AIMessage, AIMessageChunk, BaseMessage
from apis.registry import get_provider
from logger import logger


class RouterProvider(BaseProvider):
    def __init__(self, router_id: str, routes: list[dict], **provider_kwargs) -> None:
        super().__init__(model=router_id)
        self.router_id = router_id
        self.routes = [dict(route) for route in routes]
        self.provider_kwargs = dict(provider_kwargs)
        self._provider_name = "Routers"
        self._provider_id = "routers"
        self._active_llm: BaseProvider | None = None

    def _candidate(self, route: dict) -> BaseProvider:
        return get_provider(
            route["provider_id"],
            route["model_id"],
            **self.provider_kwargs,
        )

    @staticmethod
    def _route_label(route: dict) -> str:
        return f"{route['provider_id']}/{route['model_id']}"

    async def ainvoke(self, messages: list[BaseMessage], **kwargs) -> AIMessage:
        errors: list[str] = []
        for index, route in enumerate(self.routes):
            try:
                llm = self._candidate(route)
                result = await llm.ainvoke(messages, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                errors.append(
                    f"{self._route_label(route)}: {type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "Router {} route {}/{} failed ({}), switching to next: {}",
                    self.router_id,
                    index + 1,
                    len(self.routes),
                    self._route_label(route),
                    exc,
                )
                continue
            self._active_llm = llm
            return result
        raise RuntimeError(self._failure_message(errors))

    async def astream(
        self,
        messages: list[BaseMessage],
        **kwargs,
    ) -> AsyncIterator[AIMessageChunk]:
        errors: list[str] = []
        for index, route in enumerate(self.routes):
            yielded = False
            try:
                llm = self._candidate(route)
                async for chunk in llm.astream(messages, **kwargs):
                    yielded = True
                    self._active_llm = llm
                    yield chunk
                self._active_llm = llm
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if yielded:
                    raise
                errors.append(
                    f"{self._route_label(route)}: {type(exc).__name__}: {exc}"
                )
                logger.warning(
                    "Router {} route {}/{} failed ({}), switching to next: {}",
                    self.router_id,
                    index + 1,
                    len(self.routes),
                    self._route_label(route),
                    exc,
                )
        raise RuntimeError(self._failure_message(errors))

    def _failure_message(self, errors: list[str]) -> str:
        detail = "; ".join(errors) if errors else "no usable routes"
        return f"Router '{self.router_id}' exhausted all {len(self.routes)} route(s): {detail}"

    def spend_usage(self, usage: dict) -> None:
        if self._active_llm is not None:
            self._active_llm.spend_usage(usage)

    def _supports_anthropic_cache_control(self) -> bool:
        try:
            llm = self._active_llm or (
                self._candidate(self.routes[0]) if self.routes else None
            )
            return bool(llm and llm._supports_anthropic_cache_control())
        except Exception:
            return False
