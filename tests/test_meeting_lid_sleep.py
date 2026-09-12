"""Закрита кришка не має коштувати зустрічі.

🔴 12.09.2026, заміряно на Маку Льоші (`tools/sck_break_test.py`): з шести
підозрюваних потік убив рівно один крок — закрита кришка, `SCStreamError -3815`
«не вдалося знайти екран чи вікно». Вхід у Zoom, зміна виходу звуку, блокування
екрана і перемикання монітора потік пережив.

Без екрана жоден рестарт не злетить, тому три швидкі невдачі вибирали ліміт
_MEETING_RECOVER_MAX і Писар глушив СЕСІЮ — хоч кришку зараз відкриють. Тут
перевіряється, що замість штрафних балів ми чекаємо.
"""

from __future__ import annotations

import types


def _app_mod():
    from pysar import app as app_mod

    return app_mod


def _stub(**kw):
    m = _app_mod()
    obj = types.SimpleNamespace(
        _meeting=True,
        _meeting_stopping=False,
        _meeting_recover_count=2,
        _meeting_recover_window_start=0.0,
        _DISPLAY_WAIT_MAX_SEC=5.0,
    )
    obj.__dict__.update(kw)
    obj._wait_for_display = types.MethodType(m.VoiceTyper._wait_for_display, obj)
    return obj


def test_display_present_means_no_waiting(monkeypatch):
    monkeypatch.setattr(_app_mod(), "displays_present", lambda: True)
    obj = _stub()
    assert obj._wait_for_display() is True
    assert obj._meeting_recover_count == 2, "лічильник чіпати нема за що — екран на місці"


def test_closed_lid_costs_no_recovery_attempts(monkeypatch):
    m = _app_mod()
    seen: list[float] = []
    monkeypatch.setattr(m.time, "sleep", lambda s: seen.append(s))
    states = iter([False, False, True, True, True])
    monkeypatch.setattr(m, "displays_present", lambda: next(states))
    obj = _stub()
    assert obj._wait_for_display() is True
    assert seen, "не чекали жодної секунди — отже пішли палити спроби наосліп"
    assert obj._meeting_recover_count == 0, (
        "інцидент кришки тягне штрафні бали далі — наступний збій уб'є зустріч"
    )


def test_waiting_ends_when_the_user_presses_stop(monkeypatch):
    m = _app_mod()
    obj = _stub()

    def _tick(_s):
        obj._meeting_stopping = True

    monkeypatch.setattr(m.time, "sleep", _tick)
    monkeypatch.setattr(m, "displays_present", lambda: False)
    assert obj._wait_for_display() is False


def test_a_screen_that_never_returns_does_not_loop_forever(monkeypatch):
    m = _app_mod()
    monkeypatch.setattr(m.time, "sleep", lambda _s: None)
    monkeypatch.setattr(m, "displays_present", lambda: False)
    assert _stub()._wait_for_display() is False


def test_unknown_display_state_is_not_a_blocker(monkeypatch):
    """Quartz недоступний — краще спробувати підняти потік, ніж вигадати блокер."""
    from pysar import syscap

    monkeypatch.setitem(__import__("sys").modules, "Quartz", None)
    assert syscap.displays_present() is True
