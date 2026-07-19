"""config/mcp.py — конфиг MCP-серверов."""

import pytest

from config import mcp


@pytest.fixture(autouse=True)
def _isolated(isolated_data, monkeypatch):
    monkeypatch.setattr(mcp, "MCP_FILE", isolated_data / "mcp_servers.json")
    yield


class TestListServers:
    def test_empty(self):
        assert mcp.list_servers() == []


class TestAddServer:
    def test_basic(self):
        mcp.add_server({"id": "gh", "command": "npx", "args": ["-y"]})
        servers = mcp.list_servers()
        assert len(servers) == 1
        assert servers[0]["id"] == "gh"

    def test_replaces_same_id(self):
        mcp.add_server({"id": "gh", "command": "npx"})
        mcp.add_server({"id": "gh", "command": "node"})
        servers = mcp.list_servers()
        assert len(servers) == 1
        assert servers[0]["command"] == "node"

    def test_no_id_raises(self):
        with pytest.raises(ValueError):
            mcp.add_server({"command": "x"})


class TestGetServer:
    def test_found(self):
        mcp.add_server({"id": "x", "command": "c"})
        assert mcp.get_server("x")["command"] == "c"

    def test_missing(self):
        assert mcp.get_server("nope") is None


class TestRemoveServer:
    def test_existing(self):
        mcp.add_server({"id": "x", "command": "c"})
        assert mcp.remove_server("x") is True
        assert mcp.list_servers() == []

    def test_missing(self):
        assert mcp.remove_server("nope") is False


class TestSetEnabled:
    def test_existing(self):
        mcp.add_server({"id": "x", "command": "c", "enabled": True})
        assert mcp.set_enabled("x", False) is True
        assert mcp.get_server("x")["enabled"] is False

    def test_missing(self):
        assert mcp.set_enabled("nope", False) is False
