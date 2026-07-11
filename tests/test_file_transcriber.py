"""Tests for offline file transcription. No real ffmpeg or network — the
subprocess and whisper layers are stubbed via monkeypatch."""

import array
import itertools
import os
import struct
import subprocess
from pathlib import Path

from src.file_transcriber import (
    CHUNK_SEC,
    SAMPLE_RATE,
    TAIL_SEARCH_SEC,
    FileTranscriptionJob,
    find_quiet_split,
    pcm_to_wav,
    probe,
    split_plan,
)


def _silent_pcm(duration_sec: float) -> bytes:
    return b"\x00\x00" * int(duration_sec * SAMPLE_RATE)


def _pcm_with_silent_region(total_sec: float, silent_start: float, silent_dur: float) -> bytes:
    """Loud square-ish signal everywhere except one silent patch."""
    total = int(total_sec * SAMPLE_RATE)
    samples = array.array("h", [1000, -1000] * (total // 2 + 1))[:total]
    for i in range(int(silent_start * SAMPLE_RATE), min(int((silent_start + silent_dur) * SAMPLE_RATE), total)):
        samples[i] = 0
    return samples.tobytes()


# ── split_plan ───────────────────────────────────────────────────────────────


def test_split_plan_single_chunk():
    assert split_plan(30, chunk_sec=60) == [(0.0, 30.0)]


def test_split_plan_exact_multiple():
    assert split_plan(120, chunk_sec=60) == [(0.0, 60.0), (60.0, 120.0)]


def test_split_plan_tiny_remainder_merged():
    assert split_plan(125, chunk_sec=60) == [(0.0, 60.0), (60.0, 125.0)]


def test_split_plan_big_remainder_kept():
    assert split_plan(130, chunk_sec=60) == [(0.0, 60.0), (60.0, 120.0), (120.0, 130.0)]


def test_split_plan_gapless():
    plan = split_plan(213.7, chunk_sec=60)
    assert plan[0][0] == 0.0
    assert plan[-1][1] == 213.7
    for a, b in itertools.pairwise(plan):
        assert a[1] == b[0]


# ── pcm_to_wav ───────────────────────────────────────────────────────────────


def test_pcm_to_wav_header():
    pcm = _silent_pcm(1.0)
    wav = pcm_to_wav(pcm)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert len(wav) == 44 + len(pcm)
    assert struct.unpack("<I", wav[24:28])[0] == SAMPLE_RATE


# ── find_quiet_split ─────────────────────────────────────────────────────────


def test_find_quiet_split_short_chunk_kept_whole():
    short = _silent_pcm(TAIL_SEARCH_SEC * 1.5)
    assert find_quiet_split(short) == len(short)


def test_find_quiet_split_lands_in_silence():
    # Silent 300 ms at 18.0–18.3 s of a 20 s chunk; tail window is 15–20 s.
    pcm = _pcm_with_silent_region(20.0, 18.0, 0.3)
    result = find_quiet_split(pcm)
    lo = int(18.0 * SAMPLE_RATE) * 2
    hi = int((18.3 - 0.2) * SAMPLE_RATE) * 2
    assert lo <= result <= hi
    assert result % 2 == 0


# ── probe ────────────────────────────────────────────────────────────────────


def test_probe_success(monkeypatch):
    monkeypatch.setattr("src.file_transcriber.ffprobe_path", lambda: "/fake/ffprobe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="codec_type=audio\nduration=123.4\n", stderr=""
        ),
    )
    assert probe("f.mp3") == (123.4, None)


def test_probe_no_audio(monkeypatch):
    monkeypatch.setattr("src.file_transcriber.ffprobe_path", lambda: "/fake/ffprobe")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(
            cmd, 0, stdout="codec_type=video\nduration=10.0\n", stderr=""
        ),
    )
    dur, err = probe("v.mp4")
    assert dur is None
    assert "no audio" in err


def test_probe_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr("src.file_transcriber.ffprobe_path", lambda: None)
    dur, err = probe("any")
    assert dur is None
    assert "ffmpeg not installed" in err


# ── FileTranscriptionJob ─────────────────────────────────────────────────────


def _stub_decode(monkeypatch, pcm: bytes, duration: float, tmp_path: Path):
    """Wire probe/ffmpeg/transcripts_dir stubs; ffmpeg 'decodes' to given pcm."""
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (duration, None))
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(pcm)  # output path is the last ffmpeg arg
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_job_end_to_end(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _silent_pcm(2.5), 2.5, tmp_path)
    monkeypatch.setattr("src.file_transcriber.transcribe", lambda wav, mode: ("hello world", None))

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("test_audio.mp3", "en", progress.append, done.append, errors.append)
    job._run()

    assert errors == []
    assert len(done) == 1
    assert progress and progress[-1] == 1.0
    content = Path(done[0]).read_text(encoding="utf-8")
    assert "# Pysar — test_audio.mp3" in content
    assert "language: en" in content
    assert "hello world" in content
    assert "**[0:00:00]**" in content
    assert "_— end —_" in content


def test_job_cancel_keeps_partial(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _silent_pcm(CHUNK_SEC * 2 + 15), CHUNK_SEC * 2 + 15, tmp_path)

    def fake_transcribe(wav, mode):
        job.cancel()  # cancel mid-run: flag is checked before the next chunk
        return "some text", None

    monkeypatch.setattr("src.file_transcriber.transcribe", fake_transcribe)

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("cancel.mp4", "ru", progress.append, done.append, errors.append)
    job._run()

    assert errors == []
    assert len(done) == 1
    content = Path(done[0]).read_text(encoding="utf-8")
    assert "_— cancelled —_" in content
    assert "some text" in content


def test_job_transcribe_error(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _silent_pcm(10.0), 10.0, tmp_path)
    monkeypatch.setattr("src.file_transcriber.transcribe", lambda wav, mode: (None, "boom"))

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("err.mp3", "auto", progress.append, done.append, errors.append)
    job._run()

    assert done == []
    assert len(errors) == 1 and "boom" in errors[0]
    (md,) = tmp_path.glob("file_err_*.md")
    assert "_— aborted: boom —_" in md.read_text(encoding="utf-8")


def test_job_shorter_decode_than_probe_terminates(monkeypatch, tmp_path):
    # ffprobe over-reports duration; the loop must end on real EOF, not spin.
    _stub_decode(monkeypatch, _silent_pcm(1.0), 5.0, tmp_path)
    monkeypatch.setattr("src.file_transcriber.transcribe", lambda wav, mode: ("ok", None))

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("short.wav", "en", progress.append, done.append, errors.append)
    job._run()

    assert errors == []
    assert len(done) == 1
    assert progress[-1] == 1.0


def test_temp_raw_file_removed(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (1.0, None))
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)
    monkeypatch.setattr("src.file_transcriber.transcribe", lambda wav, mode: ("ok", None))

    def fake_run(cmd, **kw):
        seen["raw"] = cmd[-1]
        Path(cmd[-1]).write_bytes(_silent_pcm(1.0))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    job = FileTranscriptionJob("t.wav", "en", lambda p: None, lambda p: None, lambda e: None)
    job._run()
    assert not os.path.exists(seen["raw"])


def test_temp_raw_file_removed_on_ffmpeg_error(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (10.0, None))
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)

    def fake_run(cmd, **kw):
        seen["raw"] = cmd[-1]
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="some error")

    monkeypatch.setattr(subprocess, "run", fake_run)

    errors = []
    job = FileTranscriptionJob("f.mp4", "uk", lambda p: None, lambda p: None, errors.append)
    job._run()
    assert len(errors) == 1
    assert not os.path.exists(seen["raw"])
