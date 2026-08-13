"""TTS-dataset capture: the (audio, text) pair, the un-normalized WAV, the toggle.

The dataset is worthless if any of three things slip: the transcript must be the
raw dictation (not the LLM rewrite), the audio must keep the level the mic gave
it, and nothing at all must be written while the toggle is off.
"""

import io
import json
import wave

import numpy as np
import pytest

from pysar import recordings as rec_mod
from pysar.config import CAPTURE_RATE, SAMPLE_RATE
from pysar.recorder import pcm_to_wav


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mod, "_DATASET", tmp_path / "dataset")
    return tmp_path / "dataset"


def _quiet_pcm(peak: float = 0.05, n: int = 1600) -> bytes:
    t = np.linspace(0, 1, n, dtype=np.float32)
    return (np.sin(2 * np.pi * 220 * t) * peak).astype(np.float32).tobytes()


def _read(wav_bytes: bytes):
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        frames = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
        return wf.getframerate(), frames


def test_dataset_wav_keeps_the_original_level():
    """Peak normalisation is right for ASR and poison for a voice corpus: it makes
    loudness drift from clip to clip and the model learns that drift."""
    pcm = _quiet_pcm(peak=0.05)
    _, plain = _read(pcm_to_wav(pcm, rate=CAPTURE_RATE, normalize=False))
    _, gained = _read(pcm_to_wav(pcm))
    assert np.max(np.abs(plain)) < 0.06 * 32767  # untouched
    assert np.max(np.abs(gained)) > 3 * np.max(np.abs(plain))  # ASR path lifts it


def test_dataset_wav_carries_the_capture_rate():
    rate, _ = _read(pcm_to_wav(_quiet_pcm(), rate=CAPTURE_RATE, normalize=False))
    assert rate == CAPTURE_RATE
    rate, _ = _read(pcm_to_wav(_quiet_pcm()))
    assert rate == SAMPLE_RATE  # default path unchanged for every existing caller


def test_clip_is_saved_with_its_transcript(dataset):
    wav = pcm_to_wav(_quiet_pcm(), rate=CAPTURE_RATE, normalize=False)
    path = rec_mod.save_dataset_clip(wav, "перевірка звуку", lang="uk", rate=CAPTURE_RATE)
    assert path is not None and path.exists()
    lines = (dataset / "metadata.jsonl").read_text(encoding="utf-8").strip().splitlines()
    entry = json.loads(lines[-1])
    assert entry["file"] == path.name
    assert entry["text"] == "перевірка звуку"
    assert entry["lang"] == "uk"
    assert entry["rate"] == CAPTURE_RATE


def test_untranscribed_clip_is_skipped(dataset):
    """Audio with no text is dead weight — a voice model trains on pairs."""
    wav = pcm_to_wav(_quiet_pcm(), rate=CAPTURE_RATE, normalize=False)
    assert rec_mod.save_dataset_clip(wav, "") is None
    assert rec_mod.save_dataset_clip(wav, "   ") is None
    assert rec_mod.save_dataset_clip(b"", "щось сказано") is None
    assert not dataset.exists() or not list(dataset.glob("*.wav"))


def test_index_appends_across_clips(dataset):
    wav = pcm_to_wav(_quiet_pcm(), rate=CAPTURE_RATE, normalize=False)
    for i in range(3):
        assert rec_mod.save_dataset_clip(wav, f"фраза {i}", lang="uk") is not None
    lines = (dataset / "metadata.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    assert [json.loads(x)["text"] for x in lines] == ["фраза 0", "фраза 1", "фраза 2"]
    assert len(list(dataset.glob("*.wav"))) == 3


def test_dataset_never_prunes(dataset, monkeypatch):
    """recordings/ is a rolling buffer; the corpus must only grow."""
    monkeypatch.setattr(rec_mod, "_RECORDINGS", dataset.parent / "recordings")
    wav = pcm_to_wav(_quiet_pcm(), rate=CAPTURE_RATE, normalize=False)
    for i in range(7):
        rec_mod.save_dataset_clip(wav, f"фраза {i}")
        rec_mod.save_recording(wav, keep_last=3)
    assert len(list(dataset.glob("*.wav"))) == 7
    assert len(list((dataset.parent / "recordings").glob("*.wav"))) == 3


def test_stats_report_clips_and_hours(dataset):
    wav = pcm_to_wav(_quiet_pcm(n=CAPTURE_RATE), rate=CAPTURE_RATE, normalize=False)
    for i in range(2):
        rec_mod.save_dataset_clip(wav, f"фраза {i}")
    clips, hours = rec_mod.dataset_stats()
    assert clips == 2
    assert hours == pytest.approx(2 / 3600, rel=0.05)  # two one-second clips


def test_toggle_defaults_off_and_round_trips():
    assert rec_mod.DEFAULTS["tts_dataset"] is False


class _FakeStream:
    """A mic that hands out a fixed 48 kHz tone, then ends the take."""

    def __init__(self, blocks, done):
        self._blocks = list(blocks)
        self._done = done  # the recorder's stop event — fires when the tone runs out

    def read(self, n):
        if self._blocks:
            return self._blocks.pop(0).reshape(-1, 1), False
        self._done.set()
        return np.zeros((0, 1), dtype=np.float32), False

    def stop(self):
        pass

    def close(self):
        pass


def _run_recorder(dataset_on: bool):
    """Drive AudioRecorder over a fake 48 kHz stream, no hardware involved."""
    from pysar.config import CAPTURE_CHUNK_SIZE
    from pysar.recorder import AudioRecorder

    t = np.arange(CAPTURE_CHUNK_SIZE * 8, dtype=np.float32) / CAPTURE_RATE
    tone = (np.sin(2 * np.pi * 200 * t) * 0.2).astype(np.float32)
    blocks = np.split(tone, 8)

    r = AudioRecorder(dataset=dataset_on)
    r._open_stream = lambda: _FakeStream(blocks, r._stop_event)
    r.start()
    r._thread.join(timeout=5)
    assert not r._thread.is_alive()
    return r, tone


def test_recorder_feeds_whisper_16k_and_keeps_48k_for_the_dataset():
    r, tone = _run_recorder(dataset_on=True)
    rate, frames = _read(r._to_wav())
    assert rate == SAMPLE_RATE
    assert frames.size == pytest.approx(tone.size // 3, abs=2)
    ds_rate, ds_frames = _read(r.raw_wav())
    assert ds_rate == CAPTURE_RATE
    assert ds_frames.size == tone.size  # every captured sample, nothing dropped


def test_dataset_off_retains_nothing():
    """Privacy default: with the toggle off not one extra byte is held."""
    r, _ = _run_recorder(dataset_on=False)
    assert r.raw_wav() is None
    assert r._raw_frames == []
