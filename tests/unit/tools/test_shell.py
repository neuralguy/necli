"""tools/shell.py — execute_shell + блокировки."""

import subprocess
from unittest.mock import patch

from tools.models import ToolCall
from tools.shell import (
    _is_blocked,
    _is_file_write_via_shell,
    _strip_shell_prefix,
    execute_shell,
)


def _call(cmd: str) -> ToolCall:
    return ToolCall(command=cmd, tool_name="shell", args={})


class TestIsBlocked:
    def test_rm_rf_root(self):
        assert _is_blocked("rm -rf /") is not None

    def test_mkfs(self):
        assert _is_blocked("mkfs.ext4 /dev/sda1") is not None

    def test_fork_bomb(self):
        assert _is_blocked(":(){ :|:& };:") is not None

    def test_benign(self):
        assert _is_blocked("ls -la") is None

    def test_case_insensitive(self):
        assert _is_blocked("RM -RF /") is not None


class TestFileWriteViaShell:
    def test_heredoc_cat(self):
        msg = _is_file_write_via_shell("cat > x.py << EOF")
        assert msg and "create_file" in msg

    def test_heredoc_tee(self):
        msg = _is_file_write_via_shell("tee x.py << EOF")
        assert msg

    def test_cat_redirect(self):
        msg = _is_file_write_via_shell("cat > x.py")
        assert msg and "create_file" in msg

    def test_tee_redirect(self):
        # tee пишет в файл и без heredoc — блокируем.
        msg = _is_file_write_via_shell("echo hi | tee x.py")
        assert msg and "create_file" in msg

    def test_tee_append_redirect(self):
        msg = _is_file_write_via_shell("echo hi | tee -a x.py")
        assert msg and "create_file" in msg

    def test_echo_redirect(self):
        # echo-редирект — осознанно НЕ блокируем (мелкие inline-операции).
        assert _is_file_write_via_shell("echo hi > x.py") is None

    def test_printf_redirect(self):
        # printf-редирект тоже осознанно НЕ блокируем.
        assert _is_file_write_via_shell("printf 'x' > x.py") is None

    def test_echo_with_pipe_allowed(self):
        # pipe в grep не должен блокироваться
        assert _is_file_write_via_shell("echo hi | grep h") is None

    def test_normal_command(self):
        assert _is_file_write_via_shell("ls -la") is None

    def test_quoted_cat_redirect_not_flagged(self):
        # 'cat >' внутри кавычек — литерал, не реальная запись файла.
        assert _is_file_write_via_shell('echo "cat > x.py"') is None

    def test_quoted_heredoc_not_flagged(self):
        # heredoc-подобный текст внутри кавычек не должен срабатывать.
        assert _is_file_write_via_shell("echo 'cat > x.py << EOF'") is None

    def test_quoted_tee_not_flagged(self):
        assert _is_file_write_via_shell('grep "tee file" log.txt') is None

    def test_real_cat_redirect_still_flagged(self):
        # Реальный 'cat >' вне кавычек по-прежнему блокируется.
        assert _is_file_write_via_shell('cat > x.py') is not None

    def test_unbalanced_quotes_fail_safe(self):
        # При незакрытой кавычке остаёмся осторожными — блокируем реальную запись.
        msg = _is_file_write_via_shell('cat > x.py "unterminated')
        assert msg and "create_file" in msg


class TestStripShellPrefix:
    def test_strips(self):
        assert _strip_shell_prefix("shell ls") == "ls"

    def test_strips_tab(self):
        assert _strip_shell_prefix("shell\tls") == "ls"

    def test_no_prefix_kept(self):
        assert _strip_shell_prefix("ls") == "ls"

    def test_shell_in_word_kept(self):
        # 'shellcheck' не должно трактоваться как префикс
        assert _strip_shell_prefix("shellcheck x.sh") == "shellcheck x.sh"


class TestExecuteShell:
    def test_simple_echo(self, tmp_workdir):
        r = execute_shell(_call("echo hello"))
        assert r.status == "ok"
        assert "hello" in r.output
        assert r.exit_code == 0

    def test_nonzero_exit(self, tmp_workdir):
        r = execute_shell(_call("false"))
        assert r.status == "error"
        assert r.exit_code != 0

    def test_blocked(self, tmp_workdir):
        r = execute_shell(_call("rm -rf /"))
        assert r.status == "error"
        assert "rm -rf /" in r.output

    def test_cd_allowed_within_call(self, tmp_workdir):
        # cd теперь разрешён: работает внутри одного вызова.
        sub = tmp_workdir / "sub"
        sub.mkdir()
        r = execute_shell(_call("cd sub && pwd"))
        assert r.status == "ok"
        assert "sub" in r.output

    def test_cd_does_not_leak_between_calls(self, tmp_workdir):
        # Каждый вызов снова стартует из working dir — cd не «утекает».
        sub = tmp_workdir / "sub"
        sub.mkdir()
        execute_shell(_call("cd sub"))
        r = execute_shell(_call("pwd"))
        assert r.status == "ok"
        assert r.output.strip().rstrip("/") == str(tmp_workdir).rstrip("/")

    def test_cd_absolute_path_allowed(self, tmp_workdir):
        # Работа в произвольной директории через абсолютный путь.
        r = execute_shell(_call("cd /tmp && pwd"))
        assert r.status == "ok"
        assert "/tmp" in r.output

    def test_heredoc_blocked(self, tmp_workdir):
        r = execute_shell(_call("cat > x.py << EOF"))
        assert r.status == "error"
        assert "create_file" in r.output

    def test_empty_command(self, tmp_workdir):
        r = execute_shell(_call(""))
        assert r.status == "error"

    def test_strips_shell_prefix(self, tmp_workdir):
        r = execute_shell(_call("shell echo hi"))
        assert r.status == "ok"
        assert "hi" in r.output

    def test_stderr_captured(self, tmp_workdir):
        # python -c печатает в stderr без shell-редиректа > (который блокируется защитой)
        r = execute_shell(_call("python3 -c 'import sys; sys.stderr.write(\"oops\\n\")'"))
        assert "oops" in r.output
        assert "[stderr]" in r.output

    def test_timeout_mocked(self, tmp_workdir):
        # Симулируем timeout через мок subprocess.run
        with patch("tools.shell.subprocess.run", side_effect=subprocess.TimeoutExpired("x", 1)):
            r = execute_shell(_call("sleep 10"))
        assert r.status == "error"
        assert "Timeout" in r.output

    def test_no_output_marker(self, tmp_workdir):
        r = execute_shell(_call("true"))
        assert r.status == "ok"
        # "true" не печатает ничего
        assert "no output" in r.output.lower()
