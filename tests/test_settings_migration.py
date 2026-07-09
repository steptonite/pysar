"""load_settings() migration of superseded shipped surzhyk styles."""

import json

from pysar import recordings
from pysar.profiles import DEFAULT_PROFILES, LEGACY_SURZHYK_STYLES

_NEW_STYLE = next(
    p["style_prompt"] for p in DEFAULT_PROFILES if p.get("name") == "Суржик / розмова"
)


def _load_with_profiles(monkeypatch, tmp_path, profiles):
    settings_file = tmp_path / "settings.json"
    settings_file.write_text(json.dumps({"profiles": profiles}))
    monkeypatch.setattr(recordings, "_SETTINGS", settings_file)
    return recordings.load_settings()


def test_legacy_surzhyk_styles_are_migrated(monkeypatch, tmp_path):
    for legacy in LEGACY_SURZHYK_STYLES:
        merged = _load_with_profiles(
            monkeypatch,
            tmp_path,
            [{"name": "Суржик / розмова", "language": "uk", "style_prompt": legacy}],
        )
        assert merged["profiles"][0]["style_prompt"] == _NEW_STYLE


def test_user_customized_style_is_untouched(monkeypatch, tmp_path):
    custom = "Мій власний стиль: пиши як я."
    merged = _load_with_profiles(
        monkeypatch,
        tmp_path,
        [{"name": "Суржик / розмова", "language": "uk", "style_prompt": custom}],
    )
    assert merged["profiles"][0]["style_prompt"] == custom


def test_first_run_seeds_new_default(monkeypatch, tmp_path):
    monkeypatch.setattr(recordings, "_SETTINGS", tmp_path / "missing.json")
    merged = recordings.load_settings()
    surzhyk = next(p for p in merged["profiles"] if p["name"] == "Суржик / розмова")
    assert surzhyk["style_prompt"] == _NEW_STYLE
    assert surzhyk["style_prompt"] not in LEGACY_SURZHYK_STYLES
