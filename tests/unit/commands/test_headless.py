"""commands/headless.py — консистентный вывод ошибок в --json режиме."""

import json

import pytest

from commands.headless import _fail


class TestFail:
    """Регрессия: ранние ошибки (модель/api/workdir/empty) в --json режиме шли
    plain-текстом на stderr с пустым stdout — CI-скрипт `run --json | parse`
    падал на пустом вводе. Теперь в --json ошибка — валидный JSON на stdout."""

    def test_json_mode_emits_json_on_stdout(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _fail("Model not found: 'x'", json_output=True)
        assert exc.value.code == 2
        out = capsys.readouterr()
        # JSON на stdout, не на stderr
        assert out.err == ""
        parsed = json.loads(out.out)
        assert parsed == {"ok": False, "error": "Model not found: 'x'"}

    def test_plain_mode_emits_to_stderr(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _fail("boom", json_output=False)
        assert exc.value.code == 2
        out = capsys.readouterr()
        assert out.out == ""
        assert "error: boom" in out.err

    def test_custom_exit_code(self, capsys):
        with pytest.raises(SystemExit) as exc:
            _fail("interrupted", json_output=True, code=130)
        assert exc.value.code == 130
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["ok"] is False
