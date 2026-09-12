"""Мікрофон зустрічі через апаратний AEC (VPIO) замість мікрофона SCK.

🔴 12.09.2026. Заміряно на живій машині: ехо власних динаміків у мікрофоні
−28,7 dBFS без AEC проти −50,5 з ним. Тут перевіряється не сам AEC (він
апаратний, у тестах його немає), а ПРОВОДКА навколо нього — саме в ній ціна
помилки найвища: якщо шлях підніметься наполовину, зустріч запишеться або з
ехом, або взагалі без мікрофона, і дізнаємось ми про це після розмови.
"""

import types

import numpy as np
import pytest

from pysar import syscap
from pysar.syscap import SAMPLE_RATE, SystemAudioRecorder


class _FakeVpio:
    """Стенд замість VoiceProcessingMic: віддає блоки на вимогу тесту."""

    def __init__(self, on_block, err: str | None = None, channels: int = 9):
        self.on_block = on_block
        self.channels = channels
        self._err = err
        self.started = 0
        self.stopped = 0

    def start(self) -> str | None:
        self.started += 1
        return self._err

    def stop(self) -> None:
        self.stopped += 1

    def speak(self, seconds: float = 0.5, level: float = 0.3) -> None:
        self.on_block(np.full(int(SAMPLE_RATE * seconds), level, np.float32), SAMPLE_RATE)


@pytest.fixture
def no_sck(monkeypatch):
    monkeypatch.setattr(syscap, "AVAILABLE", True)
    monkeypatch.setattr(syscap, "SC", types.SimpleNamespace(), raising=False)


@pytest.fixture
def vpio(monkeypatch):
    """Ставить фейковий VPIO і віддає останній створений екземпляр."""
    made: list[_FakeVpio] = []

    def factory(err=None):
        def make(on_block):
            made.append(_FakeVpio(on_block, err=err))
            return made[-1]

        monkeypatch.setattr(syscap, "VoiceProcessingMic", lambda on_block: make(on_block))
        return made

    return factory


def test_aec_mic_replaces_the_sck_mic(tmp_path, no_sck, vpio):
    """Піднявся VPIO ⇒ у ScreenCaptureKit мікрофон не просимо взагалі.

    Інакше мік відкрився б ДВІЧІ: один із AEC, другий сирий, і сире ехо все
    одно лягло б у транскрипт — тобто вся робота була б марна."""
    made = vpio()
    rec = SystemAudioRecorder(
        capture_mic=True, mic_aec=True, raw_dump_dir=tmp_path, raw_dump_stem="aec"
    )
    rec.start()
    assert rec._mic_from_vpio is True
    assert made[0].started == 1
    made[0].speak(0.5)
    rec.stop()
    mic = next(p for p in rec._dump_final[0] if "-mic" in p.name)
    assert mic.stat().st_size > 0, "звук з AEC не дійшов до дампа"
    assert made[0].stopped == 1, "мікрофон лишився відкритим після Стоп"


def test_aec_failure_falls_back_to_the_plain_mic_and_says_so(tmp_path, no_sck, vpio):
    """AEC не піднявся ⇒ пишемо як раніше, але вголос.

    Без мікрофона зустріч гірша, ніж із ехом, тому відкат правильний. А от
    тиха відкат — ні: людина має знати, що ехо сьогодні буде."""
    vpio(err="мікрофон тримає інший застосунок")
    errs: list[str] = []
    rec = SystemAudioRecorder(
        capture_mic=True, mic_aec=True, raw_dump_dir=tmp_path, raw_dump_stem="aec"
    )
    rec.start(on_error=errs.append)
    assert rec._mic_from_vpio is False, "мік мусить лишитись на SCK"
    assert errs and "ехо" in errs[0] and "інший застосунок" in errs[0]


def test_aec_off_never_touches_the_engine(tmp_path, no_sck, vpio):
    made = vpio()
    rec = SystemAudioRecorder(capture_mic=True, mic_aec=False, raw_dump_dir=tmp_path)
    rec.start()
    assert made == [] and rec._mic_from_vpio is False


def test_restart_does_not_leave_the_previous_mic_open(tmp_path, no_sck, vpio):
    """Обрив захоплення перезапускає рекордер тим самим об'єктом — 23.07.2026
    саме так мік лишався відкритим до перезагрузки."""
    made = vpio()
    rec = SystemAudioRecorder(
        capture_mic=True, mic_aec=True, raw_dump_dir=tmp_path, raw_dump_stem="aec"
    )
    rec.start()
    rec.start()
    assert made[0].stopped == 1, "перший мікрофон не закрили"
    assert made[1].started == 1 and rec._mic_from_vpio is True


def test_aec_blocks_go_down_the_mic_path_not_the_system_one(tmp_path, no_sck, vpio):
    """Блоки з AEC мусять іти тим самим шляхом, що мік SCK: у режимі
    розділення голосів вони позначаються «mic», а не «sys» і не None."""
    made = vpio()
    got: list[str | None] = []
    rec = SystemAudioRecorder(
        capture_mic=True,
        mic_aec=True,
        source_mode="smart",
        raw_dump_dir=tmp_path,
        raw_dump_stem="aec",
    )
    rec.start(on_segment=lambda wav, src, span: got.append(src))
    made[0].speak(seconds=3.0)
    rec.stop()
    assert got and set(got) == {"mic"}


def test_aec_mic_does_not_prove_the_system_stream_alive(tmp_path, no_sck, vpio):
    """Мік з AEC — ОКРЕМИЙ рушій, тому в серцебиття захоплення він не стукає.

    Інакше сторож обриву осліп би: у Каті 12.09 системний потік падав
    SCStreamError -3817, і зустріч дописувалась би далі без співрозмовника,
    бо мікрофон бадьоро постачав буфери."""
    made = vpio()
    rec = SystemAudioRecorder(
        capture_mic=True, mic_aec=True, raw_dump_dir=tmp_path, raw_dump_stem="aec"
    )
    rec.start()
    made[0].speak(0.5)
    assert rec.seconds_since_audio() is None, "мік з AEC підробив серцебиття SCK"
    rec._ingest_pcm(0, np.full(SAMPLE_RATE // 2, 0.2, np.float32), SAMPLE_RATE)
    assert rec.seconds_since_audio() is not None
    rec.stop()
