"""tools/ssh.py — arg/command building, blocking, execute_ssh (no real SSH)."""

from unittest.mock import patch, MagicMock

import subprocess


import tools.ssh as ssh_mod
from tools.ssh import (
    _build_ssh_args,
    _is_blocked_interactive,
    _is_hard_blocked,
    _needs_confirmation,
    _socket_path,
    _run_ssh_command,
    _run_scp,
    execute_ssh,
    check_connection,
)
from tools.models import ToolCall

def _call(args: dict, command: str = "ssh") -> ToolCall:
    return ToolCall(command=command, tool_name="ssh", args=args)

HOST_CFG = {"host": "1.2.3.4", "user": "ubuntu", "port": 2222, "confirm_dangerous": True}

class TestBuildSshArgs:
    def test_basic_structure(self):
        args = _build_ssh_args(HOST_CFG, "srv")
        assert args[0] == "ssh"
        assert args[-1] == "ubuntu@1.2.3.4"
        assert "-p" in args
        assert args[args.index("-p") + 1] == "2222"

    def test_default_port_user(self):
        args = _build_ssh_args({"host": "h"}, "a")
        assert args[-1] == "root@h"
        assert args[args.index("-p") + 1] == "22"

    def test_control_path_present(self):
        args = _build_ssh_args(HOST_CFG, "srv")
        cp = [a for a in args if a.startswith("ControlPath=")]
        assert cp
        assert "srv.sock" in cp[0]

    def test_key_added_and_expanded(self):
        args = _build_ssh_args({"host": "h", "key": "~/k"}, "a")
        assert "-i" in args
        key_val = args[args.index("-i") + 1]
        assert "~" not in key_val  # expanduser applied

    def test_no_key_no_i_flag(self):
        args = _build_ssh_args({"host": "h"}, "a")
        assert "-i" not in args

class TestSocketPath:
    def test_socket_path_naming(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ssh_mod, "SOCKETS_DIR", tmp_path / "socks")
        p = _socket_path("myhost")
        assert p.name == "myhost.sock"
        assert p.parent.exists()

class TestBlockedInteractive:
    def test_vim_blocked(self):
        assert _is_blocked_interactive("vim file") is not None

    def test_top_blocked(self):
        assert _is_blocked_interactive("top") is not None

    def test_normal_allowed(self):
        assert _is_blocked_interactive("ls -la") is None

    def test_empty_allowed(self):
        assert _is_blocked_interactive("") is None

    def test_substring_not_matched(self):
        # 'vimdiff' is not in the blocked list as first word
        assert _is_blocked_interactive("vimdiff a b") is None

class TestHardBlocked:
    def test_rm_rf_root(self):
        assert _is_hard_blocked("rm -rf /") is not None

    def test_reboot(self):
        assert _is_hard_blocked("reboot") is not None

    def test_case_insensitive(self):
        assert _is_hard_blocked("REBOOT") is not None

    def test_benign(self):
        assert _is_hard_blocked("ls -la") is None

class TestNeedsConfirmation:
    def test_rm_rf(self):
        assert _needs_confirmation("rm -rf /home/x") is True

    def test_drop_table(self):
        assert _needs_confirmation("mysql -e 'DROP TABLE users'") is True

    def test_case_insensitive(self):
        assert _needs_confirmation("drop database mydb") is True

    def test_benign(self):
        assert _needs_confirmation("echo hi") is False

class TestRunSshCommand:
    def test_blocked_interactive_returns_error(self):
        r = _run_ssh_command(HOST_CFG, "srv", "vim x")
        assert r.status == "error"
        assert "vim" in r.output

    def test_hard_blocked_returns_error(self):
        r = _run_ssh_command(HOST_CFG, "srv", "rm -rf /")
        assert r.status == "error"
        assert "rm -rf /" in r.output

    def test_confirmation_rejected(self):
        # 'chown -R' triggers confirmation but is NOT a hard-block
        with patch("tools.ssh._confirm_command", return_value=False):
            r = _run_ssh_command(HOST_CFG, "srv", "chown -R root /home/data")
        assert r.status == "error"
        assert "отклонена" in r.output

    def test_confirmation_accepted_runs(self):
        completed = MagicMock(returncode=0, stdout="done", stderr="")
        with patch("tools.ssh._confirm_command", return_value=True), \
             patch("tools.ssh.subprocess.run", return_value=completed) as run:
            r = _run_ssh_command(HOST_CFG, "srv", "chown -R root /home/data")
        assert r.status == "ok"
        assert "done" in r.output
        # command appended last
        assert run.call_args.args[0][-1] == "chown -R root /home/data"

    def test_confirm_skipped_when_disabled(self):
        cfg = dict(HOST_CFG, confirm_dangerous=False)
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("tools.ssh._confirm_command") as confirm, \
             patch("tools.ssh.subprocess.run", return_value=completed):
            r = _run_ssh_command(cfg, "srv", "chown -R root /home/data")
        confirm.assert_not_called()
        assert r.status == "ok"

    def test_success_output_and_command_field(self):
        completed = MagicMock(returncode=0, stdout="hello\n", stderr="")
        with patch("tools.ssh.subprocess.run", return_value=completed):
            r = _run_ssh_command(HOST_CFG, "srv", "echo hello")
        assert r.status == "ok"
        assert "hello" in r.output
        assert r.command == "ssh:srv echo hello"

    def test_stderr_captured(self):
        completed = MagicMock(returncode=1, stdout="", stderr="bad")
        with patch("tools.ssh.subprocess.run", return_value=completed):
            r = _run_ssh_command(HOST_CFG, "srv", "false")
        assert r.status == "error"
        assert "[stderr]" in r.output
        assert "bad" in r.output

    def test_no_output_marker(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("tools.ssh.subprocess.run", return_value=completed):
            r = _run_ssh_command(HOST_CFG, "srv", "true")
        assert "no output" in r.output

    def test_timeout(self):
        with patch("tools.ssh.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("ssh", 1)):
            r = _run_ssh_command(HOST_CFG, "srv", "sleep 999")
        assert r.status == "error"
        assert "Timeout" in r.output

    def test_generic_exception(self):
        with patch("tools.ssh.subprocess.run", side_effect=OSError("nope")):
            r = _run_ssh_command(HOST_CFG, "srv", "echo x")
        assert r.status == "error"
        assert "SSH error" in r.output

class TestRunScp:
    def test_upload_builds_command(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("tools.ssh.subprocess.run", return_value=completed) as run:
            r = _run_scp(HOST_CFG, "srv", "/local/f", "/remote/f", upload=True)
        assert r.status == "ok"
        argv = run.call_args.args[0]
        assert argv[0] == "scp"
        assert "/local/f" in argv
        assert "ubuntu@1.2.3.4:/remote/f" in argv

    def test_download_builds_command(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("tools.ssh.subprocess.run", return_value=completed) as run:
            _run_scp(HOST_CFG, "srv", "/remote/f", "/local/f", upload=False)
        argv = run.call_args.args[0]
        assert "ubuntu@1.2.3.4:/remote/f" in argv
        assert "/local/f" in argv

    def test_scp_port_flag(self):
        completed = MagicMock(returncode=0, stdout="", stderr="")
        with patch("tools.ssh.subprocess.run", return_value=completed) as run:
            _run_scp(HOST_CFG, "srv", "a", "b", upload=True)
        argv = run.call_args.args[0]
        assert "-P" in argv
        assert argv[argv.index("-P") + 1] == "2222"

    def test_scp_failure_status(self):
        completed = MagicMock(returncode=1, stdout="", stderr="permission denied")
        with patch("tools.ssh.subprocess.run", return_value=completed):
            r = _run_scp(HOST_CFG, "srv", "a", "b", upload=True)
        assert r.status == "error"
        assert "permission denied" in r.output

    def test_scp_timeout(self):
        with patch("tools.ssh.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("scp", 1)):
            r = _run_scp(HOST_CFG, "srv", "a", "b", upload=True)
        assert r.status == "error"
        assert "timeout" in r.output.lower()

class TestExecuteSsh:
    def test_no_alias_no_hosts(self):
        with patch("tools.ssh.list_hosts", return_value={}):
            r = execute_ssh(_call({}, command=""))
        assert r.status == "error"
        assert "Нет настроенных" in r.output

    def test_no_alias_with_hosts(self):
        with patch("tools.ssh.list_hosts", return_value={"a": {}, "b": {}}):
            r = execute_ssh(_call({}, command=""))
        assert r.status == "error"
        assert "a" in r.output and "b" in r.output

    def test_unknown_host(self):
        with patch("tools.ssh.get_host", return_value=None), \
             patch("tools.ssh.list_hosts", return_value={"known": {}}):
            r = execute_ssh(_call({"host": "ghost"}))
        assert r.status == "error"
        assert "ghost" in r.output

    def test_missing_command(self):
        with patch("tools.ssh.get_host", return_value=HOST_CFG):
            r = execute_ssh(_call({"host": "srv"}, command="srv"))
        assert r.status == "error"
        assert "команду" in r.output.lower()

    def test_command_mode_dispatch(self):
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh.subprocess.run", return_value=completed) as run:
            r = execute_ssh(_call({"host": "srv", "command": "uptime"}))
        assert r.status == "ok"
        assert run.call_args.args[0][-1] == "uptime"

    def test_upload_dispatch(self):
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh._run_scp") as scp:
            scp.return_value = MagicMock(status="ok")
            execute_ssh(_call({"host": "srv", "upload": "/f", "dest": "/d"}))
        assert scp.call_args.kwargs.get("upload") is True or scp.call_args.args[-1] is True

    def test_download_dispatch(self):
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh._run_scp") as scp:
            scp.return_value = MagicMock(status="ok")
            execute_ssh(_call({"host": "srv", "download": "/f"}))
        scp.assert_called_once()

    def test_command_from_call_command_strips_alias(self):
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh.subprocess.run", return_value=completed) as run:
            execute_ssh(_call({"host": "srv"}, command="srv ls -la"))
        assert run.call_args.args[0][-1] == "ls -la"

class TestCheckConnection:
    def test_host_not_found(self):
        with patch("tools.ssh.get_host", return_value=None):
            ok, msg = check_connection("ghost")
        assert ok is False
        assert "не найден" in msg

    def test_success(self):
        completed = MagicMock(returncode=0, stdout="ok", stderr="")
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh.subprocess.run", return_value=completed):
            ok, msg = check_connection("srv")
        assert ok is True
        assert "ubuntu@1.2.3.4:2222" in msg

    def test_failure(self):
        completed = MagicMock(returncode=255, stdout="", stderr="refused")
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh.subprocess.run", return_value=completed):
            ok, msg = check_connection("srv")
        assert ok is False
        assert "refused" in msg

    def test_timeout(self):
        with patch("tools.ssh.get_host", return_value=HOST_CFG), \
             patch("tools.ssh.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("ssh", 15)):
            ok, msg = check_connection("srv")
        assert ok is False
        assert "timeout" in msg.lower()