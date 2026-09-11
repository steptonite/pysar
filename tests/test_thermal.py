"""Термо-сторож: чи справді він ТРИМАЄ роботу, поки гаряче, і чи справді
відпускає її, коли охололо — і чи мовчить, коли датчика немає.

Живого заліза тут немає: температуру підставляємо самі, бо перевіряти треба
рішення сторожа, а не сенсор Apple."""

from src import thermal


class _Fake:
    """Керований «датчик»: віддає числа зі списку, останнє тримає далі."""

    def __init__(self, readings):
        self.readings = list(readings)
        self.slept = []

    def read(self):
        value = self.readings[0] if len(self.readings) == 1 else self.readings.pop(0)
        return ("PMU tdie1", value)

    def sleep(self, sec):
        self.slept.append(sec)


def _gate(readings, mode="normal", **kw):
    fake = _Fake(readings)
    gate = thermal.ThermalGate(mode=mode, cache_sec=0.0, reader=fake.read, sleep=fake.sleep, **kw)
    return gate, fake


def test_cold_mac_is_never_delayed():
    gate, fake = _gate([60.0])
    assert gate.wait() is True
    assert fake.slept == []


def test_hot_mac_waits_until_it_cools():
    # 97 > 95 (пауза) · 90 ще вище за 83 (поріг продовження) · 80 — відпускаємо.
    gate, fake = _gate([97.0, 90.0, 80.0])
    assert gate.wait() is True
    assert len(fake.slept) == 2, "мусив чекати, поки не впаде нижче порога продовження"
    assert gate.holding is False


def test_resume_threshold_is_lower_than_pause_threshold():
    # Гістерезис: інакше на 95,1 → 94,9 робота смикалась би щосекунди.
    pause_c, resume_c = thermal.profile("normal")
    assert resume_c < pause_c


def test_work_resumes_only_below_resume_threshold_not_below_pause():
    # 94° — це вже нижче за поріг ПАУЗИ, але ще вище за поріг ПРОДОВЖЕННЯ.
    gate, fake = _gate([99.0, 94.0, 82.0])
    gate.wait()
    assert len(fake.slept) == 2


def test_cancel_releases_the_wait():
    # Скасована робота не має висіти в паузі до охолодження.
    gate, _ = _gate([105.0])
    assert gate.wait(should_stop=lambda: True) is False


def test_no_sensor_means_no_guard_not_endless_pause():
    # Зламаний датчик НЕ має права зупинити роботу назавжди.
    gate = thermal.ThermalGate(mode="normal", cache_sec=0.0, reader=lambda: None)
    assert gate.wait() is True


def test_off_mode_never_touches_the_sensor():
    called = []

    def reader():
        called.append(1)
        return ("PMU tdie1", 120.0)

    gate = thermal.ThermalGate(mode="off", reader=reader)
    assert gate.enabled is False
    assert gate.wait() is True
    assert called == []


def test_unknown_mode_falls_back_to_the_default_not_to_off():
    gate = thermal.ThermalGate(mode="хтозна-що")
    assert gate.mode == thermal.DEFAULT_MODE
    assert gate.enabled is True


def test_mode_can_be_changed_while_running():
    gate, _ = _gate([90.0])
    assert gate.wait() is True  # 90 < 95, «звичайно» пропускає
    gate.set_mode("gentle")  # 90 > 88 — той самий мак уже «гарячий»
    assert gate.holding is False
    assert thermal.profile(gate.mode) == (88.0, 75.0)


def test_gentle_mode_also_cuts_cpu_threads():
    # Пауза сама по собі лише розтягує біду: розділення голосів гріє процесор,
    # тому в бережному режимі воно ще й рахує вдвічі меншим числом потоків.
    assert thermal.ThermalGate(mode="gentle").threads == 2
    assert thermal.ThermalGate(mode="normal").threads == 4


def test_state_callback_reports_the_temperature_it_is_waiting_on():
    gate, _ = _gate([97.0, 80.0])
    seen = []
    gate.wait(on_state=lambda holding, temp: seen.append((holding, temp)))
    assert seen and seen[0][0] is True
    assert seen[0][1] == 97.0


def test_gate_is_one_per_process():
    assert thermal.gate() is thermal.gate()


def test_hottest_prefers_the_die_over_calibration_sensors(monkeypatch):
    # На M2 Air «PMU tcal» на спокої ВИЩИЙ за всі tdie — і це не тепло.
    monkeypatch.setattr(
        thermal, "read_temps", lambda: {"PMU tcal": 51.9, "PMU tdie1": 50.2, "PMU tdie2": 48.9}
    )
    assert thermal.hottest() == ("PMU tdie1", 50.2)


def test_hottest_falls_back_to_any_sensor_when_there_is_no_die(monkeypatch):
    monkeypatch.setattr(thermal, "read_temps", lambda: {"battery": 33.0, "case": 41.0})
    assert thermal.hottest() == ("case", 41.0)


def test_hottest_is_none_when_the_machine_has_no_sensors(monkeypatch):
    monkeypatch.setattr(thermal, "read_temps", lambda: {})
    assert thermal.hottest() is None


# ── 11.09.2026: окремі вмикачі на ділянки («зустріч» / «файли») ───────────────
# Поріг спільний, а стерегти зустріч і чергу файлів людина може хотіти окремо.


def _scoped_gate(temp: float, mode: str = "normal"):
    return thermal.ThermalGate(
        mode=mode, poll_sec=0, reader=lambda: ("tdie", temp), sleep=lambda _s: None
    )


def test_scope_off_lets_hot_work_through():
    g = _scoped_gate(120.0)
    g.set_scope("files", False)
    assert g.enabled_for("files") is False
    assert g.wait(scope="files") is True  # не зависло, хоч і пекло


def test_other_scope_still_guarded():
    g = _scoped_gate(120.0)
    g.set_scope("files", False)
    assert g.enabled_for("meeting") is True
    stopped = g.wait(should_stop=lambda: True, scope="meeting")
    assert stopped is False  # ворота таки тримали й відпустили на «стоп»


def test_unknown_scope_is_guarded_by_default():
    """Нова ділянка має зʼявлятися ПІД охороною, а не повз неї."""
    assert _scoped_gate(50.0).enabled_for("щось-нове") is True


def test_scope_cannot_revive_a_disabled_guard():
    g = _scoped_gate(120.0, mode="off")
    g.set_scope("files", True)
    assert g.enabled_for("files") is False
