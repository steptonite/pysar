"""The raw recovery buffer of the meeting capture (_RawDump) and the mic pinning.

Written after 24.08.2026, when a 10-minute phone call produced an empty
transcript and NO audio at all: the WAV had only ever lived in memory, so when
the pipeline yielded nothing there was nothing left to re-run. The buffer must
therefore hold under conditions where the rest of the pipeline does not — a dead
source, a broken path, a capture restarted mid-meeting.

ScreenCaptureKit is unavailable on CI (and must not be driven on a dev Mac
either), so the recorder is exercised with `AVAILABLE` forced on and `SC`
replaced by an object that has no shareable-content API: `start()` then runs its
own bookkeeping — including arming the dumps — and the stream setup it kicks off
at the end is swallowed by the suppress already around it. Buffers are injected
straight into `_ingest`, with the CoreMedia decode stubbed.
"""

import sys
import types
import wave

import numpy as np
import pytest

from pysar import syscap
from pysar.app import _mic_uid_for_name
from pysar.syscap import SAMPLE_RATE, SystemAudioRecorder, _RawDump

_STEM = "2026-08-24_10-11-12"  # the capture's own stem format (strftime in start())


# ── _RawDump on its own ───────────────────────────────────────────────────────
def test_a_source_that_never_delivers_leaves_no_file(tmp_path):
    # The absence of the file IS the diagnosis ("this source was mute"), so a
    # zero-byte header must not be written just because the dump was armed.
    d = _RawDump(tmp_path / "silent-mic.wav")
    d.close()
    assert not (tmp_path / "silent-mic.wav").exists()
    assert d.frames == 0


def test_empty_buffers_do_not_open_the_file(tmp_path):
    d = _RawDump(tmp_path / "empty-sys.wav")
    d.write(np.zeros(0, np.float32))
    d.close()
    assert not (tmp_path / "empty-sys.wav").exists()


def test_written_audio_is_a_readable_16k_mono_wav(tmp_path):
    path = tmp_path / "call-mic.wav"
    d = _RawDump(path)
    d.write(np.full(1600, 0.5, np.float32))
    d.write(np.full(800, -0.5, np.float32))
    d.close()

    assert d.frames == 2400
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == SAMPLE_RATE
        assert wf.getnframes() == 2400
        pcm = np.frombuffer(wf.readframes(2400), dtype="<i2")
    # Round-trip through int16 with the same sign and rough level.
    assert pcm[0] == pytest.approx(16383, abs=2)
    assert pcm[-1] == pytest.approx(-16383, abs=2)


def test_samples_accumulate_across_writes(tmp_path):
    d = _RawDump(tmp_path / "acc-sys.wav")
    for _ in range(5):
        d.write(np.zeros(320, np.float32))
    assert d.frames == 1600
    d.close()


def test_out_of_range_samples_are_clipped_not_wrapped(tmp_path):
    # int16 wrap-around would turn a loud passage into noise — the recovered file
    # has to stay listenable, that is its whole job.
    path = tmp_path / "loud-mic.wav"
    d = _RawDump(path)
    d.write(np.array([4.0, -4.0], np.float32))
    d.close()
    with wave.open(str(path), "rb") as wf:
        pcm = np.frombuffer(wf.readframes(2), dtype="<i2")
    assert pcm[0] > 0 and pcm[1] < 0


def test_close_is_idempotent(tmp_path):
    path = tmp_path / "twice-sys.wav"
    d = _RawDump(path)
    d.write(np.zeros(160, np.float32))
    d.close()
    d.close()  # must not raise, must not corrupt what is already on disk
    with wave.open(str(path), "rb") as wf:
        assert wf.getnframes() == 160


def test_a_broken_path_cannot_kill_the_write(tmp_path, capsys):
    # Parent is a FILE — mkdir fails. The dump is a safety net; a safety net that
    # throws into the capture thread would take down the capture it exists for.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    d = _RawDump(blocker / "x-sys.wav")
    d.write(np.zeros(160, np.float32))
    d.write(np.zeros(160, np.float32))  # still no exception on the retry
    d.close()
    assert d.frames == 0
    assert "raw dump write failed" in capsys.readouterr().out


# ── the recorder's dump lifecycle ─────────────────────────────────────────────
@pytest.fixture
def no_sck(monkeypatch):
    """Let start() run its bookkeeping without ScreenCaptureKit behind it."""
    monkeypatch.setattr(syscap, "AVAILABLE", True)
    monkeypatch.setattr(syscap, "SC", types.SimpleNamespace(), raising=False)
    monkeypatch.setattr(syscap, "_pcm_mono", lambda sbuf: (sbuf, SAMPLE_RATE))


def _feed(rec, source: int, seconds: float, level: float = 0.25) -> None:
    """One decoded buffer of *seconds* into the given source (0 = sys, 1 = mic)."""
    rec._ingest(source, np.full(int(SAMPLE_RATE * seconds), level, np.float32))


def test_ingest_writes_the_stream_to_disk(tmp_path, no_sck):
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 1, 2.0)
    rec.stop()

    assert rec.dump_seconds() == pytest.approx(2.0, abs=0.01)
    assert [p.name for p in rec.dump_paths()] == [f"{_STEM}-mic.wav"]
    with wave.open(str(tmp_path / f"{_STEM}-mic.wav"), "rb") as wf:
        assert wf.getnframes() == SAMPLE_RATE * 2


def test_each_source_gets_its_own_file(tmp_path, no_sck):
    # Sys and mic arrive at different rates; one interleaved file would be
    # unusable, so they are never mixed into the recovery buffer.
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.0)
    _feed(rec, 1, 3.0)
    rec.stop()
    assert {p.name for p in rec.dump_paths()} == {f"{_STEM}-sys.wav", f"{_STEM}-mic.wav"}
    # The tally reports the LONGEST source: "was anything captured at all".
    assert rec.dump_seconds() == pytest.approx(3.0, abs=0.01)


def test_a_mute_source_leaves_no_file_behind(tmp_path, no_sck):
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 1, 1.0)  # mic only — system audio never delivered a buffer
    rec.stop()
    assert not (tmp_path / f"{_STEM}-sys.wav").exists()
    assert [p.name for p in rec.dump_paths()] == [f"{_STEM}-mic.wav"]


def test_the_tally_survives_stop(tmp_path, no_sck):
    """The whole point of _dump_final: _stop_meeting reports the outcome AFTER
    the capture is torn down, so a summary that died with the dumps would always
    read "no audio" — the very alarm it is supposed to raise honestly."""
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.5)
    assert rec.dump_seconds() == pytest.approx(1.5, abs=0.01)  # live reading
    rec.stop()
    assert rec.dump_seconds() == pytest.approx(1.5, abs=0.01)  # same after teardown
    assert rec.dump_paths()


def test_a_capture_with_no_audio_reports_zero(tmp_path, no_sck):
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    rec.stop()
    assert rec.dump_seconds() == 0.0
    assert rec.dump_paths() == []


def test_a_new_session_resets_the_tally(tmp_path, no_sck):
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.0)
    rec.stop()
    rec.start()  # next meeting must not inherit the previous one's minutes
    assert rec.dump_seconds() == 0.0
    assert rec.dump_paths() == []


def test_a_restart_suffixes_the_stem_instead_of_overwriting(tmp_path, no_sck):
    """A recover mid-meeting starts a fresh recorder with the SAME stem. Opening
    the same name "wb" would truncate the half of the call already saved."""
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.0)
    rec.stop()
    first = tmp_path / f"{_STEM}-sys.wav"
    size_before = first.stat().st_size

    rec.start()  # the recover-restart
    _feed(rec, 0, 2.0)
    rec.stop()

    assert first.stat().st_size == size_before  # untouched
    assert (tmp_path / f"{_STEM}-2-sys.wav").exists()
    assert [p.name for p in rec.dump_paths()] == [f"{_STEM}-2-sys.wav"]


def test_a_third_restart_keeps_counting(tmp_path, no_sck):
    rec = SystemAudioRecorder(raw_dump_dir=tmp_path, raw_dump_stem=_STEM)
    for _ in range(3):
        rec.start()
        _feed(rec, 1, 0.5)
        rec.stop()
    names = sorted(p.name for p in tmp_path.glob("*.wav"))
    assert names == [f"{_STEM}-2-mic.wav", f"{_STEM}-3-mic.wav", f"{_STEM}-mic.wav"]


def test_capture_survives_a_dump_that_cannot_be_written(tmp_path, no_sck):
    # Recovery is a bonus; the live transcript is the product. A dump directory
    # that turns out to be a file must not cost the user the meeting.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    rec = SystemAudioRecorder(raw_dump_dir=blocker, raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.0)
    assert rec.seconds_since_audio() is not None  # the stream kept being fed
    rec.stop()
    assert rec.dump_seconds() == 0.0


def test_without_a_dump_dir_nothing_is_written(tmp_path, no_sck):
    # The dump is opt-in per capture (dictation reuses none of this).
    rec = SystemAudioRecorder(raw_dump_stem=_STEM)
    rec.start()
    _feed(rec, 0, 1.0)
    rec.stop()
    assert rec.dump_seconds() == 0.0
    assert list(tmp_path.glob("*.wav")) == []


# ── mic pinning: menu name → CoreAudio UID ────────────────────────────────────
def _fake_avfoundation(*devices):
    """Stand-in for the AVFoundation binding; devices are (name, uid) pairs."""
    return types.SimpleNamespace(
        AVMediaTypeAudio="soun",
        AVCaptureDevice=types.SimpleNamespace(
            devicesWithMediaType_=lambda _kind: [
                types.SimpleNamespace(localizedName=lambda n=n: n, uniqueID=lambda u=u: u)
                for n, u in devices
            ]
        ),
    )


def test_no_name_means_system_default(monkeypatch):
    monkeypatch.setitem(sys.modules, "AVFoundation", _fake_avfoundation(("Mic", "uid-1")))
    assert _mic_uid_for_name(None) is None
    assert _mic_uid_for_name("") is None


def test_known_name_resolves_to_its_uid(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "AVFoundation",
        _fake_avfoundation(("AirPods Pro", "uid-air"), ("MacBook Air Microphone", "uid-built-in")),
    )
    assert _mic_uid_for_name("MacBook Air Microphone") == "uid-built-in"


def test_unknown_name_falls_back_to_default(monkeypatch):
    # Device unplugged or renamed since the menu choice was saved: the capture
    # must keep working on the system default, not refuse to start.
    monkeypatch.setitem(sys.modules, "AVFoundation", _fake_avfoundation(("Mic", "uid-1")))
    assert _mic_uid_for_name("Some Vanished USB Mic") is None


def test_missing_avfoundation_does_not_raise(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "AVFoundation", None)  # import → ImportError
    assert _mic_uid_for_name("MacBook Air Microphone") is None
    assert "could not resolve mic UID" in capsys.readouterr().out


def test_a_device_that_throws_is_reported_not_swallowed_silently(monkeypatch, capsys):
    broken = types.SimpleNamespace(
        AVMediaTypeAudio="soun",
        AVCaptureDevice=types.SimpleNamespace(
            devicesWithMediaType_=lambda _k: (_ for _ in ()).throw(RuntimeError("boom"))
        ),
    )
    monkeypatch.setitem(sys.modules, "AVFoundation", broken)
    assert _mic_uid_for_name("Mic") is None
    assert "could not resolve mic UID" in capsys.readouterr().out
