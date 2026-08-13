"""Конфиг MCP-серверов (.data/mcp_servers.json).

Формат:
{
  "servers": [
    {
      "id": "github",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "..."},
      "enabled": true,
      "transport": "stdio"
    }
  ]
}
"""

from __future__ import annotations

import copy
import json
import logging
import threading

from config._atomic import atomic_write_json
from config._sync import set_enabled as _set_enabled
from config._sync import synchronized
from config.paths import BASE_DIR

logger = logging.getLogger(__name__)

MCP_FILE = BASE_DIR / "mcp_servers.json"
_load_failed = False
_LOCK = threading.RLock()


@synchronized(_LOCK)
def _load() -> dict:
    global _load_failed
    if not MCP_FILE.exists():
        _load_failed = False
        return {"servers": []}
    try:
        data = json.loads(MCP_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("root must be a JSON object")
        _load_failed = False
        return data
    except (json.JSONDecodeError, OSError, ValueError) as e:
        _load_failed = True
        logger.error(
            "mcp config load failed for %s: %s. Saving is disabled until the file is fixed.",
            MCP_FILE,
            e,
        )
        return {"servers": []}


@synchronized(_LOCK)
def _save(data: dict) -> None:
    if _load_failed:
        logger.error("refusing to save MCP config after failed load: %s", MCP_FILE)
        return
    atomic_write_json(MCP_FILE, data)


@synchronized(_LOCK)
def list_servers() -> list[dict]:
    return copy.deepcopy(_load().get("servers", []))


@synchronized(_LOCK)
def get_server(server_id: str) -> dict | None:
    for s in list_servers():
        if s.get("id") == server_id:
            return s
    return None


@synchronized(_LOCK)
def add_server(cfg: dict) -> None:
    data = _load()
    servers = data.setdefault("servers", [])
    sid = cfg.get("id")
    if not sid:
        raise ValueError("server config must have 'id'")
    transport = cfg.get("transport", "stdio")
    if transport != "stdio":
        raise ValueError(f"transport '{transport}' not supported yet (only 'stdio')")
    servers[:] = [s for s in servers if s.get("id") != sid]
    servers.append(copy.deepcopy(cfg))
    _save(data)


@synchronized(_LOCK)
def remove_server(server_id: str) -> bool:
    data = _load()
    servers = data.get("servers", [])
    new = [s for s in servers if s.get("id") != server_id]
    if len(new) == len(servers):
        return False
    data["servers"] = new
    _save(data)
    return True


@synchronized(_LOCK)
def set_enabled(server_id: str, enabled: bool) -> bool:
    data = _load()
    if not _set_enabled(data.get("servers", []), server_id, enabled):
        return False
    _save(data)
    return True
