"""Every locale carries the same strings as English."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

TRANSLATIONS = (
    Path(__file__).parent.parent
    / "custom_components"
    / "elegoo_spaghetti_detection"
    / "translations"
)


def _keys(data: object, prefix: str = "") -> set[str]:
    if not isinstance(data, dict):
        return {prefix}
    keys: set[str] = set()
    for key, value in data.items():
        keys |= _keys(value, f"{prefix}.{key}" if prefix else key)
    return keys


@pytest.mark.parametrize(
    "path", sorted(TRANSLATIONS.glob("*.json")), ids=lambda path: path.stem
)
def test_locale_has_the_english_keys(path: Path) -> None:
    english = _keys(json.loads((TRANSLATIONS / "en.json").read_text("utf-8")))
    assert _keys(json.loads(path.read_text("utf-8"))) == english
