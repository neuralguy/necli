"""Конфиг LSP-серверов (.data/lsp_servers.json).

Формат:
{
  "servers": [
    {
      "id": "pyright",
      "command": "pyright-langserver",
      "args": ["--stdio"],
      "languages": ["python"],
      "root_markers": ["pyproject.toml", "setup.py", ".git"],
      "enabled": true
    }
  ]
}

Если файла нет — используются дефолты из DEFAULT_SERVERS (по требованию).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from config.paths import BASE_DIR

logger = logging.getLogger(__name__)

LSP_FILE = BASE_DIR / "lsp_servers.json"
_load_failed = False


# Дефолтные конфиги популярных LSP. Включаются если есть соответствующая команда в PATH.
DEFAULT_SERVERS: list[dict] = [
    {
        "id": "pyright",
        "command": "pyright-langserver",
        "args": ["--stdio"],
        "languages": ["python"],
        "extensions": [".py", ".pyi"],
        "root_markers": ["pyproject.toml", "setup.py", "setup.cfg", ".git"],
        "enabled": True,
        "settings": {
            "python": {
                "analysis": {
                    "diagnosticMode": "openFilesOnly",
                    "useLibraryCodeForTypes": True,
                    "typeCheckingMode": "basic",
                    "autoSearchPaths": True,
                },
            },
        },
    },
    {
        "id": "ts",
        "command": "typescript-language-server",
        "args": ["--stdio"],
        "languages": ["typescript", "javascript", "typescriptreact", "javascriptreact"],
        "extensions": [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"],
        "root_markers": ["package.json", "tsconfig.json", ".git"],
        "enabled": True,
    },
    {
        "id": "gopls",
        "command": "gopls",
        "args": [],
        "languages": ["go"],
        "extensions": [".go"],
        "root_markers": ["go.mod", ".git"],
        "enabled": True,
    },
    {
        "id": "rust-analyzer",
        "command": "rust-analyzer",
        "args": [],
        "languages": ["rust"],
        "extensions": [".rs"],
        "root_markers": ["Cargo.toml", ".git"],
        "enabled": True,
    },
]


def _load() -> dict:
    global _load_failed
    if not LSP_FILE.exists():
        _load_failed = False
        return {"servers": list(DEFAULT_SERVERS)}
    try:
        data = json.loads(LSP_FILE.read_text(encoding="utf-8"))
        _load_failed = False
        return data
    except (json.JSONDecodeError, OSError) as e:
        _load_failed = True
        logger.error(
            "lsp config load failed for %s: %s. Saving is disabled until the file is fixed.",
            LSP_FILE, e,
        )
        return {"servers": list(DEFAULT_SERVERS)}


def _save(data: dict) -> None:
    if _load_failed:
        logger.error("refusing to save LSP config after failed load: %s", LSP_FILE)
        return
    LSP_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=LSP_FILE.parent, delete=False,
    ) as fh:
        fh.write(payload)
        tmp_name = fh.name
    os.replace(tmp_name, LSP_FILE)


def list_servers() -> list[dict]:
    return list(_load().get("servers", []))


def add_server(cfg: dict) -> None:
    data = _load()
    servers = data.setdefault("servers", [])
    sid = cfg.get("id")
    if not sid:
        raise ValueError("server config must have 'id'")
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


def get_auto_diagnostics() -> bool:
    """Включена ли авто-диагностика после write/patch/create. По умолчанию True."""
    data = _load()
    return bool(data.get("auto_diagnostics", True))


def set_auto_diagnostics(enabled: bool) -> None:
    data = _load()
    data["auto_diagnostics"] = bool(enabled)
    _save(data)
