"""Offline file transcription: ffmpeg decode → chunk → whisper → Markdown.

Powers the "transcribe a file" screen in Settings. A video/audio file is
decoded to 16 kHz mono PCM on disk (never in RAM — a 2 h video is ~230 MB and
the target machine has 8 GB), cut into ~60 s chunks at quiet points so words
aren't split mid-syllable, and each chunk goes through the same whisper client
as live dictation. Output is a Markdown transcript with media timecodes.

No AppKit imports — the module is unit-testable like transcripts.py. Callbacks
are invoked from the worker thread; marshalling to the main thread is the
caller's job.
"""

import array
import contextlib
import io
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from .transcriber import transcribe, transcribe_segments
from .transcripts import transcripts_dir

SAMPLE_RATE = 16000  # whisper.cpp expects 16 kHz mono s16le
CHUNK_SEC = 60  # seconds fed per whisper call
TAIL_SEARCH_SEC = 5  # window at a chunk's end searched for a quiet split point
SILENCE_PEAK = 24  # exact/near digital silence only; quiet speech must survive
SILENCE_RMS = 8.0


# ── ffmpeg discovery ─────────────────────────────────────────────────────────


def _find_binary(name: str) -> str | None:
    path = shutil.which(name)
    if path:
        return path
    # The .app bundle's PATH doesn't include Homebrew, so check its prefixes.
    for candidate in (f"/opt/homebrew/bin/{name}", f"/usr/local/bin/{name}"):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def ffmpeg_path() -> str | None:
    return _find_binary("ffmpeg")


def ffprobe_path() -> str | None:
    return _find_binary("ffprobe")


# ── Pure helpers (testable) ──────────────────────────────────────────────────


def probe(path: str) -> tuple[float | None, str | None]:
    """Returns (duration_seconds, error). Exactly one of them is always None."""
    ffprobe = ffprobe_path()
    if ffprobe is None:
        return None, "ffmpeg not installed"
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return None, "ffprobe timed out"
    except Exception as exc:
        return None, f"ffprobe failed: {exc}"

    output = completed.stdout
    if "codec_type=audio" not in output:
        return None, "no audio track"
    duration: float | None = None
    for line in output.splitlines():
        if line.startswith("duration="):
            with contextlib.suppress(ValueError):
                duration = float(line.split("=", 1)[1])
    if duration is None or duration <= 0:
        return None, "could not parse duration"
    return duration, None


def split_plan(total_sec: float, chunk_sec: float = CHUNK_SEC) -> list[tuple[float, float]]:
    """Gapless (start, end) windows covering [0, total]. A final remainder
    shorter than 10 s is merged into the previous window — whisper tends to
    hallucinate on tiny tails."""
    if total_sec <= chunk_sec:
        return [(0.0, total_sec)]
    chunks: list[tuple[float, float]] = []
    pos = 0.0
    while pos < total_sec:
        end = min(pos + chunk_sec, total_sec)
        chunks.append((pos, end))
        pos = end
    if len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) < 10.0:
        last = chunks.pop()
        prev = chunks.pop()
        chunks.append((prev[0], last[1]))
    return chunks


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw s16le mono PCM in a minimal WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def find_quiet_split(
    pcm: bytes, sample_rate: int = SAMPLE_RATE, tail_sec: float = TAIL_SEARCH_SEC
) -> int:
    """Byte offset (sample-aligned) of the quietest 200 ms window inside the
    last ``tail_sec`` seconds — the least-bad place to cut so a word isn't
    split across two whisper calls. Chunks shorter than 2×tail are kept whole.

    Uses a prefix sum over |sample| so the scan is O(n), not O(n·window)."""
    samples = array.array("h")
    samples.frombytes(pcm)
    total = len(samples)
    window = int(sample_rate * 0.2)
    if window == 0 or total < int(tail_sec * sample_rate) * 2:
        return len(pcm)

    search_start = max(0, total - int(tail_sec * sample_rate))
    if search_start > total - window:
        return len(pcm)

    tail = samples[search_start:]
    prefix = [0]
    acc = 0
    for v in tail:
        acc += v if v >= 0 else -v
        prefix.append(acc)

    best_local = 0
    best_energy = None
    for start in range(len(tail) - window + 1):
        energy = prefix[start + window] - prefix[start]
        if best_energy is None or energy < best_energy:
            best_energy = energy
            best_local = start
    return (search_start + best_local) * 2


def is_clearly_silent(pcm: bytes) -> bool:
    """Conservative pre-Whisper gate for digital silence.

    This intentionally does not try to recognize speech.  It only rejects a
    near-zero signal; ambiguous room tone and quiet speech are preserved and
    left to the server's Silero VAD.
    """
    if not pcm:
        return True
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if samples.size == 0:
        return True
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples * samples)))
    return peak <= SILENCE_PEAK and rms <= SILENCE_RMS


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str
    no_speech_prob: float = 0.0
    speaker: str = ""


def _speaker_feature(pcm: bytes) -> np.ndarray | None:
    """Small, dependency-free spectral voice signature for best-effort labels."""
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    if samples.size < SAMPLE_RATE // 3:
        return None
    samples -= float(np.mean(samples))
    peak = float(np.max(np.abs(samples)))
    if peak < 32:
        return None
    samples /= peak
    frame = 400
    hop = 160
    count = 1 + (samples.size - frame) // hop
    if count <= 0:
        return None
    # Cap work on long segments while sampling their full duration.
    indices = np.linspace(0, count - 1, min(count, 160), dtype=int)
    window = np.hanning(frame).astype(np.float32)
    spectra = []
    for idx in indices:
        chunk = samples[idx * hop : idx * hop + frame] * window
        power = np.abs(np.fft.rfft(chunk)) ** 2
        bands = np.array_split(power[1:161], 16)
        spectra.append([math.log1p(float(np.mean(b))) for b in bands])
    matrix = np.asarray(spectra, dtype=np.float32)
    feature = np.concatenate((matrix.mean(axis=0), matrix.std(axis=0)))
    norm = float(np.linalg.norm(feature))
    return feature / norm if norm > 0 else None


def _kmeans(features: np.ndarray, k: int) -> tuple[np.ndarray, float]:
    """Deterministic tiny k-means; returns labels and within-cluster loss."""
    centers = [features[0]]
    while len(centers) < k:
        distances = np.min(np.stack([np.sum((features - c) ** 2, axis=1) for c in centers]), axis=0)
        centers.append(features[int(np.argmax(distances))])
    centers_arr = np.asarray(centers)
    labels = np.zeros(len(features), dtype=int)
    for _ in range(30):
        distances = np.stack(
            [np.sum((features - center) ** 2, axis=1) for center in centers_arr], axis=1
        )
        new_labels = np.argmin(distances, axis=1)
        if np.array_equal(new_labels, labels) and _:
            break
        labels = new_labels
        for idx in range(k):
            members = features[labels == idx]
            if len(members):
                centers_arr[idx] = members.mean(axis=0)
    loss = float(np.sum((features - centers_arr[labels]) ** 2))
    return labels, loss


def assign_speakers(segments: list[TranscriptSegment], pcm: bytes) -> None:
    """Add best-effort speaker labels; never touches segment text."""
    usable: list[tuple[int, np.ndarray]] = []
    for idx, segment in enumerate(segments):
        start = max(0, int(segment.start * SAMPLE_RATE) * 2)
        end = min(len(pcm), int(segment.end * SAMPLE_RATE) * 2)
        feature = _speaker_feature(pcm[start:end])
        if feature is not None:
            usable.append((idx, feature))
    if not usable:
        return
    features = np.stack([feature for _, feature in usable])
    max_k = min(4, len(features))
    candidates = []
    for k in range(1, max_k + 1):
        labels, loss = _kmeans(features, k)
        # BIC-like penalty prevents every short segment becoming a speaker.
        score = len(features) * math.log(max(loss / len(features), 1e-9)) + k * 8.0
        candidates.append((score, labels))
    labels = min(candidates, key=lambda item: item[0])[1]
    durations: dict[int, float] = {}
    for (segment_idx, _), label in zip(usable, labels, strict=True):
        segment = segments[segment_idx]
        durations[int(label)] = durations.get(int(label), 0.0) + max(
            0.0, segment.end - segment.start
        )
    order = {
        label: rank + 1
        for rank, label in enumerate(sorted(durations, key=durations.get, reverse=True))
    }
    for (segment_idx, _), label in zip(usable, labels, strict=True):
        number = order[int(label)]
        segments[segment_idx].speaker = f"Спікер {number}" + (" (основний)" if number == 1 else "")
    for segment in segments:
        if not segment.speaker:
            segment.speaker = "Спікер ?"


def render_segments(segments: list[TranscriptSegment]) -> str:
    """Format labels while proving that the canonical Whisper text survives."""
    before = " ".join(segment.text for segment in segments).split()
    blocks = []
    rendered_text = []
    for segment in segments:
        h, rem = divmod(int(segment.start), 3600)
        m, s = divmod(rem, 60)
        label = f" {segment.speaker}:" if segment.speaker else ""
        blocks.append(f"**[{h}:{m:02d}:{s:02d}]{label}**\n\n{segment.text}\n\n")
        rendered_text.append(segment.text)
    if " ".join(rendered_text).split() != before:
        # Defensive fallback: labels are expendable, recognized text is not.
        for segment in segments:
            segment.speaker = ""
        return render_segments(segments)
    return "".join(blocks)


# ── The job ──────────────────────────────────────────────────────────────────


class FileTranscriptionJob:
    """Transcribes one media file on a daemon thread.

    Callbacks fire on the worker thread. Progress is 0.0–1.0 of decoded audio
    consumed. cancel() stops after the in-flight whisper call; the partial
    transcript is kept and on_done still fires (a half transcript is useful)."""

    def __init__(
        self,
        path: str,
        mode: str,
        on_progress: Callable[[float], None],
        on_done: Callable[[str], None],
        on_error: Callable[[str], None],
        prompt: str = "",
        on_paused: Callable[[], None] | None = None,
    ) -> None:
        self._path = path
        self._mode = mode
        self._prompt = prompt
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_error = on_error
        self._on_paused = on_paused or (lambda: None)
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._running = False
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.set()

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._pause_event.set()

    def _run(self) -> None:
        raw_path: str | None = None
        md_path: Path | None = None
        try:
            _duration, err = probe(self._path)
            if err is not None:
                self._on_error(err)
                return

            ffmpeg = ffmpeg_path()
            if ffmpeg is None:
                self._on_error("ffmpeg not installed")
                return

            with tempfile.NamedTemporaryFile(delete=False, suffix=".raw") as tmp:
                raw_path = tmp.name
            # -y: the temp file already exists; without it ffmpeg blocks on an
            # interactive overwrite prompt forever.
            result = subprocess.run(
                [
                    ffmpeg,
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    self._path,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    str(SAMPLE_RATE),
                    "-f",
                    "s16le",
                    raw_path,
                ],
                capture_output=True,
                text=True,
                timeout=7200,
            )
            if result.returncode != 0:
                tail = result.stderr[-300:] if result.stderr else "unknown ffmpeg error"
                self._on_error(f"ffmpeg decode failed: {tail}")
                return

            # Progress runs off the ACTUAL decoded size — ffprobe's duration is
            # an estimate and trusting it can over- or under-run the loop.
            total_bytes = os.path.getsize(raw_path)
            if total_bytes == 0:
                self._on_error("decoded audio is empty")
                return

            src = Path(self._path)
            now = datetime.now()
            md_path = transcripts_dir() / (
                f"file_{src.stem}_{now.strftime('%Y-%m-%d_%H-%M-%S')}.md"
            )
            bytes_per_chunk = CHUNK_SEC * SAMPLE_RATE * 2
            consumed = 0
            carry = b""
            canonical: list[TranscriptSegment] = []
            can_diarize = True

            with open(md_path, "w", encoding="utf-8") as md:
                md.write(
                    f"# Pysar — {src.name}\n\n"
                    f"_transcribed {now.strftime('%Y-%m-%d %H:%M')}, "
                    f"language: {self._mode}_\n\n"
                    "_Спікери визначені автоматично; номери не ідентифікують людей._\n\n"
                )
                md.flush()

                with open(raw_path, "rb") as raw:
                    while True:
                        if self._cancel_event.is_set():
                            if can_diarize:
                                with contextlib.suppress(Exception):
                                    assign_speakers(canonical, Path(raw_path).read_bytes())
                            md.write(render_segments(canonical))
                            md.write("_— cancelled —_\n")
                            md.flush()
                            self._on_done(str(md_path))
                            return

                        # pause() takes effect at a safe chunk boundary. cancel()
                        # releases this wait, so a paused job remains cancellable.
                        if not self._pause_event.is_set():
                            self._on_paused()
                            self._pause_event.wait()
                        if self._cancel_event.is_set():
                            continue

                        chunk = carry + raw.read(max(0, bytes_per_chunk - len(carry)))
                        if not chunk:
                            break
                        is_last = consumed + len(chunk) >= total_bytes

                        if not is_last and len(chunk) >= TAIL_SEARCH_SEC * SAMPLE_RATE * 2:
                            split = find_quiet_split(chunk)
                            to_transcribe, carry = chunk[:split], chunk[split:]
                        else:
                            to_transcribe, carry = chunk, b""
                        if not to_transcribe:
                            # A degenerate split; push everything through as-is.
                            to_transcribe, carry = chunk, b""

                        chunk_start = consumed / (SAMPLE_RATE * 2)
                        if is_clearly_silent(to_transcribe):
                            segments, err = [], None
                        else:
                            wav = pcm_to_wav(to_transcribe)
                            raw_segments, err = transcribe_segments(wav, self._mode, self._prompt)
                            if raw_segments is None and err is None:
                                can_diarize = False
                                text, err = transcribe(wav, self._mode, self._prompt)
                                raw_segments = (
                                    [
                                        {
                                            "start": 0.0,
                                            "end": len(to_transcribe) / (SAMPLE_RATE * 2),
                                            "text": text,
                                        }
                                    ]
                                    if text
                                    else []
                                )
                            segments = [
                                TranscriptSegment(
                                    start=chunk_start + float(segment["start"]),
                                    end=chunk_start + float(segment["end"]),
                                    text=str(segment["text"]),
                                    no_speech_prob=float(segment.get("no_speech_prob", 0.0)),
                                )
                                for segment in (raw_segments or [])
                                if str(segment.get("text", "")).strip()
                            ]
                        if err is not None:
                            md.write(f"_— aborted: {err} —_\n")
                            md.flush()
                            self._on_error(err)
                            return

                        canonical.extend(segments)

                        consumed += len(to_transcribe)
                        self._on_progress(min(consumed / total_bytes, 1.0))

                raw_pcm = Path(raw_path).read_bytes()
                if can_diarize:
                    with contextlib.suppress(Exception):
                        assign_speakers(canonical, raw_pcm)
                md.write(render_segments(canonical))
                md.write("_— end —_\n")
                md.flush()
            sidecar = md_path.with_suffix(".segments.json")
            sidecar.write_text(
                json.dumps(
                    [
                        {
                            "start": segment.start,
                            "end": segment.end,
                            "text": segment.text,
                            "no_speech_prob": segment.no_speech_prob,
                            "speaker": segment.speaker,
                        }
                        for segment in canonical
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            self._on_done(str(md_path))

        except Exception as exc:
            if md_path is not None:
                with contextlib.suppress(Exception), open(md_path, "a", encoding="utf-8") as md:
                    md.write(f"_— aborted: {exc} —_\n")
            self._on_error(str(exc))
        finally:
            if raw_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(raw_path)
            self._running = False


# ── Batch queue ──────────────────────────────────────────────────────────────

MEDIA_EXTS: frozenset[str] = frozenset(
    {
        ".mp3",
        ".m4a",
        ".wav",
        ".aac",
        ".flac",
        ".ogg",
        ".opus",
        ".wma",
        ".aiff",
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".webm",
        ".m4v",
        ".mpg",
        ".mpeg",
        ".ts",
        ".flv",
        ".wmv",
        ".3gp",
    }
)


def scan_media(paths: list[str]) -> list[str]:
    """Expand directories recursively, keeping only media files by extension.
    Explicitly given files pass through as-is regardless of extension — the
    user picked them on purpose, and probe() is the real gatekeeper anyway.
    Deduplicated (by resolved path) and sorted; nonexistent paths dropped."""
    chosen: set[str] = set()
    for raw in paths:
        p = Path(raw)
        if not p.exists():
            continue
        if p.is_dir():
            for child in p.rglob("*"):
                if child.is_file() and child.suffix.lower() in MEDIA_EXTS:
                    chosen.add(child.resolve().as_posix())
        elif p.is_file():
            chosen.add(p.resolve().as_posix())
    return sorted(chosen)


@dataclass
class QueueItem:
    id: int
    path: str
    name: str
    status: str  # pending | running | done | error | skipped | cancelled
    progress: float = 0.0
    result_path: str = ""
    error: str = ""
    # A running item was removed/cancelled: its job fires on_done with the
    # partial transcript (kept), but the item must not read as "done".
    _will_cancel: bool = field(default=False, repr=False)


class FileTranscriptionQueue:
    """Sequential batch of FileTranscriptionJobs on one daemon worker thread.

    Sequential because there is a single whisper.cpp server — parallel jobs
    would fight over it. Phase 1 probes every candidate up front (each probe
    has a 30 s timeout, so this happens on the worker, state "scanning") and
    visibly skips the unusable ones; phase 2 runs the survivors one by one.

    One callback: on_change(snapshot), fired from worker threads after every
    meaningful change — marshalling to the main thread is the caller's job.
    pause() reaches the active job and takes effect after its in-flight Whisper
    request; between files the queue waits on its condition variable.
    """

    def __init__(
        self,
        paths: list[str],
        mode: str,
        prompt: str,
        on_change: Callable[[dict], None],
    ) -> None:
        self._mode = mode
        self._prompt = prompt
        self._on_change = on_change
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._state = "idle"  # idle | scanning | running | pausing | paused | done | cancelled
        self._items: list[QueueItem] = []
        self._items_by_id: dict[int, QueueItem] = {}
        self._worker: threading.Thread | None = None
        self._paused = False
        self._cancel_all_event = threading.Event()
        self._job_complete_event = threading.Event()
        self._current_job: FileTranscriptionJob | None = None
        self._current_item_id: int | None = None

        for idx, path in enumerate(scan_media(paths), start=1):
            item = QueueItem(id=idx, path=path, name=os.path.basename(path), status="pending")
            self._items.append(item)
            self._items_by_id[idx] = item

    # ── public interface ─────────────────────────────────────────────────────

    def start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._worker_run, daemon=True)
        self._worker.start()

    def pause(self) -> None:
        with self._lock:
            if self._state != "running":
                return
            self._paused = True
            if self._current_job is not None:
                self._current_job.pause()
                self._state = "pausing"
            else:
                self._state = "paused"
            snap = self._make_snapshot()
        self._on_change(snap)

    def resume(self) -> None:
        with self._lock:
            if self._state not in ("pausing", "paused"):
                return
            self._paused = False
            self._state = "running"
            if self._current_job is not None:
                self._current_job.resume()
            snap = self._make_snapshot()
        self._on_change(snap)
        with self._cond:
            self._cond.notify_all()

    def cancel_all(self) -> None:
        with self._lock:
            if self._current_item_id is not None:
                item = self._items_by_id.get(self._current_item_id)
                if item is not None and item.status == "running":
                    item._will_cancel = True
                if self._current_job is not None:
                    self._current_job.cancel()  # just sets an event — lock-safe
            for item in self._items:
                if item.status == "pending":
                    item.status = "cancelled"
            self._state = "cancelled"
            snap = self._make_snapshot()
        self._on_change(snap)
        self._cancel_all_event.set()
        with self._cond:
            self._cond.notify_all()

    def remove(self, item_id: int) -> None:
        """Drop one item: pending → cancelled; the running one → cancel its job
        (partial transcript survives). Finished/unknown ids are ignored."""
        job_to_cancel: FileTranscriptionJob | None = None
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item is None:
                return
            if item.status == "pending":
                item.status = "cancelled"
            elif item.status == "running" and self._current_item_id == item_id:
                item._will_cancel = True
                job_to_cancel = self._current_job
            else:
                return
            snap = self._make_snapshot()
        self._on_change(snap)
        if job_to_cancel is not None:
            job_to_cancel.cancel()

    def snapshot(self) -> dict:
        with self._lock:
            return self._make_snapshot()

    # ── private ──────────────────────────────────────────────────────────────

    def _make_snapshot(self) -> dict:
        """Caller must hold the lock. total excludes skipped items so the
        "3/7" counter reflects files that can actually produce a transcript."""
        items_data = [
            {
                "id": i.id,
                "name": i.name,
                "path": i.path,
                "status": i.status,
                "progress": i.progress,
                "result_path": i.result_path,
                "error": i.error,
            }
            for i in self._items
        ]
        done_count = sum(1 for i in self._items if i.status == "done")
        total = sum(1 for i in self._items if i.status != "skipped")
        return {"state": self._state, "items": items_data, "done_count": done_count, "total": total}

    def _worker_run(self) -> None:
        # Phase 1 — probe every candidate before any transcription.
        with self._lock:
            self._state = "scanning"
            snap = self._make_snapshot()
        self._on_change(snap)

        for item in self._items:
            if self._cancel_all_event.is_set():
                break
            with self._lock:
                # remove() may have cancelled it while we were probing others —
                # probing it anyway could overwrite "cancelled" with "skipped".
                if item.status != "pending":
                    continue
            _duration, err = probe(item.path)
            with self._lock:
                if err is not None and item.status == "pending":
                    item.status = "skipped"
                    item.error = err
                snap = self._make_snapshot()
            self._on_change(snap)

        # Phase 2 — run the survivors one by one. pause() only applies from
        # the "running" state, so the transition must happen before the loop.
        with self._lock:
            if not self._cancel_all_event.is_set():
                self._state = "running"
                snap = self._make_snapshot()
        self._on_change(snap)

        while not self._cancel_all_event.is_set():
            with self._cond:
                while self._paused and not self._cancel_all_event.is_set():
                    self._cond.wait()
            if self._cancel_all_event.is_set():
                break

            with self._lock:
                item = next((i for i in self._items if i.status == "pending"), None)
                if item is None:
                    break
                item.status = "running"
                item.progress = 0.0
                # The job must become cancellable in the same critical section
                # that marks the item running — otherwise remove()/cancel_all()
                # can observe "running" while _current_job is still None and
                # the cancel is silently lost.
                job = FileTranscriptionJob(
                    item.path,
                    self._mode,
                    on_progress=lambda p, iid=item.id: self._on_job_progress(iid, p),
                    on_done=lambda md, iid=item.id: self._on_job_done(iid, md),
                    on_error=lambda e, iid=item.id: self._on_job_error(iid, e),
                    prompt=self._prompt,
                    on_paused=self._on_job_paused,
                )
                self._current_job = job
                self._current_item_id = item.id
                self._job_complete_event.clear()
                snap = self._make_snapshot()
            self._on_change(snap)

            job.start()
            self._job_complete_event.wait()
            with self._lock:
                self._current_job = None
                self._current_item_id = None

        with self._lock:
            if self._cancel_all_event.is_set():
                for item in self._items:
                    if item.status == "pending":
                        item.status = "cancelled"
                self._state = "cancelled"
            else:
                self._state = "done"
            snap = self._make_snapshot()
        self._on_change(snap)

    # Job callbacks arrive on the job's worker thread.

    def _on_job_progress(self, item_id: int, progress: float) -> None:
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item is None:
                return
            item.progress = progress
            snap = self._make_snapshot()
        self._on_change(snap)

    def _on_job_paused(self) -> None:
        with self._lock:
            if not self._paused or self._state != "pausing":
                return
            self._state = "paused"
            snap = self._make_snapshot()
        self._on_change(snap)

    def _on_job_done(self, item_id: int, result_path: str) -> None:
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item is None:
                return
            item.result_path = result_path
            item.status = "cancelled" if item._will_cancel else "done"
            snap = self._make_snapshot()
        self._on_change(snap)
        self._job_complete_event.set()

    def _on_job_error(self, item_id: int, error: str) -> None:
        with self._lock:
            item = self._items_by_id.get(item_id)
            if item is None:
                return
            item.status = "error"
            item.error = error
            snap = self._make_snapshot()
        self._on_change(snap)
        self._job_complete_event.set()
