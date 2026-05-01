"""Microphone capture via sounddevice. WAV is kept in memory, never written to disk."""

import io
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from .config import CHANNELS, CHUNK_SIZE, MIN_RECORDING_SEC, SAMPLE_RATE


class AudioRecorder:
    def __init__(self):
        self._frames: list[bytes] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0

    def start(self):
        self._frames = []
        self._stop_event.clear()
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()

    def stop(self) -> bytes | None:
        """Stops recording and returns the WAV bytes (or None if the clip was too short)."""
        duration = time.time() - self._started_at
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

        if duration < MIN_RECORDING_SEC:
            print(f"⚠️ Recording too short: {duration:.2f}s")
            return None
        return self._to_wav()

    def _record(self):
        try:
            with sd.InputStream(
                channels=CHANNELS,
                samplerate=SAMPLE_RATE,
                blocksize=CHUNK_SIZE,
                dtype=np.float32,
            ) as stream:
                while not self._stop_event.is_set():
                    chunk, _ = stream.read(CHUNK_SIZE)
                    if chunk is not None and chunk.size > 0:
                        self._frames.append(np.mean(chunk, axis=1).tobytes())
        except Exception as e:
            print(f"‼️ Recording error: {e}")

    def _to_wav(self) -> bytes | None:
        if not self._frames:
            return None
        data = np.frombuffer(b"".join(self._frames), dtype=np.float32)
        if data.size == 0:
            return None
        data_i16 = (data * 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(data_i16.tobytes())
        return buf.getvalue()
