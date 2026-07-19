"""apis/definitions/*.json — sanity-check встроенных шаблонов провайдеров."""

import json
from pathlib import Path

import pytest

_DEFINITIONS_DIR = Path(__file__).resolve().parents[3] / "src" / "apis" / "definitions"

_JSON_FILES = sorted(
    p for p in _DEFINITIONS_DIR.glob("*.json")
    if not p.name.startswith("_")
)


@pytest.mark.parametrize("path", _JSON_FILES, ids=lambda p: p.name)
class TestDefinitionsJson:
    def test_parses(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_required_fields(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for field in ("id", "name", "type", "base_url"):
            assert field in data, f"{path.name}: missing {field}"

    def test_pricing_non_negative(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for m in data.get("models", []):
            assert m.get("input_price", 0) >= 0, f"{path.name}/{m.get('id')}: negative input_price"
            assert m.get("output_price", 0) >= 0, f"{path.name}/{m.get('id')}: negative output_price"

    def test_models_have_id_and_display(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        for m in data.get("models", []):
            assert "id" in m, f"{path.name}: model missing id"

    def test_default_model_in_list(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        default = data.get("default_model")
        if not default:
            return
        ids = [m.get("id") for m in data.get("models", [])]
        assert default in ids, f"{path.name}: default_model {default} not in models"
