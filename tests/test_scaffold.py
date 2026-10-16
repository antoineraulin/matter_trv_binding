"""Scaffold sanity tests."""
import json
from pathlib import Path

CC = Path(__file__).parent.parent / "custom_components" / "matter_trv_binding"


def test_manifest_keys():
    manifest = json.loads((CC / "manifest.json").read_text())
    assert manifest["domain"] == "matter_trv_binding"
    assert manifest["config_flow"] is True
    assert manifest["version"] == "0.1.0"
    assert "matter-python-client~=1.0" in manifest["requirements"]
    assert manifest["dependencies"] == []


def test_translations_parse():
    data = json.loads((CC / "translations" / "en.json").read_text())
    assert "config" in data and "options" in data and "issues" in data
