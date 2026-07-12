"""Tests for offline file transcription. No real ffmpeg or network — the
subprocess and whisper layers are stubbed via monkeypatch."""

import array
import itertools
import json
import os
import struct
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from src.file_transcriber import (
    CHUNK_SEC,
    SAMPLE_RATE,
    TAIL_SEARCH_SEC,
    FileTranscriptionJob,
    FileTranscriptionQueue,
    TranscriptSegment,
    assign_speakers,
    find_quiet_split,
    is_clearly_silent,
    pcm_to_wav,
    probe,
    render_segments,
    scan_media,
    split_plan,
)


def _silent_pcm(duration_sec: float) -> bytes:
    return b"\x00\x00" * int(duration_sec * SAMPLE_RATE)


def _voiced_pcm(duration_sec: float, frequency: float = 180.0) -> bytes:
    t = np.arange(int(duration_sec * SAMPLE_RATE), dtype=np.float32) / SAMPLE_RATE
    signal = np.sin(2 * np.pi * frequency * t) * 4000
    return signal.astype("<i2").tobytes()


@pytest.fixture(autouse=True)
def _structured_whisper_falls_back(monkeypatch):
    monkeypatch.setattr(
        "src.file_transcriber.transcribe_segments", lambda wav, mode, prompt="": (None, None)
    )


def _pcm_with_silent_region(total_sec: float, silent_start: float, silent_dur: float) -> bytes:
    """Loud square-ish signal everywhere except one silent patch."""
    total = int(total_sec * SAMPLE_RATE)
    samples = array.array("h", [1000, -1000] * (total // 2 + 1))[:total]
    for i in range(
        int(silent_start * SAMPLE_RATE), min(int((silent_start + silent_dur) * SAMPLE_RATE), total)
    ):
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


def test_silence_gate_rejects_only_near_digital_silence():
    assert is_clearly_silent(_silent_pcm(1.0))
    assert not is_clearly_silent(_voiced_pcm(1.0))
    quiet = (np.sin(2 * np.pi * 220 * np.arange(SAMPLE_RATE) / SAMPLE_RATE) * 30).astype("<i2")
    assert not is_clearly_silent(quiet.tobytes())


def test_speaker_labels_preserve_every_word():
    pieces = []
    segments = []
    for idx, frequency in enumerate((180, 420, 180, 420)):
        pieces.append(_voiced_pcm(1.0, frequency))
        segments.append(TranscriptSegment(idx, idx + 1, f"word-{idx}"))
    before = [segment.text for segment in segments]
    assign_speakers(segments, b"".join(pieces))
    rendered = render_segments(segments)
    assert [segment.text for segment in segments] == before
    assert all(word in rendered for word in before)
    assert segments[0].speaker == segments[2].speaker
    assert segments[1].speaker == segments[3].speaker
    assert segments[0].speaker != segments[1].speaker


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
    monkeypatch.setattr("src.file_transcriber.ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(pcm)  # output path is the last ffmpeg arg
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_job_end_to_end(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _voiced_pcm(2.5), 2.5, tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("hello world", None)
    )

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
    sidecar = Path(done[0]).with_suffix(".segments.json")
    assert sidecar.exists()
    assert json.loads(sidecar.read_text(encoding="utf-8"))[0]["text"] == "hello world"


def test_job_digital_silence_never_calls_whisper(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _silent_pcm(2.5), 2.5, tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe",
        lambda *args, **kwargs: pytest.fail("digital silence reached Whisper"),
    )
    done = []
    job = FileTranscriptionJob("silent.wav", "uk", lambda p: None, done.append, pytest.fail)
    job._run()
    content = Path(done[0]).read_text(encoding="utf-8")
    assert "дякую за перегляд" not in content.lower()


def test_job_uses_structured_segments_verbatim(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _voiced_pcm(2.5), 2.5, tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe_segments",
        lambda wav, mode, prompt="": (
            [
                {"start": 0.2, "end": 1.0, "text": "перше слово", "no_speech_prob": 0.1},
                {"start": 1.1, "end": 2.0, "text": "друге слово", "no_speech_prob": 0.2},
            ],
            None,
        ),
    )
    monkeypatch.setattr(
        "src.file_transcriber.transcribe",
        lambda *args, **kwargs: pytest.fail("plain fallback should not run"),
    )
    done = []
    job = FileTranscriptionJob("dialog.wav", "uk", lambda p: None, done.append, pytest.fail)
    job._run()
    content = Path(done[0]).read_text(encoding="utf-8")
    assert "перше слово" in content and "друге слово" in content
    assert "Спікер 1 (основний)" in content


def test_job_cancel_keeps_partial(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _voiced_pcm(CHUNK_SEC * 2 + 15), CHUNK_SEC * 2 + 15, tmp_path)

    def fake_transcribe(wav, mode, prompt=""):
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
    _stub_decode(monkeypatch, _voiced_pcm(10.0), 10.0, tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": (None, "boom")
    )

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("err.mp3", "auto", progress.append, done.append, errors.append)
    job._run()

    assert done == []
    assert len(errors) == 1 and "boom" in errors[0]
    (md,) = tmp_path.glob("file_err_*.md")
    assert "_— aborted: boom —_" in md.read_text(encoding="utf-8")


def test_job_forwards_prompt_to_transcribe(monkeypatch, tmp_path):
    _stub_decode(monkeypatch, _voiced_pcm(2.5), 2.5, tmp_path)
    seen_prompts = []

    def fake_transcribe(wav, mode, prompt=""):
        seen_prompts.append(prompt)
        return "ok", None

    monkeypatch.setattr("src.file_transcriber.transcribe", fake_transcribe)

    job = FileTranscriptionJob(
        "hinted.mp3",
        "uk",
        lambda p: None,
        lambda p: None,
        lambda e: None,
        prompt="вебінар про Claude, MCP, агенти",
    )
    job._run()
    assert seen_prompts and all(p == "вебінар про Claude, MCP, агенти" for p in seen_prompts)


def test_job_shorter_decode_than_probe_terminates(monkeypatch, tmp_path):
    # ffprobe over-reports duration; the loop must end on real EOF, not spin.
    _stub_decode(monkeypatch, _voiced_pcm(1.0), 5.0, tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("ok", None)
    )

    progress, done, errors = [], [], []
    job = FileTranscriptionJob("short.wav", "en", progress.append, done.append, errors.append)
    job._run()

    assert errors == []
    assert len(done) == 1
    assert progress[-1] == 1.0


def test_temp_raw_file_removed(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (1.0, None))
    monkeypatch.setattr("src.file_transcriber.ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("ok", None)
    )

    def fake_run(cmd, **kw):
        seen["raw"] = cmd[-1]
        Path(cmd[-1]).write_bytes(_voiced_pcm(1.0))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    job = FileTranscriptionJob("t.wav", "en", lambda p: None, lambda p: None, lambda e: None)
    job._run()
    assert not os.path.exists(seen["raw"])


def test_temp_raw_file_removed_on_ffmpeg_error(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (10.0, None))
    monkeypatch.setattr("src.file_transcriber.ffmpeg_path", lambda: "/fake/ffmpeg")
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


# ── Batch queue ──────────────────────────────────────────────────────────────


def _wait_for_state(q: FileTranscriptionQueue, state: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if q.snapshot()["state"] == state:
            return True
        time.sleep(0.02)
    return False


def _queue_env(monkeypatch, tmp_path, duration: float = 5.0):
    """Hermetic decode stack for queue tests: probe OK, ffmpeg fake writes
    ``duration`` seconds of silence to the requested output path."""
    monkeypatch.setattr("src.file_transcriber.probe", lambda p: (duration, None))
    monkeypatch.setattr("src.file_transcriber.ffmpeg_path", lambda: "/fake/ffmpeg")
    monkeypatch.setattr("src.file_transcriber.transcripts_dir", lambda: tmp_path)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).write_bytes(_voiced_pcm(duration))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)


def _gated_transcribe(monkeypatch):
    """transcribe() stub that blocks until released — the only way to hold a
    queue deterministically mid-file (the fake decode is instantaneous, so
    sleep-based timing would be flaky to the point of failing)."""
    started = threading.Event()
    release = threading.Event()

    def fake(wav, mode, prompt=""):
        started.set()
        release.wait(timeout=5)
        return ("text", None)

    monkeypatch.setattr("src.file_transcriber.transcribe", fake)
    return started, release


def test_scan_media_dir_filter(tmp_path):
    (tmp_path / "song.mp3").touch()
    (tmp_path / "video.MKV").touch()  # extension match is case-insensitive
    (tmp_path / "readme.txt").touch()
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.wav").touch()
    (tmp_path / "subdir" / "ignore.doc").touch()
    explicit_non_media = tmp_path / "data.bin"  # picked by hand → passes through
    explicit_non_media.touch()

    result = scan_media([str(tmp_path), str(explicit_non_media)])
    names = sorted(Path(p).name for p in result)
    assert names == ["data.bin", "nested.wav", "song.mp3", "video.MKV"]


def test_scan_media_dedup_and_missing(tmp_path):
    a = tmp_path / "dup.wav"
    a.touch()
    result = scan_media([str(a), str(a), str(tmp_path), "/no/such/file.mp3"])
    assert result == [a.resolve().as_posix()]


def test_queue_empty_input():
    events = []
    q = FileTranscriptionQueue([], "uk", "", on_change=events.append)
    q.start()
    assert _wait_for_state(q, "done")
    snap = q.snapshot()
    assert snap["total"] == 0 and snap["done_count"] == 0
    assert events  # even a no-op run reports itself


def test_queue_prefilter_skips_with_reason(monkeypatch, tmp_path):
    good = tmp_path / "good.mp3"
    bad = tmp_path / "bad.mp4"
    good.touch()
    bad.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    monkeypatch.setattr(
        "src.file_transcriber.probe",
        lambda p: (None, "no audio track") if "bad" in p else (2.5, None),
    )
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("text", None)
    )

    q = FileTranscriptionQueue([str(good), str(bad)], "uk", "", on_change=lambda s: None)
    q.start()
    assert _wait_for_state(q, "done")
    by_name = {i["name"]: i for i in q.snapshot()["items"]}
    assert by_name["bad.mp4"]["status"] == "skipped"
    assert by_name["bad.mp4"]["error"] == "no audio track"
    assert by_name["good.mp3"]["status"] == "done"
    snap = q.snapshot()
    assert snap["total"] == 1 and snap["done_count"] == 1


def test_queue_two_files_sequential(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "first.mp3", tmp_path / "second.wav"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("hello words", None)
    )

    changes = []
    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "hint", on_change=changes.append)
    q.start()
    assert _wait_for_state(q, "done")
    snap = q.snapshot()
    assert snap["done_count"] == 2 and snap["total"] == 2
    for item in snap["items"]:
        assert item["status"] == "done"
        md = Path(item["result_path"])
        assert md.exists() and "hello words" in md.read_text(encoding="utf-8")


def test_queue_pause_at_chunk_boundary(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "a.m4a", tmp_path / "b.m4a"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    started, release = _gated_transcribe(monkeypatch)

    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "", on_change=lambda s: None)
    q.start()
    assert started.wait(timeout=5)  # file 1 is mid-transcription
    q.pause()
    release.set()  # finish the in-flight Whisper call; job must pause before EOF/next file
    deadline = time.time() + 5
    while time.time() < deadline:
        if q.snapshot()["state"] == "paused":
            break
        time.sleep(0.02)
    snap = q.snapshot()
    assert snap["state"] == "paused"
    assert snap["items"][0]["status"] == "running"
    assert snap["items"][1]["status"] == "pending"
    q.resume()
    assert _wait_for_state(q, "done")
    assert q.snapshot()["done_count"] == 2


def test_queue_cancel_all_mid_file(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "first.mp3", tmp_path / "second.mp3"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    started, release = _gated_transcribe(monkeypatch)

    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "", on_change=lambda s: None)
    q.start()
    assert started.wait(timeout=5)
    q.cancel_all()
    release.set()
    assert _wait_for_state(q, "cancelled")
    q._worker.join(timeout=2)
    items = q.snapshot()["items"]
    assert items[0]["status"] == "cancelled"
    assert items[0]["result_path"] != ""  # partial transcript survives
    assert Path(items[0]["result_path"]).exists()
    assert items[1]["status"] == "cancelled"


def test_queue_remove_pending(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "a.wav", tmp_path / "b.wav"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    monkeypatch.setattr(
        "src.file_transcriber.transcribe", lambda wav, mode, prompt="": ("text", None)
    )

    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "", on_change=lambda s: None)
    q.remove(2)  # drop b.wav before the queue even starts
    q.start()
    assert _wait_for_state(q, "done")
    items = q.snapshot()["items"]
    assert items[0]["status"] == "done"
    assert items[1]["status"] == "cancelled"
    assert q.snapshot()["done_count"] == 1


def test_queue_remove_running_continues_with_next(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "run.mp3", tmp_path / "next.m4a"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)
    started, release = _gated_transcribe(monkeypatch)

    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "", on_change=lambda s: None)
    q.start()
    assert started.wait(timeout=5)  # item 1 guaranteed running
    q.remove(1)
    release.set()
    assert _wait_for_state(q, "done")
    items = q.snapshot()["items"]
    assert items[0]["status"] == "cancelled"
    assert items[0]["result_path"] != ""  # partial kept
    assert items[1]["status"] == "done"


def test_queue_cancel_all_during_scanning(monkeypatch, tmp_path):
    probe_entered = threading.Event()
    probe_release = threading.Event()

    def slow_probe(path):
        probe_entered.set()
        probe_release.wait(timeout=5)
        return (10.0, None)

    files = []
    for i in (1, 2, 3):
        f = tmp_path / f"f{i}.mp3"
        f.touch()
        files.append(str(f))
    monkeypatch.setattr("src.file_transcriber.probe", slow_probe)

    q = FileTranscriptionQueue(files, "uk", "", on_change=lambda s: None)
    q.start()
    assert probe_entered.wait(timeout=5)
    q.cancel_all()
    probe_release.set()
    assert _wait_for_state(q, "cancelled")
    q._worker.join(timeout=2)
    assert all(i["status"] == "cancelled" for i in q.snapshot()["items"])


def test_queue_transcribe_error_marks_item_and_continues(monkeypatch, tmp_path):
    f1, f2 = tmp_path / "bad.mp3", tmp_path / "ok.mp3"
    f1.touch()
    f2.touch()
    _queue_env(monkeypatch, tmp_path, duration=2.5)

    def fake_transcribe(wav, mode, prompt=""):
        return (None, "server down") if fake_transcribe.calls == 0 else ("text", None)

    fake_transcribe.calls = 0
    real = fake_transcribe

    def counting(wav, mode, prompt=""):
        result = real(wav, mode, prompt)
        real.calls += 1
        return result

    monkeypatch.setattr("src.file_transcriber.transcribe", counting)

    q = FileTranscriptionQueue([str(f1), str(f2)], "uk", "", on_change=lambda s: None)
    q.start()
    assert _wait_for_state(q, "done")
    items = q.snapshot()["items"]
    assert items[0]["status"] == "error" and items[0]["error"] == "server down"
    assert items[1]["status"] == "done"
