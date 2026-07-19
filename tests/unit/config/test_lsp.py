"""config/lsp.py — конфиг LSP-серверов."""

import pytest

from config import lsp


@pytest.fixture(autouse=True)
def _isolated(isolated_data, monkeypatch):
    monkeypatch.setattr(lsp, "LSP_FILE", isolated_data / "lsp_servers.json")
    yield


class TestDefaults:
    def test_no_file_returns_defaults(self):
        servers = lsp.list_servers()
        ids = [s["id"] for s in servers]
        assert "pyright" in ids


class TestAddRemove:
    def test_add(self):
        lsp.add_server({"id": "mylsp", "command": "c", "args": []})
        ids = [s["id"] for s in lsp.list_servers()]
        assert "mylsp" in ids

    def test_replace_same_id(self):
        lsp.add_server({"id": "mylsp", "command": "old"})
        lsp.add_server({"id": "mylsp", "command": "new"})
        servers = [s for s in lsp.list_servers() if s["id"] == "mylsp"]
        assert len(servers) == 1
        assert servers[0]["command"] == "new"

    def test_remove_existing(self):
        lsp.add_server({"id": "mylsp", "command": "c"})
        assert lsp.remove_server("mylsp") is True

    def test_remove_missing(self):
        # никакой реальной записи "totally-unknown-lsp"
        assert lsp.remove_server("totally-unknown-lsp") is False

    def test_no_id_raises(self):
        with pytest.raises(ValueError):
            lsp.add_server({"command": "x"})


class TestSetEnabled:
    def test_toggle(self):
        lsp.add_server({"id": "x", "command": "c", "enabled": True})
        assert lsp.set_enabled("x", False) is True
        srv = next(s for s in lsp.list_servers() if s["id"] == "x")
        assert srv["enabled"] is False

    def test_missing(self):
        assert lsp.set_enabled("nope_lsp", False) is False


class TestAutoDiagnostics:
    def test_default_true(self):
        # save default servers first, чтобы файл существовал
        lsp.add_server({"id": "tmp", "command": "c"})
        assert lsp.get_auto_diagnostics() is True

    def test_set_false(self):
        lsp.set_auto_diagnostics(False)
        assert lsp.get_auto_diagnostics() is False
