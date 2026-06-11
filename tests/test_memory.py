"""Тесты для memory-системы (src/memory/memdir)."""

import pytest


@pytest.fixture
def mem_workdir(tmp_path, monkeypatch):
    """Изолирует MEMORY_DIR во временной папке и фиксирует working_dir."""
    from config import paths

    monkeypatch.setattr(paths, "MEMORY_DIR", tmp_path / "memory")
    return str(tmp_path / "proj")


def test_write_and_read_roundtrip(mem_workdir):
    from memory import read_memory, write_memory

    mf = write_memory(
        "user profile", "User is a Go dev, new to React.",
        mtype="user", today="2026-06-11", working_dir=mem_workdir,
    )
    assert mf.path.exists()
    back = read_memory(mf.path)
    assert back is not None
    assert back.type == "user"
    assert back.created == "2026-06-11"
    assert back.updated == "2026-06-11"
    assert "Go dev" in back.body


def test_invalid_type_falls_back_to_project(mem_workdir):
    from memory import write_memory

    mf = write_memory("x", "body", mtype="bogus", today="2026-06-11", working_dir=mem_workdir)
    assert mf.type == "project"


def test_update_preserves_created(mem_workdir):
    from memory import read_memory, write_memory

    write_memory("note", "v1", mtype="project", today="2026-06-01", working_dir=mem_workdir)
    mf = write_memory("note", "v2", mtype="project", today="2026-06-11", working_dir=mem_workdir)
    back = read_memory(mf.path)
    assert back.created == "2026-06-01"  # сохранён
    assert back.updated == "2026-06-11"  # обновлён
    assert back.body == "v2"


def test_safe_filename(mem_workdir):
    from memory import write_memory

    mf = write_memory("My Cool Note!!!", "b", working_dir=mem_workdir)
    assert mf.name.endswith(".md")
    assert " " not in mf.name
    assert "!" not in mf.name


def test_scan_memories(mem_workdir):
    from memory import scan_memories, write_memory

    write_memory("a", "aaa", mtype="user", today="2026-06-11", working_dir=mem_workdir)
    write_memory("b", "bbb", mtype="project", today="2026-06-11", working_dir=mem_workdir)
    files = scan_memories(mem_workdir)
    assert len(files) == 2
    names = {f.name for f in files}
    assert names == {"a.md", "b.md"}


def test_scan_empty(mem_workdir):
    from memory import scan_memories

    assert scan_memories(mem_workdir) == []


def test_format_memory_block_empty(mem_workdir):
    from memory import format_memory_block

    assert format_memory_block(mem_workdir) == ""


def test_format_memory_block_groups_by_type(mem_workdir):
    from memory import format_memory_block, write_memory

    write_memory("proj-note", "project context", mtype="project", today="2026-06-11", working_dir=mem_workdir)
    write_memory("who", "user info", mtype="user", today="2026-06-11", working_dir=mem_workdir)
    block = format_memory_block(mem_workdir)
    assert "<persistent_memory>" in block
    assert "</persistent_memory>" in block
    assert "user info" in block
    assert "project context" in block
    # user идёт раньше project (порядок MEMORY_TYPES).
    assert block.index("user info") < block.index("project context")


def test_format_memory_block_respects_max_chars(mem_workdir):
    from memory import format_memory_block, write_memory

    write_memory("big", "x" * 5000, mtype="project", today="2026-06-11", working_dir=mem_workdir)
    write_memory("big2", "y" * 5000, mtype="project", today="2026-06-11", working_dir=mem_workdir)
    block = format_memory_block(mem_workdir, max_chars=4000)
    assert "усечена" in block


def test_project_isolation(mem_workdir, tmp_path, monkeypatch):
    """Память одного проекта не видна в другом."""
    from config import paths
    from memory import scan_memories, write_memory

    monkeypatch.setattr(paths, "MEMORY_DIR", tmp_path / "memory")
    proj_a = str(tmp_path / "a")
    proj_b = str(tmp_path / "b")
    write_memory("note", "secret-a", mtype="project", today="2026-06-11", working_dir=proj_a)
    assert scan_memories(proj_a)
    assert scan_memories(proj_b) == []


def test_system_prompt_includes_memory(mem_workdir):
    from memory import write_memory
    from system_prompt import _build_memory_block

    write_memory("pref", "User prefers terse replies.", mtype="feedback",
                 today="2026-06-11", working_dir=mem_workdir)
    block = _build_memory_block(mem_workdir)
    assert "terse replies" in block


# ── extract.py: фоновое извлечение долговременной памяти ──────────────────────

class TestExtractParser:
    def test_plain_array(self):
        from memory.extract import _parse_items
        items = _parse_items('[{"name":"a","type":"user","body":"x"}]')
        assert len(items) == 1 and items[0]["name"] == "a"

    def test_fenced_json(self):
        from memory.extract import _parse_items
        raw = '```json\n[{"name":"b","body":"y","type":"feedback"}]\n```'
        assert len(_parse_items(raw)) == 1

    def test_preamble_and_trailing_text(self):
        from memory.extract import _parse_items
        assert len(_parse_items('Sure: [{"name":"c","body":"z"}] done')) == 1

    def test_single_object(self):
        from memory.extract import _parse_items
        assert len(_parse_items('{"name":"d","body":"q","type":"project"}')) == 1

    def test_empty_and_garbage(self):
        from memory.extract import _parse_items
        assert _parse_items("[]") == []
        assert _parse_items("no json here") == []
        assert _parse_items("") == []

    def test_drops_items_missing_name_or_body(self):
        from memory.extract import _parse_items
        items = _parse_items('[{"name":"ok","body":"b"},{"name":"","body":"b"},{"name":"x"}]')
        assert len(items) == 1


def test_extract_memories_writes_valid_items(mem_workdir, monkeypatch):
    import asyncio
    import apis.agent_adapter as aa
    from memory import scan_memories, extract_memories

    async def fake_api(prompt):
        assert "TRANSCRIPT" in prompt and "EXISTING MEMORIES" in prompt
        return ('[{"name":"User prefers Rust","type":"user","body":"Likes Rust."},'
                '{"name":"bad","type":"weird","body":""}]')

    monkeypatch.setattr(aa, "api_extract_memory", fake_api)
    n = asyncio.run(extract_memories("user: hi\nassistant: ok", working_dir=mem_workdir))
    assert n == 1  # пустой body отбрасывается
    files = scan_memories(mem_workdir)
    assert len(files) == 1
    assert files[0].type == "user"
    assert "Rust" in files[0].body


def test_extract_memories_empty_transcript_is_noop(mem_workdir, monkeypatch):
    import asyncio
    import apis.agent_adapter as aa
    from memory import extract_memories

    async def fake_api(prompt):  # не должен вызываться
        raise AssertionError("model should not be called for empty transcript")

    monkeypatch.setattr(aa, "api_extract_memory", fake_api)
    assert asyncio.run(extract_memories("   ", working_dir=mem_workdir)) == 0


def test_extract_memories_swallows_model_error(mem_workdir, monkeypatch):
    import asyncio
    import apis.agent_adapter as aa
    from memory import extract_memories

    async def boom(prompt):
        raise RuntimeError("provider down")

    monkeypatch.setattr(aa, "api_extract_memory", boom)
    # никогда не бросает наружу — возвращает 0
    assert asyncio.run(extract_memories("user: hi", working_dir=mem_workdir)) == 0
