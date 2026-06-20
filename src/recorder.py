"""Microphone capture via sounddevice. WAV is kept in memory, never written to disk."""

import io
import threading
import time
import wave

import numpy as np
import sounddevice as sd

from .config import CHANNELS, CHUNK_SIZE, MIN_RECORDING_SEC, SAMPLE_RATE


def list_input_devices() -> list[str]:
    """Names of devices that can capture audio, for the menu's mic picker."""
    try:
        seen: list[str] = []
        for dev in sd.query_devices():
            if dev.get("max_input_channels", 0) > 0:
                name = dev.get("name", "")
                if name and name not in seen:
                    seen.append(name)
        return seen
    except Exception as e:
        print(f"⚠️ could not list input devices: {e}")
        return []


def _resolve_device(name: str | None):
    """Map a stored device *name* to a sounddevice index. None / unknown → default.

    Names are stable across reconnects; indices aren't, so we always re-resolve
    at stream-open time and silently fall back to the system default if the
    chosen mic is gone (unplugged headset, etc.)."""
    if not name:
        return None
    try:
        for i, dev in enumerate(sd.query_devices()):
            if dev.get("max_input_channels", 0) > 0 and dev.get("name") == name:
                return i
    except Exception:
        pass
    return None  # not found → default device


class AudioRecorder:
    def __init__(self, device: str | None = None):
        self._frames: list[bytes] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self._device = device  # input device *name* or None for system default

    def set_device(self, name: str | None) -> None:
        """Change the input device. Takes effect on the next recording."""
        self._device = name

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
                device=_resolve_device(self._device),
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

        # Peak-normalize quiet input. The built-in Air mic runs hot-and-low; a
        # faint signal makes turbo guess. Scale the loudest sample toward full
        # scale, but cap the gain so we don't amplify a near-silent noise floor
        # into garbage (VAD upstream already discards true silence).
        peak = float(np.max(np.abs(data)))
        if peak > 1e-3:
            gain = min(0.95 / peak, 8.0)  # ≤ +18 dB
            data = data * gain

        data_i16 = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(data_i16.tobytes())
        return buf.getvalue()
