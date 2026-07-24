"""apis/lsp_client.py — сообщение о недоступности LSP подсказывает fallback."""

from pathlib import Path

from apis.lsp_client import LSPManager


def _mgr_with_py_config():
    m = LSPManager()
    m._configs = [{
        "id": "pyright",
        "command": "pyright-langserver",
        "args": ["--stdio"],
        "extensions": [".py", ".pyi"],
        "root_markers": ["pyproject.toml", ".git"],
        "enabled": True,
    }]
    return m


class TestUnavailableReason:
    """Регрессия UX: раньше любая недоступность LSP давала одно «нет сервера,
    проверь конфиг» — сбивало с толку (часто конфиг в порядке) и не подсказывало
    замену. Теперь причина точная + всегда совет использовать read_files."""

    def test_no_config_for_extension(self):
        m = _mgr_with_py_config()
        msg = m._unavailable_reason(Path("/tmp/x.zzz"))
        assert "No LSP server configured" in msg
        assert ".zzz" in msg
        assert "read_files" in msg

    def test_always_suggests_read_fallback(self):
        m = _mgr_with_py_config()
        # .py с конфигом, но без реального сервера/рута в /tmp
        msg = m._unavailable_reason(Path("/tmp/nonexistent_proj/a.py"))
        assert "read_files" in msg
        # причина названа (pyright фигурирует), а не общее «проверь конфиг»
        assert "pyright" in msg

    def test_action_resolves_extensionless_absolute_file(self, tmp_path, monkeypatch):
        target = tmp_path / "sample.py"
        target.write_text("x = 1\n")
        captured = {}
        manager = LSPManager()

        def capture_path(path):
            captured["path"] = path
            return None

        monkeypatch.setattr(manager, "_ensure_server", capture_path)
        result = manager._run_action(str(target.with_suffix("")), 1, 0, "diagnostics")

        assert result.status == "error"
        assert captured["path"] == target
