"""SSH-инструмент: выполнение команд на удалённых серверах через ControlMaster."""

import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from config.paths import BASE_DIR
from config.ssh import get_host, list_hosts
from tools.models import ToolCall, ToolResult
from ui.poll import run_poll_step

logger = logging.getLogger(__name__)

SOCKETS_DIR = BASE_DIR / "ssh_sockets"

_EXECUTION_TIMEOUT = 120

_DANGEROUS_COMMANDS = [
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=/dev",
    ":(){ :|:& };:", "> /dev/sda",
    "reboot", "shutdown", "halt", "init 0", "init 6",
    "poweroff", "systemctl reboot", "systemctl poweroff",
]

_DANGEROUS_PATTERNS = [
    "rm -rf", "rm -r /", "mkfs.", "wipefs",
    "chmod -r 777 /", "chown -r",
    "systemctl stop", "systemctl restart", "systemctl disable",
    "service stop", "service restart",
    "kill -9", "killall", "pkill",
    "iptables -f", "ufw disable",
    "docker rm", "docker stop", "docker system prune",
    "drop table", "drop database", "truncate",
]

_BLOCKED_INTERACTIVE = [
    "vim", "nano", "vi", "emacs", "top", "htop",
    "less", "more", "man", "ssh", "tmux", "screen",
]


def _ssh_env() -> dict:
    env = dict(os.environ)
    if sys.platform == "win32":
        env["PYTHONUTF8"] = "1"
    else:
        env["LC_ALL"] = "C.UTF-8"
        env["LANG"] = "C.UTF-8"
    return env

def _socket_path(alias: str) -> Path:
    SOCKETS_DIR.mkdir(parents=True, exist_ok=True)
    return SOCKETS_DIR / f"{alias}.sock"

def _ssh_bin() -> str | None:
    return "ssh" if shutil.which("ssh") else None


def _scp_bin() -> str | None:
    return "scp" if shutil.which("scp") else None


def _controlmaster_supported() -> bool:
    return sys.platform != "win32"


def _build_ssh_args(host_cfg: dict, alias: str) -> list[str]:
    ssh = _ssh_bin()
    if not ssh:
        raise FileNotFoundError("ssh executable not found. Install OpenSSH Client.")
    args = [
        ssh,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-p", str(host_cfg.get("port", 22)),
    ]
    if _controlmaster_supported():
        args[3:3] = [
            "-o", f"ControlPath={_socket_path(alias)}",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=300",
        ]
    key = host_cfg.get("key", "")
    if key:
        args.extend(["-i", os.path.expanduser(key)])
    user = host_cfg.get("user", "root")
    host = host_cfg["host"]
    args.append(f"{user}@{host}")
    return args


def _is_blocked_interactive(command: str) -> str | None:
    first_word = command.strip().split()[0] if command.strip() else ""
    if first_word in _BLOCKED_INTERACTIVE:
        return f"Интерактивная команда '{first_word}' не поддерживается через SSH-инструмент."
    return None


def _is_hard_blocked(command: str) -> str | None:
    cmd_lower = command.strip().lower()
    for blocked in _DANGEROUS_COMMANDS:
        if blocked.lower() in cmd_lower:
            return f"Заблокировано: '{blocked}'"
    return None


def _needs_confirmation(command: str) -> bool:
    """Токенизируем команду и матчим опасные паттерны по токенам, а не по
    сырой подстроке. Так `echo "kill -9 docs"` не срабатывает ложно, а
    `rm  -rf` / `rm -fr` (двойной пробел / переставленные флаги) ловятся.
    Консервативно: при невозможности распарсить — считаем опасной.
    """
    try:
        tokens = shlex.split(command)
    except ValueError:
        return True
    if not tokens:
        return False

    norm = " ".join(tokens).lower()

    def _has_rm_recursive() -> bool:
        # ловит rm -rf / rm -fr / rm -r (в любом порядке флагов, в т.ч.
        # с лишними пробелами — shlex их уже схлопнул в отдельные токены)
        for i, tok in enumerate(tokens):
            if tok != "rm":
                continue
            for arg in tokens[i + 1:]:
                if arg.startswith("-") and not arg.startswith("--"):  # noqa: SIM102
                    if "r" in arg[1:].lower():
                        return True
        return False

    if _has_rm_recursive():
        return True

    for p in _DANGEROUS_PATTERNS:
        pl = p.lower()
        if " " in pl:
            if pl in norm:
                return True
        else:
            if pl in (t.lower() for t in tokens):
                return True
    return False


def _confirm_command(command: str, alias: str) -> bool:
    answer = run_poll_step(
        f"Выполнить на [{alias}]?",
        [f"✓ Выполнить: {command[:80]}", "✗ Пропустить"],
    )
    return answer.startswith("✓")


def _run_ssh_command(host_cfg: dict, alias: str, command: str) -> ToolResult:
    blocked = _is_blocked_interactive(command)
    if blocked:
        return ToolResult(
            name="ssh", status="error",
            output=blocked, exit_code=-1,
            command=f"ssh:{alias} {command}",
        )

    hard_blocked = _is_hard_blocked(command)
    if hard_blocked:
        return ToolResult(
            name="ssh", status="error",
            output=hard_blocked, exit_code=-1,
            command=f"ssh:{alias} {command}",
        )

    if host_cfg.get("confirm_dangerous", True) and _needs_confirmation(command):  # noqa: SIM102
        if not _confirm_command(command, alias):
            return ToolResult(
                name="ssh", status="error",
                output="Команда отклонена пользователем.",
                exit_code=-1,
                command=f"ssh:{alias} {command}",
            )

    ssh_args = _build_ssh_args(host_cfg, alias)
    ssh_args.append(command)

    logger.info("SSH exec on %s: %s", alias, command[:200])

    try:
        result = subprocess.run(
            ssh_args,
            capture_output=True, text=True,
            timeout=_EXECUTION_TIMEOUT,
            env=_ssh_env(),
        )
        parts = []
        if result.stdout:
            parts.append(result.stdout)
        if result.stderr:
            parts.append(f"[stderr]\n{result.stderr}")
        output = "\n".join(parts) if parts else "(no output)"

        return ToolResult(
            name="ssh", status="ok" if result.returncode == 0 else "error",
            output=output, exit_code=result.returncode,
            command=f"ssh:{alias} {command}",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            name="ssh", status="error",
            output=f"Timeout: {_EXECUTION_TIMEOUT}s",
            exit_code=-1, command=f"ssh:{alias} {command}",
        )
    except Exception as e:
        return ToolResult(
            name="ssh", status="error",
            output=f"SSH error: {e}",
            exit_code=-1, command=f"ssh:{alias} {command}",
        )


def _run_scp(host_cfg: dict, alias: str, src: str, dest: str, upload: bool) -> ToolResult:
    scp = _scp_bin()
    if not scp:
        return ToolResult(
            name="ssh", status="error",
            output="scp executable not found. Install OpenSSH Client.",
            exit_code=-1, command=f"scp:{alias}",
        )
    scp_cmd = [
        scp,
        "-o", "ConnectTimeout=10",
        "-P", str(host_cfg.get("port", 22)),
    ]
    if _controlmaster_supported():
        scp_cmd[1:1] = [
            "-o", f"ControlPath={_socket_path(alias)}",
            "-o", "ControlMaster=auto",
            "-o", "ControlPersist=300",
        ]
    key = host_cfg.get("key", "")
    if key:
        scp_cmd.extend(["-i", os.path.expanduser(key)])

    remote_prefix = f"{host_cfg.get('user', 'root')}@{host_cfg['host']}"

    # Удалённый путь scp прогоняет через шелл на той стороне, поэтому его
    # нужно экранировать (пробелы/метасимволы иначе ломают передачу или
    # раскрываются удалённым шеллом). Локальный путь — обычный argv-токен.
    if upload:
        scp_cmd.extend([src, f"{remote_prefix}:{shlex.quote(dest)}"])
        action = f"upload {src} -> {alias}:{dest}"
    else:
        scp_cmd.extend([f"{remote_prefix}:{shlex.quote(src)}", dest])
        action = f"download {alias}:{src} -> {dest}"

    logger.info("SCP %s", action)

    try:
        result = subprocess.run(
            scp_cmd,
            capture_output=True, text=True,
            timeout=_EXECUTION_TIMEOUT,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        if not output:
            output = f"OK: {action}"

        return ToolResult(
            name="ssh", status="ok" if result.returncode == 0 else "error",
            output=output, exit_code=result.returncode,
            command=f"scp:{alias} {action}",
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            name="ssh", status="error",
            output=f"SCP timeout: {_EXECUTION_TIMEOUT}s",
            exit_code=-1, command=f"scp:{alias} {action}",
        )
    except Exception as e:
        return ToolResult(
            name="ssh", status="error",
            output=f"SCP error: {e}",
            exit_code=-1, command=f"scp:{alias} {action}",
        )


def execute_ssh(call: ToolCall) -> ToolResult:
    args = call.args
    alias = args.get("host", "").strip()
    if not alias:
        alias = call.command.strip()

    if not alias:
        hosts = list_hosts()
        if hosts:
            host_list = ", ".join(hosts.keys())
            return ToolResult(
                name="ssh", status="error",
                output=f"Укажи хост. Доступные: {host_list}",
                exit_code=1, command="ssh",
            )
        return ToolResult(
            name="ssh", status="error",
            output="Нет настроенных SSH-хостов. Попроси пользователя добавить через /ssh.",
            exit_code=1, command="ssh",
        )

    host_cfg = get_host(alias)
    if host_cfg is None:
        hosts = list_hosts()
        host_list = ", ".join(hosts.keys()) if hosts else "нет хостов"
        return ToolResult(
            name="ssh", status="error",
            output=f"Хост '{alias}' не найден. Доступные: {host_list}",
            exit_code=1, command=f"ssh {alias}",
        )

    # SCP mode
    upload_path = args.get("upload")
    download_path = args.get("download")
    if upload_path:
        dest = args.get("dest", "/tmp/")
        return _run_scp(host_cfg, alias, upload_path, dest, upload=True)
    if download_path:
        dest = args.get("dest", ".")
        return _run_scp(host_cfg, alias, download_path, dest, upload=False)

    # Command mode
    command = args.get("command", "").strip()
    if not command:
        command = call.command.strip()
        # Убираем alias из начала если он там есть
        if command.startswith(alias):
            command = command[len(alias):].strip()

    if not command:
        return ToolResult(
            name="ssh", status="error",
            output="Укажи команду в аргументе 'command'.",
            exit_code=1, command=f"ssh {alias}",
        )

    return _run_ssh_command(host_cfg, alias, command)


def check_connection(alias: str) -> tuple[bool, str]:
    host_cfg = get_host(alias)
    if not host_cfg:
        return False, f"Хост '{alias}' не найден."

    ssh_args = _build_ssh_args(host_cfg, alias)
    ssh_args.append("echo ok")
    try:
        result = subprocess.run(
            ssh_args,
            capture_output=True, text=True,
            timeout=15,
        )
        if result.returncode == 0:
            return True, f"{host_cfg.get('user', 'root')}@{host_cfg['host']}:{host_cfg.get('port', 22)}"
        return False, (result.stderr or result.stdout or "unknown error").strip()
    except subprocess.TimeoutExpired:
        return False, "Connection timeout (15s)"
    except Exception as e:
        return False, str(e)


def close_all_connections() -> int:
    """Закрывает все ControlMaster соединения. Возвращает количество закрытых."""
    if not _controlmaster_supported() or not SOCKETS_DIR.exists():
        return 0
    closed = 0
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        return 0
    for sock in SOCKETS_DIR.glob("*.sock"):
        try:
            subprocess.run(
                [ssh_bin, "-o", f"ControlPath={sock}", "-O", "exit", "dummy"],
                capture_output=True, timeout=5,
            )
            closed += 1
        except Exception:
            logger.debug("ssh ControlMaster exit failed: %s", sock, exc_info=True)
        try:
            sock.unlink(missing_ok=True)
        except Exception:
            logger.debug("ssh sock cleanup failed: %s", sock, exc_info=True)
    return closed


def get_active_connections() -> list[str]:
    """Возвращает список алиасов с ЖИВЫМИ ControlMaster сокетами.

    Наличие файла сокета не значит что мастер жив (ControlPersist мог
    истечь, процесс умереть) — проверяем через `ssh -O check`. Мёртвые
    сокеты подчищаем, чтобы не висели в статусной строке.
    """
    if not _controlmaster_supported() or not SOCKETS_DIR.exists():
        return []
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        return []
    active = []
    for sock in SOCKETS_DIR.glob("*.sock"):
        alias = sock.stem
        try:
            result = subprocess.run(
                [ssh_bin, "-o", f"ControlPath={sock}", "-O", "check", "dummy"],
                capture_output=True, timeout=5,
            )
            alive = result.returncode == 0
        except Exception:
            logger.debug("ssh ControlMaster check failed: %s", sock, exc_info=True)
            alive = False
        if alive:
            active.append(alias)
        else:
            try:
                sock.unlink(missing_ok=True)
            except Exception:
                logger.debug("ssh dead sock cleanup failed: %s", sock, exc_info=True)
    return active
