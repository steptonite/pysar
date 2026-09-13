"""Мікрофон зустрічі через апаратний AEC (VPIO) замість мікрофона SCK.


🔴 13.09.2026 VPIO ЗАБОРОНЕНО: самотест tools/call_audio_selftest.py показав,
що з ним звук дзвінка в динаміках −23.8 дБ, а мік для Telegram −9.4 дБ.
Тести проводки VPIO прибрано; лишається одна гарантія — рушій не створюється
НІКОЛИ, хоч би що просили налаштування.
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


@pytest.mark.parametrize("mic_aec", [False, True])
def test_vpio_engine_is_never_created(tmp_path, no_sck, vpio, mic_aec):
    made = vpio()
    rec = SystemAudioRecorder(capture_mic=True, mic_aec=mic_aec, raw_dump_dir=tmp_path)
    rec.start()
    assert made == [] and rec._mic_from_vpio is False
