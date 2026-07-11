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
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .transcriber import transcribe
from .transcripts import transcripts_dir

SAMPLE_RATE = 16000  # whisper.cpp expects 16 kHz mono s16le
CHUNK_SEC = 60  # seconds fed per whisper call
TAIL_SEARCH_SEC = 5  # window at a chunk's end searched for a quiet split point


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
                "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1",
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
    ) -> None:
        self._path = path
        self._mode = mode
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_error = on_error
        self._cancel_event = threading.Event()
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
                    "-v", "error",
                    "-y",
                    "-i", self._path,
                    "-vn",
                    "-ac", "1",
                    "-ar", str(SAMPLE_RATE),
                    "-f", "s16le",
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

            with open(md_path, "w", encoding="utf-8") as md:
                md.write(
                    f"# Pysar — {src.name}\n\n"
                    f"_transcribed {now.strftime('%Y-%m-%d %H:%M')}, "
                    f"language: {self._mode}_\n\n"
                )
                md.flush()

                with open(raw_path, "rb") as raw:
                    while True:
                        if self._cancel_event.is_set():
                            md.write("_— cancelled —_\n")
                            md.flush()
                            self._on_done(str(md_path))
                            return

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

                        text, err = transcribe(pcm_to_wav(to_transcribe), self._mode)
                        if err is not None:
                            md.write(f"_— aborted: {err} —_\n")
                            md.flush()
                            self._on_error(err)
                            return

                        if text and text.strip():
                            start_sec = consumed / (SAMPLE_RATE * 2)
                            h, rem = divmod(int(start_sec), 3600)
                            m, s = divmod(rem, 60)
                            md.write(f"**[{h}:{m:02d}:{s:02d}]**\n\n{text.strip()}\n\n")
                            md.flush()

                        consumed += len(to_transcribe)
                        self._on_progress(min(consumed / total_bytes, 1.0))

                md.write("_— end —_\n")
                md.flush()
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
