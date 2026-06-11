"""ui/_keyreader.py — нормализация клавиш и парсинг escape-последовательностей.

Терминал/stdin мокаются; реальный TTY не используется.
"""

from unittest import mock

import pytest

import ui._keyreader as kr

class TestNormalize:
    def test_enter_variants(self):
        assert kr._normalize("\r") == "enter"
        assert kr._normalize("\n") == "enter"

    def test_ctrl_c_and_d(self):
        assert kr._normalize("\x03") == "ctrl-c"
        assert kr._normalize("\x04") == "ctrl-c"

    def test_q_quits(self):
        assert kr._normalize("q") == "ctrl-c"
        assert kr._normalize("Q") == "ctrl-c"

    def test_vim_navigation(self):
        assert kr._normalize("j") == "down"
        assert kr._normalize("k") == "up"

    def test_plain_char_passthrough(self):
        assert kr._normalize("a") == "a"
        assert kr._normalize("Z") == "Z"
        assert kr._normalize("5") == "5"

@pytest.mark.skipif(kr._IS_WIN, reason="POSIX-only escape parsing")
class TestReadKeyRawPosix:
    def _fd_reads(self, chunks):
        """Возвращает функцию-замену os.read, отдающую chunks по очереди."""
        it = iter(chunks)

        def fake_read(fd, n):
            return next(it).encode("utf-8")

        return fake_read

    def test_plain_char(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["a"])):
            assert kr._read_key_raw(0) == "a"

    def test_enter(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["\r"])):
            assert kr._read_key_raw(0) == "enter"

    def test_ctrl_c(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x03"])):
            assert kr._read_key_raw(0) == "ctrl-c"

    def test_arrow_up(self):
        # ESC, then select reports data ready, then "[A"
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "[A"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "up"

    def test_arrow_down(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "[B"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "down"

    def test_arrow_left(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "[D"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "left"

    def test_arrow_right(self):
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "[C"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "right"

    def test_lone_escape_no_followup(self):
        # ESC and select reports no data → escape
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b"])), \
             mock.patch.object(kr.select, "select", return_value=([], [], [])):
            assert kr._read_key_raw(0) == "escape"

    def test_unknown_csi_returns_second_char(self):
        # ESC [ Z (shift-tab) → second char "Z"
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "[Z"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "Z"

    def test_escape_followed_by_non_csi(self):
        # ESC then something not starting with "[" → escape
        with mock.patch.object(kr.os, "read", self._fd_reads(["\x1b", "OP"])), \
             mock.patch.object(kr.select, "select", return_value=([0], [], [])):
            assert kr._read_key_raw(0) == "escape"

@pytest.mark.skipif(kr._IS_WIN, reason="POSIX-only drain logic")
class TestDrainKeysPosix:
    def test_single_key_no_more(self):
        with mock.patch.object(kr.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(kr, "_read_key_raw", return_value="a"), \
             mock.patch.object(kr.select, "select", return_value=([], [], [])):
            assert kr.drain_keys() == "a"

    def test_returns_last_of_burst(self):
        seq = iter(["a", "b", "c"])
        ready = iter([([0], [], []), ([0], [], []), ([], [], [])])

        with mock.patch.object(kr.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(kr, "_read_key_raw", side_effect=lambda fd: next(seq)), \
             mock.patch.object(kr.select, "select", side_effect=lambda *a: next(ready)):
            assert kr.drain_keys() == "c"

    def test_stops_early_on_enter(self):
        seq = iter(["a", "enter", "ignored"])
        ready = iter([([0], [], []), ([0], [], []), ([], [], [])])

        with mock.patch.object(kr.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(kr, "_read_key_raw", side_effect=lambda fd: next(seq)), \
             mock.patch.object(kr.select, "select", side_effect=lambda *a: next(ready)):
            assert kr.drain_keys() == "enter"

    def test_stops_early_on_ctrl_c(self):
        seq = iter(["x", "ctrl-c"])
        ready = iter([([0], [], []), ([0], [], [])])

        with mock.patch.object(kr.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(kr, "_read_key_raw", side_effect=lambda fd: next(seq)), \
             mock.patch.object(kr.select, "select", side_effect=lambda *a: next(ready)):
            assert kr.drain_keys() == "ctrl-c"

@pytest.mark.skipif(kr._IS_WIN, reason="POSIX-only read_key wrapper")
class TestReadKeyPosix:
    def test_read_key_restores_termios(self):
        calls = {"setraw": 0, "tcsetattr": 0}

        with mock.patch.object(kr.sys.stdin, "fileno", return_value=0), \
             mock.patch.object(kr.termios, "tcgetattr", return_value="OLD"), \
             mock.patch.object(kr.termios, "tcsetattr",
                               side_effect=lambda *a: calls.__setitem__("tcsetattr", calls["tcsetattr"] + 1)), \
             mock.patch.object(kr.tty, "setraw",
                               side_effect=lambda fd: calls.__setitem__("setraw", calls["setraw"] + 1)), \
             mock.patch.object(kr, "_read_key_raw", return_value="x"):
            assert kr.read_key() == "x"
        assert calls["setraw"] == 1
        assert calls["tcsetattr"] == 1

class TestModuleStructure:
    def test_normalize_is_pure(self):
        # одна и та же входная клавиша всегда даёт один результат
        for ch in ("a", "j", "k", "\r", "\x03", "Q"):
            assert kr._normalize(ch) == kr._normalize(ch)