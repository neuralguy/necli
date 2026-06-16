"""agent/display.py — формат строки времени инструмента (_format_elapsed)."""

from agent.display import _format_elapsed


class TestFormatElapsed:
    """Регрессия: read/list показывали «0.0s» — мгновенное исполнение
    округлялось до 0.0 и выглядело как баг таймера. Теперь такое время скрывается."""

    def test_instant_op_hidden(self):
        # read/list с файлом в кеше: ~0.001s → округлилось бы в 0.0s → скрываем.
        assert _format_elapsed(0.001) == ""
        assert _format_elapsed(0.0) == ""

    def test_just_below_threshold_hidden(self):
        # 0.04s округлилось бы в 0.0s — тоже скрываем.
        assert _format_elapsed(0.04) == ""

    def test_at_threshold_shown(self):
        # 0.05s округляется в 0.1s — показываем.
        assert _format_elapsed(0.05) == " 0.1s"

    def test_real_time_shown(self):
        assert _format_elapsed(1.23) == " 1.2s"
        assert _format_elapsed(11.2) == " 11.2s"

    def test_none_safe(self):
        assert _format_elapsed(None) == ""


class TestShellPreviewTailOnFailure:
    """Регрессия UX: при падении shell-команды превью показывало ПЕРВЫЕ строки
    (`[stderr]` + начало traceback), а сам текст ошибки (последняя строка вроде
    `ValueError: 42`) уходил в «… +M lines». Теперь при ошибке показываем ХВОСТ."""

    def _preview(self, output, status):
        import tools
        from agent.display import _compact_preview_content
        r = tools.ToolResult(name="shell", status=status, output=output,
                             exit_code=0 if status == "ok" else 1, command="x")
        prev = _compact_preview_content("shell", {}, r)
        return [p.plain if hasattr(p, "plain") else str(p) for p in (prev or [])]

    def test_failure_shows_tail_with_error(self):
        out = "[stderr]\n" + "\n".join(f"frame {i}" for i in range(8)) + "\nValueError: 42"
        lines = self._preview(out, "error")
        joined = "\n".join(lines)
        # последняя смысловая строка (текст ошибки) должна присутствовать
        assert "ValueError: 42" in joined
        # а голова (`[stderr]`) — обрезана сверху как "… +N"
        assert "[stderr]" not in joined

    def test_success_shows_head(self):
        out = "\n".join(f"line {i}" for i in range(12))
        lines = self._preview(out, "ok")
        joined = "\n".join(lines)
        assert "line 0" in joined          # голова видна
        assert "line 11" not in joined     # хвост обрезан

    def test_short_output_shown_whole(self):
        out = "only one line"
        lines = self._preview(out, "error")
        assert any("only one line" in ln for ln in lines)
