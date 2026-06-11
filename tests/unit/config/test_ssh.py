"""config/ssh.py — SSH-хосты в config.json."""

from config import ssh as ssh_cfg


class TestParseHostString:
    def test_full(self, isolated_data):
        assert ssh_cfg.parse_host_string("user@example.com:2222") == ("user", "example.com", 2222)

    def test_no_port(self, isolated_data):
        assert ssh_cfg.parse_host_string("user@example.com") == ("user", "example.com", 22)

    def test_no_user(self, isolated_data):
        assert ssh_cfg.parse_host_string("example.com:22") == ("root", "example.com", 22)

    def test_only_host(self, isolated_data):
        assert ssh_cfg.parse_host_string("example.com") == ("root", "example.com", 22)

    def test_invalid_port_kept_in_host(self, isolated_data):
        # некорректный port → остаётся в host part
        user, host, port = ssh_cfg.parse_host_string("user@host:notnumber")
        assert user == "user"
        assert port == 22


class TestAddRemove:
    def test_add(self, isolated_data):
        entry = ssh_cfg.add_host("server1", "1.2.3.4", user="ubuntu", port=2222)
        assert entry["host"] == "1.2.3.4"
        assert entry["user"] == "ubuntu"
        assert entry["port"] == 2222
        assert ssh_cfg.get_host("server1") == entry

    def test_add_with_key(self, isolated_data):
        entry = ssh_cfg.add_host("s", "h", key="/path/to/key")
        assert entry["key"] == "/path/to/key"

    def test_no_key_field_when_absent(self, isolated_data):
        entry = ssh_cfg.add_host("s", "h")
        assert "key" not in entry

    def test_remove_existing(self, isolated_data):
        ssh_cfg.add_host("s", "h")
        assert ssh_cfg.remove_host("s") is True
        assert ssh_cfg.get_host("s") is None

    def test_remove_missing(self, isolated_data):
        assert ssh_cfg.remove_host("nope") is False


class TestListHosts:
    def test_empty(self, isolated_data):
        assert ssh_cfg.list_hosts() == {}

    def test_multiple(self, isolated_data):
        ssh_cfg.add_host("a", "1.1.1.1")
        ssh_cfg.add_host("b", "2.2.2.2")
        hosts = ssh_cfg.list_hosts()
        assert set(hosts.keys()) == {"a", "b"}

    def test_returns_copy(self, isolated_data):
        ssh_cfg.add_host("a", "1.1.1.1")
        hosts = ssh_cfg.list_hosts()
        hosts["x"] = "spoiled"
        # модификация не должна затронуть persisted
        assert "x" not in ssh_cfg.list_hosts()