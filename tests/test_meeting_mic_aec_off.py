"""13.09.2026: AEC мікрофона зустрічі вимкнено — VPIO глушив мік для Telegram/Zoom."""

import json


def test_aec_is_off_out_of_the_box():
    from pysar import recordings

    assert recordings.DEFAULTS["meeting_mic_aec"] is False


def test_update_turns_aec_off_for_installs_that_already_exist(tmp_path, monkeypatch):
    from pysar import recordings

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"meeting_mic_aec": True}))
    monkeypatch.setattr(recordings, "_SETTINGS", f)
    assert recordings.load_settings()["meeting_mic_aec"] is False

    # Свідоме ввімкнення після переходу — поважаємо.
    f.write_text(json.dumps({"meeting_mic_aec": True, "aec_off_migrated": True}))
    assert recordings.load_settings()["meeting_mic_aec"] is True
