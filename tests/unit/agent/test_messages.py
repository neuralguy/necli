"""agent/messages.py — helpers (без асинхронного loop)."""

import asyncio

from agent.messages import (
    _truncate,
    is_api_proxy_error,
    is_likely_truncated,
    _build_tree_lines,
    gather_proof,
    gather_dir_context,
)


class TestTruncate:
    def test_short_passthrough(self):
        text = "small"
        assert _truncate(text, max_len=100) == text

    def test_long_truncated_with_expand_hint(self):
        text = "x" * 5000
        result = _truncate(text, max_len=200)
        assert len(result) < 5000
        assert "expand_tool_result" in result

    def test_long_keeps_head_and_tail(self):
        text = "HEAD" + ("x" * 5000) + "TAIL"
        result = _truncate(text, max_len=200)
        assert "HEAD" in result
        assert "TAIL" in result


class TestIsApiProxyError:
    def test_empty(self):
        assert is_api_proxy_error("") is False

    def test_502_with_proxy(self):
        text = "HTTP error 502 from ask_proxy"
        assert is_api_proxy_error(text) is True

    def test_503_with_proxy(self):
        assert is_api_proxy_error("HTTP error 503 ask_proxy timed out") is True

    def test_524_with_proxy(self):
        assert is_api_proxy_error("HTTP error 524 origin timeout ask_proxy") is True

    def test_request_aborted_proxy(self):
        assert is_api_proxy_error("request aborted via ask_proxy") is True

    def test_unrelated_500(self):
        assert is_api_proxy_error("HTTP 500 internal error") is False

    def test_plain_text(self):
        assert is_api_proxy_error("just a response") is False


class TestIsLikelyTruncated:
    def test_short_returns_false(self):
        assert is_likely_truncated("short text") is False

    def test_odd_fence_count_in_long_text(self):
        # длинный текст с непарными ``` → True
        text = "x" * 50000 + "\n```\nopen"
        assert is_likely_truncated(text) is True

    def test_ends_with_comma_in_long_text(self):
        text = "x" * 50000 + ","
        assert is_likely_truncated(text) is True

    def test_balanced_fences_no_truncate(self):
        text = "x" * 50000 + "\n```\ncode\n```\n"
        assert is_likely_truncated(text) is False


class TestBuildTreeLines:
    def test_root_present(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        lines = _build_tree_lines(tmp_workdir, max_depth=1)
        # первая строка — имя корня
        assert lines[0].endswith("/")
        assert any("a.py" in ln for ln in lines)

    def test_ignores_pycache(self, tmp_workdir):
        (tmp_workdir / "__pycache__").mkdir()
        (tmp_workdir / "__pycache__" / "ignored.pyc").write_text("x")
        (tmp_workdir / "visible.py").write_text("y")
        lines = _build_tree_lines(tmp_workdir, max_depth=2)
        assert any("visible.py" in ln for ln in lines)
        assert not any("__pycache__" in ln for ln in lines)

    def test_ignores_hidden(self, tmp_workdir):
        (tmp_workdir / ".secret").write_text("x")
        (tmp_workdir / "open.py").write_text("y")
        lines = _build_tree_lines(tmp_workdir)
        assert any("open.py" in ln for ln in lines)
        assert not any(".secret" in ln for ln in lines)


class TestGatherProof:
    def test_returns_workdir_and_date_and_tree(self, tmp_workdir):
        (tmp_workdir / "a.py").write_text("x")
        result = asyncio.run(gather_proof(str(tmp_workdir)))
        assert str(tmp_workdir) in result
        assert "date" in result.lower() or "Today" in result
        assert "a.py" in result


class TestGatherDirContext:
    def test_no_agents_md(self, tmp_workdir):
        result = asyncio.run(gather_dir_context(str(tmp_workdir)))
        assert result == ""

    def test_reads_agents_md(self, tmp_workdir):
        (tmp_workdir / "AGENTS.md").write_text("# project guide\nrules here")
        result = asyncio.run(gather_dir_context(str(tmp_workdir)))
        # Содержимое не вшивается — только заметка о наличии файла.
        assert "AGENTS.md" in result

    def test_empty_agents_md_still_noted(self, tmp_workdir):
        # Заметка зависит от наличия файла, а не его содержимого.
        (tmp_workdir / "AGENTS.md").write_text("   \n\n")
        result = asyncio.run(gather_dir_context(str(tmp_workdir)))
        assert "AGENTS.md" in result