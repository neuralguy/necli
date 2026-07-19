from __future__ import annotations

import logging

from config.settings import get, set_value

logger = logging.getLogger(__name__)


def _get_hosts() -> dict[str, dict]:
    return get("ssh_hosts", {})


def _save_hosts(hosts: dict[str, dict]) -> None:
    set_value("ssh_hosts", hosts)


def list_hosts() -> dict[str, dict]:
    return dict(_get_hosts())


def get_host(alias: str) -> dict | None:
    return _get_hosts().get(alias)


def add_host(
    alias: str,
    host: str,
    user: str = "root",
    port: int = 22,
    key: str = "",
    confirm_dangerous: bool = True,
) -> dict:
    entry = {
        "host": host,
        "user": user,
        "port": port,
    }
    if key:
        entry["key"] = key
    entry["confirm_dangerous"] = confirm_dangerous
    hosts = _get_hosts()
    hosts[alias] = entry
    _save_hosts(hosts)
    logger.info("SSH host added: %s -> %s@%s:%d", alias, user, host, port)
    return entry


def remove_host(alias: str) -> bool:
    hosts = _get_hosts()
    if alias not in hosts:
        return False
    del hosts[alias]
    _save_hosts(hosts)
    logger.info("SSH host removed: %s", alias)
    return True


def parse_host_string(s: str) -> tuple[str, str, int]:
    """Парсит 'user@host:port' -> (user, host, port). user и port опциональны."""
    user = "root"
    port = 22
    rest = s
    if "@" in rest:
        user, rest = rest.split("@", 1)
    if ":" in rest:
        host_part, port_str = rest.rsplit(":", 1)
        try:
            port = int(port_str)
            rest = host_part
        except ValueError:
            pass
    return user, rest, port
