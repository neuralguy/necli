"""apis/_context_pruner.py — pruning старых read-результатов из истории.

Покрываем три триггера вытеснения (A modified-later / B superseded / C age+size)
в обеих архитектурах (text-mode и native), а также инварианты: последний раунд
не трогается, system сохраняется, оригинал не мутируется.
"""

from apis._context_pruner import (
    prune_messages,
    _extract_paths_from_cmd_tail,
    _paths_from_args,
    _scan_round_writes,
    _scan_read_paths,
    _should_evict,
    _KEEP_RECENT_ROUNDS,
    _MIN_EVICT_CHARS,
    _EVICT_MARKER,
    _BLOCK_SEP,
)
from apis.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage

def _big(n: int = _MIN_EVICT_CHARS + 500) -> str:
    return "x" * n

def _read_block(path: str, body: str) -> str:
    return f"$ read_files {path}\n{body}"

def _tc(name: str, path, tc_id: str = "c1") -> dict:
    return {"id": tc_id, "name": name, "args": {"path": path}, "type": "tool_call"}

class TestExtractPaths:
    def test_simple_path(self):
        assert _extract_paths_from_cmd_tail("agent/stream.py") == ["agent/stream.py"]

    def test_path_with_lines(self):
        assert _extract_paths_from_cmd_tail("agent/x.py:120-200") == ["agent/x.py"]

    def test_repr_list(self):
        got = _extract_paths_from_cmd_tail("['a.py', 'b.py']")
        assert got == ["a.py", "b.py"]

    def test_repr_list_double_quotes(self):
        got = _extract_paths_from_cmd_tail('["a.py", "b.py"]')
        assert got == ["a.py", "b.py"]

    def test_empty(self):
        assert _extract_paths_from_cmd_tail("   ") == []

    def test_only_first_token(self):
        assert _extract_paths_from_cmd_tail("a.py b.py") == ["a.py"]

class TestPathsFromArgs:
    def test_str_path(self):
        assert _paths_from_args({"path": "x.py"}) == ["x.py"]

    def test_list_path(self):
        assert _paths_from_args({"path": ["a.py", "b.py"]}) == ["a.py", "b.py"]

    def test_no_path(self):
        assert _paths_from_args({"foo": 1}) == []

    def test_not_dict(self):
        assert _paths_from_args("nope") == []

    def test_empty_str(self):
        assert _paths_from_args({"path": ""}) == []

class TestScanWrites:
    def test_text_mode_write(self):
        msgs = [
            HumanMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="next"),
        ]
        writes = _scan_round_writes(msgs)
        assert writes == {"a.py": 1}

    def test_native_write(self):
        msgs = [
            HumanMessage(content="q1"),
            HumanMessage(content="q2"),
            AIMessage(content="", tool_calls=[_tc("patch_file", "b.py")]),
        ]
        writes = _scan_round_writes(msgs)
        assert writes == {"b.py": 2}

    def test_keeps_max_round(self):
        msgs = [
            HumanMessage(content="$ write_file a.py\nx"),
            HumanMessage(content="$ write_file a.py\ny"),
        ]
        assert _scan_round_writes(msgs) == {"a.py": 2}

class TestScanReads:
    def test_text_mode_read(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", "body")),
            HumanMessage(content="next"),
        ]
        assert _scan_read_paths(msgs) == {"a.py": 1}

    def test_native_read(self):
        msgs = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[_tc("read_files", "a.py")]),
        ]
        assert _scan_read_paths(msgs) == {"a.py": 1}

    def test_keeps_max_round(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", "b1")),
            HumanMessage(content=_read_block("a.py", "b2")),
        ]
        assert _scan_read_paths(msgs) == {"a.py": 2}

class TestShouldEvict:
    def test_no_paths(self):
        assert _should_evict([], 1, 5, 9999, {}, {}) is None

    def test_trigger_a_modified_later(self):
        reason = _should_evict(["a.py"], 1, 5, 10, {"a.py": 3}, {})
        assert reason == "file modified in later round"

    def test_trigger_b_superseded(self):
        reason = _should_evict(["a.py"], 1, 5, 10, {}, {"a.py": 4})
        assert reason == "superseded by a later read of the same file"

    def test_trigger_c_age_and_size(self):
        reason = _should_evict(
            ["a.py"], 1, 1 + _KEEP_RECENT_ROUNDS, _MIN_EVICT_CHARS, {}, {"a.py": 1},
        )
        assert reason is not None
        assert "stale read" in reason

    def test_trigger_c_too_recent(self):
        # age < _KEEP_RECENT_ROUNDS → не вытесняем
        reason = _should_evict(
            ["a.py"], 4, 5, _MIN_EVICT_CHARS + 1000, {}, {"a.py": 4},
        )
        assert reason is None

    def test_trigger_c_too_small(self):
        # достаточно старый, но мелкий → не вытесняем
        reason = _should_evict(
            ["a.py"], 1, 10, _MIN_EVICT_CHARS - 1, {}, {"a.py": 1},
        )
        assert reason is None

class TestPruneMessagesBasic:
    def test_empty(self):
        out, stats = prune_messages([])
        assert out == []
        assert stats == {"pruned_blocks": 0, "saved_chars": 0}

    def test_single_round_untouched(self):
        msgs = [SystemMessage(content="sys"), HumanMessage(content=_read_block("a.py", _big()))]
        out, stats = prune_messages(msgs)
        assert stats["pruned_blocks"] == 0
        assert out[1].content == msgs[1].content

    def test_system_preserved(self):
        msgs = [
            SystemMessage(content="sys"),
            HumanMessage(content=_read_block("a.py", _big())),
            AIMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="last"),
        ]
        out, _ = prune_messages(msgs)
        assert isinstance(out[0], SystemMessage)
        assert out[0].content == "sys"

    def test_original_not_mutated(self):
        orig_body = _read_block("a.py", _big())
        msgs = [
            HumanMessage(content=orig_body),
            HumanMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="last"),
        ]
        prune_messages(msgs)
        assert msgs[0].content == orig_body

class TestPruneTextMode:
    def test_modified_later_evicted(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", _big())),  # round1 read
            HumanMessage(content="$ write_file a.py\nok"),       # round2 write
            HumanMessage(content="last"),                        # round3 (current)
        ]
        out, stats = prune_messages(msgs)
        assert _EVICT_MARKER in out[0].content
        assert stats["pruned_blocks"] == 1
        assert stats["saved_chars"] > 0

    def test_superseded_evicted(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", _big())),   # round1 read
            HumanMessage(content=_read_block("a.py", _big())),   # round2 read (newer)
            HumanMessage(content="last"),                        # round3 current
        ]
        out, stats = prune_messages(msgs)
        # round1 копия вытеснена (superseded), round2 (тоже не current) → superseded by? нет, max read=2
        assert _EVICT_MARKER in out[0].content
        # round2 — последняя копия, не superseded и не current; age=1 < KEEP → остаётся
        assert _EVICT_MARKER not in out[1].content
        assert stats["pruned_blocks"] == 1

    def test_age_size_evicted(self):
        # 1 чтение в round1, далее много пустых раундов → age >= KEEP, размер большой
        msgs = [HumanMessage(content=_read_block("a.py", _big()))]
        for i in range(_KEEP_RECENT_ROUNDS + 1):
            msgs.append(HumanMessage(content=f"round {i}"))
        out, stats = prune_messages(msgs)
        assert _EVICT_MARKER in out[0].content
        assert stats["pruned_blocks"] == 1

    def test_small_read_not_evicted_by_age(self):
        msgs = [HumanMessage(content=_read_block("a.py", "tiny"))]
        for i in range(_KEEP_RECENT_ROUNDS + 1):
            msgs.append(HumanMessage(content=f"round {i}"))
        out, stats = prune_messages(msgs)
        assert _EVICT_MARKER not in out[0].content
        assert stats["pruned_blocks"] == 0

    def test_last_round_never_touched(self):
        # большой read в последнем (current) раунде + write раньше — но current не трогаем
        msgs = [
            HumanMessage(content="$ write_file a.py\nok"),       # round1 write
            HumanMessage(content="q2"),                          # round2
            HumanMessage(content=_read_block("a.py", _big())),   # round3 current read
        ]
        out, stats = prune_messages(msgs)
        assert _EVICT_MARKER not in out[2].content
        assert stats["pruned_blocks"] == 0

    def test_placeholder_keeps_command_line(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", _big())),
            HumanMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="last"),
        ]
        out, _ = prune_messages(msgs)
        assert out[0].content.startswith("$ read_files a.py")
        assert "a.py" in out[0].content

    def test_multiblock_partial_eviction(self):
        block_a = _read_block("a.py", _big())
        block_b = _read_block("b.py", _big())
        msgs = [
            HumanMessage(content=block_a + _BLOCK_SEP + block_b),  # round1
            HumanMessage(content="$ write_file a.py\nok"),          # round2 modifies a only
            HumanMessage(content="last"),                           # round3 current
        ]
        out, stats = prune_messages(msgs)
        parts = out[0].content.split(_BLOCK_SEP)
        evicted = [p for p in parts if _EVICT_MARKER in p]
        kept = [p for p in parts if _EVICT_MARKER not in p]
        assert len(evicted) == 1 and "a.py" in evicted[0]
        assert len(kept) == 1 and "b.py" in kept[0]
        assert stats["pruned_blocks"] == 1

    def test_already_evicted_idempotent(self):
        msgs = [
            HumanMessage(content=_read_block("a.py", _big())),
            HumanMessage(content="$ write_file a.py\nok"),
            HumanMessage(content="last"),
        ]
        out1, _ = prune_messages(msgs)
        out2, stats2 = prune_messages(out1)
        assert stats2["pruned_blocks"] == 0
        assert out2[0].content == out1[0].content

class TestPruneNative:
    def test_native_read_evicted_by_modification(self):
        msgs = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[_tc("read_files", "a.py", "r1")]),
            ToolMessage(content=_big(), tool_call_id="r1", name="read_files"),
            HumanMessage(content="q2"),
            AIMessage(content="", tool_calls=[_tc("write_file", "a.py", "w1")]),
            ToolMessage(content="written", tool_call_id="w1", name="write_file"),
            HumanMessage(content="last"),  # round3 current
        ]
        out, stats = prune_messages(msgs)
        tool_msg = next(m for m in out if isinstance(m, ToolMessage) and m.tool_call_id == "r1")
        assert _EVICT_MARKER in tool_msg.content
        assert stats["pruned_blocks"] == 1
        assert stats["saved_chars"] > 0

    def test_native_current_round_not_evicted(self):
        msgs = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[_tc("write_file", "a.py", "w1")]),
            ToolMessage(content="ok", tool_call_id="w1", name="write_file"),
            HumanMessage(content="q2 last"),  # round2 current
            AIMessage(content="", tool_calls=[_tc("read_files", "a.py", "r1")]),
            ToolMessage(content=_big(), tool_call_id="r1", name="read_files"),
        ]
        out, stats = prune_messages(msgs)
        tool_msg = next(m for m in out if isinstance(m, ToolMessage) and m.tool_call_id == "r1")
        assert _EVICT_MARKER not in tool_msg.content
        assert stats["pruned_blocks"] == 0

    def test_native_non_read_toolmessage_untouched(self):
        big = _big()
        msgs = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[_tc("shell", "x", "s1")]),
            ToolMessage(content=big, tool_call_id="s1", name="shell"),
            HumanMessage(content="last"),
        ]
        out, stats = prune_messages(msgs)
        tool_msg = next(m for m in out if isinstance(m, ToolMessage))
        assert tool_msg.content == big
        assert stats["pruned_blocks"] == 0

    def test_native_unknown_call_id_kept(self):
        msgs = [
            HumanMessage(content="q1"),
            HumanMessage(content="q2"),
            HumanMessage(content="last"),
            ToolMessage(content=_big(), tool_call_id="orphan", name="read_files"),
        ]
        out, stats = prune_messages(msgs)
        # tool_call_id не найден среди read-вызовов → не трогаем
        assert stats["pruned_blocks"] == 0

    def test_native_preserves_toolmessage_metadata(self):
        msgs = [
            HumanMessage(content="q1"),
            AIMessage(content="", tool_calls=[_tc("read_files", "a.py", "r1")]),
            ToolMessage(content=_big(), tool_call_id="r1", name="read_files"),
            HumanMessage(content="q2"),
            AIMessage(content="", tool_calls=[_tc("write_file", "a.py", "w1")]),
            ToolMessage(content="ok", tool_call_id="w1", name="write_file"),
            HumanMessage(content="last"),
        ]
        out, _ = prune_messages(msgs)
        tool_msg = next(m for m in out if isinstance(m, ToolMessage) and m.tool_call_id == "r1")
        assert tool_msg.tool_call_id == "r1"
        assert tool_msg.name == "read_files"
        assert isinstance(tool_msg, ToolMessage)