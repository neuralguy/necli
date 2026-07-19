"""ui/clipboard_copy.py — copy_to_clipboard с мокнутым subprocess (без реального буфера)."""

import subprocess

import pytest

import ui.clipboard_copy as cc


@pytest.fixture
def no_tools(monkeypatch):
    """shutil.which всегда возвращает None → нет инструментов."""
    monkeypatch.setattr(cc.shutil, "which", lambda name: None)

def _which_only(*available):
    avail = set(available)
    return lambda name: ("/usr/bin/" + name) if name in avail else None

class TestEmptyAndNoTools:
    def test_empty_text(self):
        assert cc.copy_to_clipboard("") == "empty text"

    @pytest.mark.usefixtures("no_tools")
    def test_no_clipboard_tool(self):
        err = cc.copy_to_clipboard("hello")
        assert err is not None
        assert "no clipboard tool found" in err

class TestRunBasedTools:
    """wl-copy/pbcopy/clip.exe идут через subprocess.run (detach=False)."""

    def test_wl_copy_success(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("wl-copy"))
        captured = {}

        def fake_run(cmd, input=None, env=None, capture_output=None, timeout=None):
            captured["cmd"] = cmd
            captured["input"] = input
            captured["capture_output"] = capture_output
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(cc.subprocess, "run", fake_run)
        result = cc.copy_to_clipboard("hi")
        assert result is None
        assert captured["cmd"] == ["wl-copy"]
        assert captured["input"] == b"hi"
        assert captured["capture_output"] is True

    def test_pbcopy_success(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("pbcopy"))
        monkeypatch.setattr(
            cc.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""),
        )
        assert cc.copy_to_clipboard("text") is None

    def test_clip_exe_success(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("clip.exe"))
        monkeypatch.setattr(
            cc.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, b"", b""),
        )
        assert cc.copy_to_clipboard("text") is None

    def test_nonzero_exit_returns_error(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("wl-copy"))
        monkeypatch.setattr(
            cc.subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, b"", b"boom"),
        )
        err = cc.copy_to_clipboard("x")
        assert err is not None
        assert "wl-copy exit 1" in err
        assert "boom" in err

    def test_run_raises_returns_error(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("wl-copy"))

        def boom(cmd, **kw):
            raise OSError("spawn failed")

        monkeypatch.setattr(cc.subprocess, "run", boom)
        err = cc.copy_to_clipboard("x")
        assert err is not None
        assert "wl-copy" in err
        assert "spawn failed" in err

class _FakeStdin:
    def __init__(self, store, fail=False):
        self._store = store
        self._fail = fail

    def write(self, data):
        if self._fail:
            raise OSError("write blew up")
        self._store["written"] = data

    def close(self):
        self._store["closed"] = True

class _FakeProc:
    def __init__(self, store, fail_write=False):
        self.stdin = _FakeStdin(store, fail=fail_write)

class TestDetachBasedTools:
    """xclip/xsel демонизируются через subprocess.Popen (detach=True)."""

    def test_xclip_success(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("xclip"))
        store = {}

        def fake_popen(cmd, **kw):
            store["cmd"] = cmd
            store["start_new_session"] = kw.get("start_new_session")
            return _FakeProc(store)

        monkeypatch.setattr(cc.subprocess, "Popen", fake_popen)
        result = cc.copy_to_clipboard("payload")
        assert result is None
        assert store["cmd"] == ["xclip", "-selection", "clipboard"]
        assert store["start_new_session"] is True
        assert store["written"] == b"payload"
        assert store["closed"] is True

    def test_xsel_success(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("xsel"))
        store = {}
        monkeypatch.setattr(
            cc.subprocess, "Popen", lambda cmd, **kw: _FakeProc(store)
        )
        assert cc.copy_to_clipboard("data") is None
        assert store["written"] == b"data"

    def test_detach_write_failure_returns_error(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("xclip"))
        store = {}
        monkeypatch.setattr(
            cc.subprocess, "Popen",
            lambda cmd, **kw: _FakeProc(store, fail_write=True),
        )
        err = cc.copy_to_clipboard("x")
        assert err is not None
        assert "write failed" in err

class TestPreferenceOrder:
    def test_wl_copy_preferred_over_xclip(self, monkeypatch):
        monkeypatch.setattr(cc.shutil, "which", _which_only("wl-copy", "xclip"))
        used = {}

        def fake_run(cmd, **kw):
            used["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, b"", b"")

        monkeypatch.setattr(cc.subprocess, "run", fake_run)
        # Popen не должен вызваться, т.к. wl-copy идёт первым
        monkeypatch.setattr(
            cc.subprocess, "Popen",
            lambda *a, **k: pytest.fail("Popen should not be called"),
        )
        assert cc.copy_to_clipboard("z") is None
        assert used["cmd"] == ["wl-copy"]
