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

import json
import logging
import os
import tempfile

from config.paths import BASE_DIR

logger = logging.getLogger(__name__)

MCP_FILE = BASE_DIR / "mcp_servers.json"
_load_failed = False


def _load() -> dict:
    global _load_failed
    if not MCP_FILE.exists():
        _load_failed = False
        return {"servers": []}
    try:
        data = json.loads(MCP_FILE.read_text(encoding="utf-8"))
        _load_failed = False
        return data
    except (json.JSONDecodeError, OSError) as e:
        _load_failed = True
        logger.error(
            "mcp config load failed for %s: %s. Saving is disabled until the file is fixed.",
            MCP_FILE, e,
        )
        return {"servers": []}


def _save(data: dict) -> None:
    if _load_failed:
        logger.error("refusing to save MCP config after failed load: %s", MCP_FILE)
        return
    MCP_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=MCP_FILE.parent, delete=False,
    ) as fh:
        fh.write(payload)
        tmp_name = fh.name
    os.replace(tmp_name, MCP_FILE)


def list_servers() -> list[dict]:
    return list(_load().get("servers", []))


def get_server(server_id: str) -> dict | None:
    for s in list_servers():
        if s.get("id") == server_id:
            return s
    return None


def add_server(cfg: dict) -> None:
    data = _load()
    servers = data.setdefault("servers", [])
    sid = cfg.get("id")
    if not sid:
        raise ValueError("server config must have 'id'")
    transport = cfg.get("transport", "stdio")
    if transport != "stdio":
        raise ValueError(
            f"transport '{transport}' not supported yet (only 'stdio')"
        )
    servers[:] = [s for s in servers if s.get("id") != sid]
    servers.append(cfg)
    _save(data)


def remove_server(server_id: str) -> bool:
    data = _load()
    servers = data.get("servers", [])
    new = [s for s in servers if s.get("id") != server_id]
    if len(new) == len(servers):
        return False
    data["servers"] = new
    _save(data)
    return True


def set_enabled(server_id: str, enabled: bool) -> bool:
    data = _load()
    for s in data.get("servers", []):
        if s.get("id") == server_id:
            s["enabled"] = enabled
            _save(data)
            return True
    return False