"""Розділення спікерів має працювати з коробки.

🔴 12.09.2026. Урок із чужої установки (Катя): режим стояв вимкненим дефолтом,
а моделі качались рівно на вмикання — тож не сталось нічого, і зовні це
виглядало як «Писар не розділяє спікерів». Тому тут перевіряється не UI, а два
факти: дефолт увімкнений, і моделі приїжджають ФОНОМ, до першої зустрічі.
"""

from __future__ import annotations

import types

from pysar.recordings import DEFAULTS


def _app_mod():
    """Імпорт усередині тесту: `pysar.app` тягне ObjC-класи, і якщо взяти його на
    збиранні, інші модулі тестів реєструють ті самі класи вдруге (objc.error)."""
    from pysar import app as app_mod

    return app_mod


class _Tray:
    def __init__(self) -> None:
        self.notes: list[tuple[str, str]] = []

    def notify(self, _app, title, msg) -> None:
        self.notes.append((title, msg))


def _stub(settings=None, tray=None):
    """Голий носій методу — піднімати весь застосунок заради потоку зайве."""
    obj = types.SimpleNamespace(
        _settings=settings if settings is not None else {"meeting_diarize": True},
        _tray=tray or _Tray(),
        _t=lambda key, **kw: key,
    )
    obj._prefetch_diar = types.MethodType(_app_mod().VoiceTyper._prefetch_diar, obj)
    return obj


def test_diarization_is_on_out_of_the_box():
    assert DEFAULTS["meeting_diarize"] is True
    # А мікрофон — ні: там один власник мака, кластеризація знаходить дихання.
    assert DEFAULTS["diar_mic"] is False


def test_models_are_fetched_before_the_first_meeting(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(_app_mod().time, "sleep", lambda _s: None)
    monkeypatch.setattr(_app_mod().diarize, "is_ready", lambda: False)
    monkeypatch.setattr(
        _app_mod().diarize, "ensure_ready", lambda *a, **k: calls.append("go") or (True, "ok")
    )
    obj = _stub()
    obj._prefetch_diar()
    assert calls == ["go"], "моделі не поїхали фоном — на «Стоп» людина чекатиме хвилини"
    assert obj._tray.notes, "докачали мовчки: людина не дізнається, що режим ожив"


def test_failure_is_spoken_not_swallowed(monkeypatch):
    monkeypatch.setattr(_app_mod().time, "sleep", lambda _s: None)
    monkeypatch.setattr(_app_mod().diarize, "is_ready", lambda: False)
    monkeypatch.setattr(_app_mod().diarize, "ensure_ready", lambda *a, **k: (False, "мережа впала"))
    obj = _stub()
    obj._prefetch_diar()
    assert obj._tray.notes and obj._tray.notes[-1][1] == "мережа впала"


def test_nothing_is_downloaded_when_the_mode_is_off(monkeypatch):
    monkeypatch.setattr(_app_mod().time, "sleep", lambda _s: None)
    monkeypatch.setattr(_app_mod().diarize, "is_ready", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("качаємо 110 МБ, хоч людина розділення вимкнула")

    monkeypatch.setattr(_app_mod().diarize, "ensure_ready", _boom)
    _stub(settings={"meeting_diarize": False})._prefetch_diar()


def test_ready_install_does_not_touch_the_network(monkeypatch):
    monkeypatch.setattr(_app_mod().time, "sleep", lambda _s: None)
    monkeypatch.setattr(_app_mod().diarize, "is_ready", lambda: True)

    def _boom(*a, **k):
        raise AssertionError("моделі вже є, а ми полізли качати")

    monkeypatch.setattr(_app_mod().diarize, "ensure_ready", _boom)
    obj = _stub()
    obj._prefetch_diar()
    assert not obj._tray.notes


def test_update_turns_it_on_for_installs_that_already_exist(tmp_path, monkeypatch):
    """Оновлення, а не лише чиста установка: у старому файлі ключ лежить false."""
    import json

    from pysar import recordings

    f = tmp_path / "settings.json"
    f.write_text(json.dumps({"meeting_diarize": False}))
    monkeypatch.setattr(recordings, "_SETTINGS", f)
    assert recordings.load_settings()["meeting_diarize"] is True

    # А свідоме вимкнення після переходу — поважаємо.
    f.write_text(json.dumps({"meeting_diarize": False, "diar_on_migrated": True}))
    assert recordings.load_settings()["meeting_diarize"] is False
