"""MCP (Model Context Protocol) клиент.

Подключает MCP-серверы по stdio, извлекает их инструменты и
регистрирует их в TOOL_REGISTRY как обычные tools. Каждый инструмент
получает префикс `mcp__<server_id>__<tool_name>` чтобы избежать
конфликтов с встроенными.

Архитектура:
  - MCPServer: одна подключённая сессия (stdio process + ClientSession).
  - MCPManager: singleton, держит все активные сессии, фоновый event loop
    в отдельном потоке (т.к. TOOL_REGISTRY вызывается синхронно из
    разных мест, а mcp SDK — async).

Использование:
  init_mcp_from_config()           # при старте CLI
  list_mcp_tools()                  # список доступных
  shutdown_mcp()                    # при выходе
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from logger import logger
from tools.models import ToolCall, ToolResult

MCP_TOOL_PREFIX = "mcp__"

def _make_tool_name(server_id: str, tool_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{server_id}__{tool_name}"

def _parse_tool_name(full: str) -> tuple[str, str] | None:
    if not full.startswith(MCP_TOOL_PREFIX):
        return None
    rest = full[len(MCP_TOOL_PREFIX):]
    if "__" not in rest:
        return None
    sid, tname = rest.split("__", 1)
    return sid, tname

@dataclass
class MCPTool:
    server_id: str
    name: str                  # оригинальное имя на сервере
    full_name: str             # mcp__server__name
    description: str = ""
    input_schema: dict = field(default_factory=dict)

@dataclass
class MCPServer:
    id: str
    config: dict
    tools: list[MCPTool] = field(default_factory=list)
    status: str = "disconnected"  # disconnected | connected | error
    error: str = ""
    _session: Any = None
    _exit_stack: Any = None

class MCPManager:
    """Singleton: фоновый asyncio loop + список серверов."""

    _instance: Optional["MCPManager"] = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self):
        self.servers: dict[str, MCPServer] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    @classmethod
    def instance(cls) -> "MCPManager":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = MCPManager()
        return cls._instance

    # ── фоновый loop ───────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def _run():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                try:
                    loop.run_forever()
                except Exception as e:
                    logger.error("mcp loop crashed: {}", e)
                finally:
                    try:
                        loop.close()
                    except Exception:
                        logger.debug("mcp loop.close failed", exc_info=True)

            self._thread = threading.Thread(
                target=_run, name="mcp-loop", daemon=True,
            )
            self._thread.start()
            ready.wait(timeout=5)
            if not self._loop:
                raise RuntimeError("MCP loop failed to start")
            return self._loop

    def _submit(self, coro, timeout: float = 30.0) -> Any:
        loop = self._ensure_loop()
        fut: Future = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    # ── подключение ─────────────────────────────────────────────

    def connect_all(self, configs: list[dict]) -> None:
        for cfg in configs:
            if not cfg.get("enabled", True):
                continue
            sid = cfg.get("id")
            if not sid:
                continue
            try:
                self.connect(cfg)
            except Exception as e:
                logger.error("mcp connect '{}' failed: {}", sid, e)

    def connect(self, cfg: dict) -> MCPServer:
        sid = cfg["id"]
        # Защищаем проверку+резервирование записи self.servers lock-ом от гонки
        # параллельных connect (иначе два процесса на один server_id).
        # _submit() вызывается ВНЕ критической секции: _ensure_loop тоже берёт
        # self._lock, поэтому держать его во время _submit нельзя (deadlock).
        with self._lock:
            existing = self.servers.get(sid)
            if existing is not None and existing.status == "connected":
                return existing
            stale = existing
            server = MCPServer(id=sid, config=cfg)
            self.servers[sid] = server
        # Закрываем предыдущий сервер с тем же sid (вне критической секции:
        # _submit→_ensure_loop тоже берёт self._lock → иначе deadlock), иначе
        # утечка процесса/пайпа MCP-сервера при повторном connect.
        if stale is not None and stale._exit_stack is not None:
            try:
                self._submit(self._disconnect_async(stale), timeout=10.0)
            except Exception as e:
                logger.warning("mcp aclose stale server '{}' failed: {}", sid, e)
            stale._session = None
            stale._exit_stack = None
        try:
            self._submit(self._connect_async(server), timeout=30.0)
            server.status = "connected"
            logger.info(
                "mcp connected: {} ({} tools)", sid, len(server.tools),
            )
        except Exception as e:
            server.status = "error"
            server.error = str(e)
            logger.error("mcp connect '{}' error: {}", sid, e)
        return server

    # WARNING: вход контекста и aclose() в разных задачах — потенциальный RuntimeError в anyio cancel scopes; требует рефакторинга на server-task паттерн
    async def _connect_async(self, server: MCPServer) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
        except ImportError as e:
            raise RuntimeError(
                "mcp package not installed. Run: uv add mcp"
            ) from e

        from contextlib import AsyncExitStack

        cfg = server.config
        transport = cfg.get("transport", "stdio")
        if transport != "stdio":
            raise NotImplementedError(
                f"transport '{transport}' not supported yet (only stdio)"
            )

        command = cfg.get("command")
        if not command:
            raise ValueError("server config missing 'command'")
        args = cfg.get("args", [])
        env = cfg.get("env") or None

        params = StdioServerParameters(command=command, args=args, env=env)
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            tools_resp = await session.list_tools()
            server._session = session
            server._exit_stack = stack
            server.tools = [
                MCPTool(
                    server_id=server.id,
                    name=t.name,
                    full_name=_make_tool_name(server.id, t.name),
                    description=t.description or "",
                    input_schema=t.inputSchema or {"type": "object", "properties": {}},
                )
                for t in tools_resp.tools
            ]
        except Exception:
            await stack.aclose()
            raise

    def disconnect(self, server_id: str) -> None:
        server = self.servers.get(server_id)
        if not server:
            return
        try:
            self._submit(self._disconnect_async(server), timeout=10.0)
        except Exception as e:
            logger.warning("mcp disconnect '{}' error: {}", server_id, e)
        server.status = "disconnected"
        server.tools = []
        server._session = None
        server._exit_stack = None

    async def _disconnect_async(self, server: MCPServer) -> None:
        if server._exit_stack is not None:
            await server._exit_stack.aclose()

    def shutdown(self) -> None:
        for sid in list(self.servers.keys()):
            self.disconnect(sid)
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("mcp loop thread did not stop within timeout")
        self._thread = None
        self._loop = None

    # ── вызов tool ──────────────────────────────────────────────

    def call_tool(self, full_name: str, args: dict, timeout: float = 120.0) -> ToolResult:
        parsed = _parse_tool_name(full_name)
        if not parsed:
            return ToolResult(
                name=full_name, status="error",
                output=f"invalid MCP tool name: {full_name}",
                exit_code=1, command=full_name,
            )
        sid, tname = parsed
        server = self.servers.get(sid)
        if not server or server.status != "connected":
            return ToolResult(
                name=full_name, status="error",
                output=f"MCP server '{sid}' not connected (status={server.status if server else 'missing'})",
                exit_code=1, command=full_name,
            )
        try:
            text = self._submit(
                self._call_tool_async(server, tname, args),
                timeout=timeout,
            )
            return ToolResult(
                name=full_name, status="ok",
                output=text, exit_code=0, command=f"{sid}.{tname}",
            )
        except Exception as e:
            logger.error("mcp call '{}' failed: {}", full_name, e)
            return ToolResult(
                name=full_name, status="error",
                output=f"MCP call failed: {type(e).__name__}: {e}",
                exit_code=1, command=full_name,
            )

    async def _call_tool_async(self, server: MCPServer, tname: str, args: dict) -> str:
        result = await server._session.call_tool(tname, args or {})
        # CallToolResult.content — список TextContent/ImageContent/EmbeddedResource
        parts = []
        for item in (result.content or []):
            t = getattr(item, "type", None)
            if t == "text":
                parts.append(getattr(item, "text", ""))
            elif t == "image":
                mime = getattr(item, "mimeType", "image/png")
                parts.append(f"[image/{mime} returned, {len(getattr(item, 'data', '') or '')} b64 chars]")
            elif t == "resource":
                res = getattr(item, "resource", None)
                uri = getattr(res, "uri", "?") if res else "?"
                parts.append(f"[resource: {uri}]")
            else:
                parts.append(str(item))
        text = "\n".join(parts).strip()
        if getattr(result, "isError", False):
            return f"[MCP tool error]\n{text}"
        return text or "[empty result]"

    # ── интроспекция ───────────────────────────────────────────

    def list_tools(self) -> list[MCPTool]:
        out: list[MCPTool] = []
        for s in self.servers.values():
            if s.status == "connected":
                out.extend(s.tools)
        return out

    def list_servers_info(self) -> list[dict]:
        return [
            {
                "id": s.id,
                "status": s.status,
                "error": s.error,
                "tool_count": len(s.tools),
                "tools": [t.name for t in s.tools],
                "command": s.config.get("command", ""),
            }
            for s in self.servers.values()
        ]

# ── публичный API ──────────────────────────────────────────────

def init_mcp_from_config() -> int:
    """Подключает все enabled серверы из .data/mcp_servers.json.

    Возвращает количество успешно подключённых серверов.
    """
    from config.mcp import list_servers
    cfgs = list_servers()
    if not cfgs:
        return 0
    mgr = MCPManager.instance()
    mgr.connect_all(cfgs)
    connected = sum(1 for s in mgr.servers.values() if s.status == "connected")
    if connected:
        _register_in_tool_registry()
    return connected

def _register_in_tool_registry() -> None:
    """Регистрирует все MCP-инструменты в TOOL_REGISTRY."""
    from tools.registry import TOOL_REGISTRY
    mgr = MCPManager.instance()
    for tool in mgr.list_tools():
        TOOL_REGISTRY[tool.full_name] = _make_handler(tool.full_name)
    # Инвалидируем кэш get_tool_schemas — состав MCP tools изменился.
    try:
        from apis.tool_schemas import invalidate_schemas_cache
        invalidate_schemas_cache()
    except Exception:
        logger.warning("schemas cache invalidate after register failed", exc_info=True)

def _make_handler(full_name: str) -> Callable[[ToolCall], ToolResult]:
    def _handler(call: ToolCall) -> ToolResult:
        return MCPManager.instance().call_tool(full_name, call.args or {})
    return _handler

def list_mcp_tools() -> list[MCPTool]:
    return MCPManager.instance().list_tools()

def list_mcp_servers() -> list[dict]:
    return MCPManager.instance().list_servers_info()

def reconnect_mcp() -> int:
    """Полностью переподключает все серверы."""
    from tools.registry import TOOL_REGISTRY
    mgr = MCPManager.instance()
    # Снять старые регистрации
    for k in list(TOOL_REGISTRY.keys()):
        if k.startswith(MCP_TOOL_PREFIX):
            TOOL_REGISTRY.pop(k, None)
    # Инвалидируем schemas cache — после снятия MCP tools состав изменился.
    try:
        from apis.tool_schemas import invalidate_schemas_cache
        invalidate_schemas_cache()
    except Exception:
        logger.warning("schemas cache invalidate after reconnect failed", exc_info=True)
    mgr.shutdown()
    MCPManager._instance = None
    return init_mcp_from_config()

def shutdown_mcp() -> None:
    if MCPManager._instance is not None:
        MCPManager._instance.shutdown()

def get_mcp_tool_schemas() -> list[dict]:
    """OpenAI-совместимые JSON-схемы для всех подключённых MCP-инструментов."""
    schemas = []
    for tool in list_mcp_tools():
        desc = tool.description or f"MCP tool '{tool.name}' from server '{tool.server_id}'"
        # Префикс описания подсказывает модели источник
        desc = f"[MCP/{tool.server_id}] {desc}"
        schemas.append({
            "type": "function",
            "function": {
                "name": tool.full_name,
                "description": desc,
                "parameters": tool.input_schema or {"type": "object", "properties": {}},
            },
        })
    return schemas