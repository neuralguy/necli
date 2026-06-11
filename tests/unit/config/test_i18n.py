"""config/i18n.py — таблицы переводов, fallback, форматирование."""

import string

import pytest

from config.i18n import (
    EN, RU, DE, FR, ZH,
    SUPPORTED_LANGS, LANG_DISPLAY, _TABLES,
    get_lang, set_lang, t,
)

# Известные пробелы перевода в исходниках: эти ключи отсутствуют в указанных
# языках и осознанно резолвятся через fallback EN внутри t(). Тест фиксирует
# текущее состояние, а не маскирует его — список виден явно.
_KNOWN_MISSING = {
    "de": {"help.copy", "sh.copy_ok", "sh.copy_empty", "sh.copy_fail"},
    "fr": {"help.copy", "sh.copy_ok", "sh.copy_empty", "sh.copy_fail"},
    "zh": {"help.copy", "sh.copy_ok", "sh.copy_empty", "sh.copy_fail"},
    "ru": set(),
}

# EN использует {s} для плюрализации ("day{s}"), у остальных языков склонение
# встроено в слово — разница в плейсхолдерах ожидаема.
_PLACEHOLDER_EXCEPTIONS = {"stats.last_n_days"}

@pytest.fixture(autouse=True)
def _isolated(isolated_data):
    yield

class TestKeyParity:
    def test_all_supported_langs_in_tables(self):
        assert set(_TABLES.keys()) == set(SUPPORTED_LANGS)

    def test_ru_has_full_parity_with_en(self):
        assert set(RU) == set(EN)

    @pytest.mark.parametrize("lang,table", [("de", DE), ("fr", FR), ("zh", ZH)])
    def test_missing_keys_limited_to_known_gap(self, lang, table):
        missing = set(EN) - set(table)
        assert missing == _KNOWN_MISSING[lang], (
            f"{lang}: missing set changed — got {sorted(missing)}, "
            f"expected {sorted(_KNOWN_MISSING[lang])}"
        )

    @pytest.mark.parametrize("lang,table", [("ru", RU), ("de", DE), ("fr", FR), ("zh", ZH)])
    def test_no_extra_keys_vs_en(self, lang, table):
        extra = set(table) - set(EN)
        assert not extra, f"{lang} has keys absent in EN: {sorted(extra)}"

    def test_every_lang_has_display_name(self):
        for lang in SUPPORTED_LANGS:
            assert lang in LANG_DISPLAY
            assert LANG_DISPLAY[lang]

class TestPlaceholderParity:
    @staticmethod
    def _fields(s: str) -> set[str]:
        return {fn for _, fn, _, _ in string.Formatter().parse(s) if fn}

    @pytest.mark.parametrize("lang,table", [("ru", RU), ("de", DE), ("fr", FR), ("zh", ZH)])
    def test_same_placeholders_as_en(self, lang, table):
        mismatches = []
        for key, en_val in EN.items():
            if key not in table or key in _PLACEHOLDER_EXCEPTIONS:
                continue
            if self._fields(en_val) != self._fields(table[key]):
                mismatches.append(key)
        assert not mismatches, f"{lang} placeholder mismatch: {mismatches}"

class TestGetSetLang:
    def test_default_is_en(self):
        assert get_lang() == "en"

    def test_set_and_get(self):
        set_lang("ru")
        assert get_lang() == "ru"

    def test_set_unknown_resets_to_default(self):
        set_lang("ru")
        set_lang("xx")  # invalid → нормализуется до en (см. set_lang)
        assert get_lang() == "en"

    def test_get_unknown_in_config_falls_back(self):
        from config import settings as _s
        _s.set_value("language", "klingon")
        assert get_lang() == "en"

class TestLookup:
    def test_returns_selected_lang(self):
        set_lang("ru")
        assert t("common.cancel") == RU["common.cancel"]

    def test_default_lang_en(self):
        assert t("common.cancel") == EN["common.cancel"]

    def test_missing_key_in_lang_falls_back_to_en(self):
        # ключ есть только в EN (отсутствует в zh) → t отдаёт EN-значение
        set_lang("zh")
        assert "help.copy" not in ZH
        assert t("help.copy") == EN["help.copy"]

    def test_unknown_key_returns_key_itself(self):
        assert t("no.such.key.exists") == "no.such.key.exists"

    def test_formatting_kwargs(self):
        out = t("lang.changed", name="Русский")
        assert "Русский" in out

    def test_formatting_missing_kwarg_returns_unformatted(self):
        out = t("lang.changed")  # нет name → шаблон без падения
        assert "{name}" in out