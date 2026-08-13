"""Microphone capture via sounddevice. WAV is kept in memory, never written to disk."""

import contextlib
import io
import threading
import time
import wave
from collections.abc import Callable

import numpy as np
import sounddevice as sd

from .config import (
    CAPTURE_CHUNK_SIZE,
    CAPTURE_RATE,
    CHANNELS,
    CHUNK_SIZE,
    MAX_SEG_SEC,
    MICRO_PAUSE_SEC,
    MIN_RECORDING_SEC,
    MIN_SEG_SEC,
    PAUSE_SEC,
    SAMPLE_RATE,
    SILENCE_MARGIN,
    SOFT_SEG_SEC,
)
from .resample import Downsampler
from .segmenter import Segmenter


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


def pcm_to_wav(
    float32_bytes: bytes, rate: int = SAMPLE_RATE, normalize: bool = True
) -> bytes | None:
    """Wrap raw mono float32 PCM as a 16-bit WAV (in memory) at `rate`.

    Shared by the batch path (whole clip) and the streaming path (one segment):
    peak-normalizes quiet input — the built-in Air mic runs hot-and-low; a faint
    signal makes turbo guess — then scales the loudest sample toward full scale,
    capping the gain so a near-silent noise floor isn't amplified into garbage
    (VAD upstream already discards true silence).

    `normalize=False` is for the TTS-dataset copy: per-clip peak gain is exactly
    what a voice model must NOT learn from — it makes loudness drift clip to clip
    and bakes that drift into the synthesized voice. The dataset keeps the level
    the mic actually delivered; level matching, if any, happens once over the
    whole corpus at training-prep time."""
    data = np.frombuffer(float32_bytes, dtype=np.float32)
    if data.size == 0:
        return None

    if normalize:
        peak = float(np.max(np.abs(data)))
        if peak > 1e-3:
            # ≤ +12 dB. Higher gain (was +18) over-amplifies a quiet/short segment's
            # room tone until turbo hallucinates words out of the noise; +12 lifts the
            # hot-and-low Air mic enough without blowing up the noise bed.
            gain = min(0.95 / peak, 4.0)
            data = data * gain

    data_i16 = (np.clip(data, -1.0, 1.0) * 32767).astype(np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data_i16.tobytes())
    return buf.getvalue()


class AudioRecorder:
    def __init__(self, device: str | None = None, dataset: bool = False):
        self._frames: list[bytes] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_at = 0.0
        self._device = device  # input device *name* or None for system default
        self._on_segment: Callable[[bytes], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._segmenter: Segmenter | None = None
        # TTS-dataset capture: keep the untouched full-band audio of the take
        # alongside the 16 kHz stream that whisper eats. Off ⇒ not one extra byte
        # is retained, so the privacy default ("audio lives in RAM") is unchanged.
        self._dataset = bool(dataset)
        self._raw_frames: list[bytes] = []
        self._downsampler = Downsampler()
        # The rate the mic actually opened at — 48 kHz normally, SAMPLE_RATE if
        # the device refused it (then there is nothing to decimate).
        self._rate = CAPTURE_RATE

    def set_device(self, name: str | None) -> None:
        """Change the input device. Takes effect on the next recording."""
        self._device = name

    def set_dataset(self, enabled: bool) -> None:
        """Turn the TTS-dataset copy on/off. Takes effect on the next recording."""
        self._dataset = bool(enabled)

    def raw_wav(self) -> bytes | None:
        """The finished take as an un-normalized WAV at the capture rate, for the
        TTS dataset. None unless dataset capture was on for this take."""
        if not self._raw_frames:
            return None
        return pcm_to_wav(b"".join(self._raw_frames), rate=self._rate, normalize=False)

    def raw_rate(self) -> int:
        return self._rate

    def start(
        self,
        on_segment: Callable[[bytes], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """Begin capture. When `on_segment` is given (streaming mode) the audio is
        fed through a Segmenter and `on_segment(seg_wav)` fires per finished
        segment as the user speaks; the full clip is still buffered so stop()
        returns it for recoverability. `on_error(msg)` reports a mic-open failure
        that couldn't be recovered, so the app can surface a visible status."""
        self._frames = []
        self._raw_frames = []
        self._downsampler.reset()
        self._stop_event.clear()
        self._started_at = time.time()
        self._on_segment = on_segment
        self._on_error = on_error
        self._segmenter = (
            Segmenter(
                sample_rate=SAMPLE_RATE,
                block_size=CHUNK_SIZE,
                pause_sec=PAUSE_SEC,
                min_seg_sec=MIN_SEG_SEC,
                max_seg_sec=MAX_SEG_SEC,
                silence_margin=SILENCE_MARGIN,
                soft_seg_sec=SOFT_SEG_SEC,
                micro_pause_sec=MICRO_PAUSE_SEC,
            )
            if on_segment is not None
            else None
        )
        self._thread = threading.Thread(target=self._record, daemon=True)
        self._thread.start()

    def stop(self) -> bytes | None:
        """Stops recording and returns the WAV bytes (or None if the clip was too
        short). In streaming mode the trailing segment is flushed through
        `on_segment` first, so no tail speech is lost."""
        duration = time.time() - self._started_at
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=3)

        # Flush the final partial segment before reporting duration, so even a
        # short-but-complete last sentence is delivered to the streaming worker.
        if self._segmenter is not None and self._on_segment is not None:
            with contextlib.suppress(Exception):
                tail = self._segmenter.flush()
                if tail:
                    seg_wav = pcm_to_wav(tail)
                    if seg_wav:
                        self._on_segment(seg_wav)

        if duration < MIN_RECORDING_SEC:
            print(f"⚠️ Recording too short: {duration:.2f}s")
            return None
        return self._to_wav()

    def _open_stream(self):
        """Open the input stream, retrying on failure. Three failure modes are handled:

        1. A transient CoreAudio glitch (PaErrorCode -9986 / AUHAL) on a
           freshly-(re)opened device that clears on a second attempt.
        2. A stale PortAudio context. PortAudio is initialized once per process
           and caches the device list + HAL state at that time. If the input
           device was held by another process (e.g. a leaked ScreenCaptureKit
           capture) when we started, the cached context stays broken and every
           open keeps failing in *this* process even after the device is free —
           a freshly-spawned process opens fine. Tearing down and rebuilding the
           PortAudio context (`_terminate`/`_initialize`) re-enumerates devices
           and lets us recover without an app restart.
        3. The device won't run at CAPTURE_RATE. Every Mac input we've seen does
           48 kHz natively, but a USB/Bluetooth oddity might not — rather than
           fail the dictation we fall back to SAMPLE_RATE (whisper is unaffected;
           only the dataset copy loses its extra band)."""
        last: Exception | None = None
        for attempt in range(3):
            rate = CAPTURE_RATE if attempt == 0 else SAMPLE_RATE
            blocksize = CAPTURE_CHUNK_SIZE if rate == CAPTURE_RATE else CHUNK_SIZE
            try:
                stream = sd.InputStream(
                    channels=CHANNELS,
                    samplerate=rate,
                    blocksize=blocksize,
                    dtype=np.float32,
                    device=_resolve_device(self._device),
                )
                stream.start()
                self._rate = rate
                if rate != CAPTURE_RATE:
                    print(f"⚠️ mic would not open at {CAPTURE_RATE} Hz — capturing at {rate} Hz")
                return stream
            except Exception as e:
                last = e
                print(f"⚠️ mic open failed (attempt {attempt + 1}, {rate} Hz): {e}")
                # Rebuild the PortAudio context in case it went stale (mode 2
                # above). Safe here: no stream is open at this point in the
                # recorder.
                with contextlib.suppress(Exception):
                    sd._terminate()
                    sd._initialize()
                time.sleep(0.3)
        raise last if last else RuntimeError("mic open failed")

    def _record(self):
        try:
            stream = self._open_stream()
        except Exception as e:
            print(f"‼️ Recording error: {e}")
            if self._on_error:
                with contextlib.suppress(Exception):
                    self._on_error(str(e))
            return

        read_size = CAPTURE_CHUNK_SIZE if self._rate == CAPTURE_RATE else CHUNK_SIZE
        try:
            while not self._stop_event.is_set():
                chunk, _ = stream.read(read_size)
                if chunk is None or chunk.size == 0:
                    continue
                mono = np.mean(chunk, axis=1)  # (frames, channels) → (frames,), stays float32
                # The dataset copy is taken here, before anything touches the
                # audio: full band, original level, exactly what the mic gave us.
                if self._dataset:
                    self._raw_frames.append(mono.tobytes())
                if self._rate != SAMPLE_RATE:
                    mono = self._downsampler.process(mono)
                    if mono.size == 0:
                        continue
                self._frames.append(mono.tobytes())
                if self._segmenter is not None:
                    seg = self._segmenter.feed(mono)
                    if seg is not None and self._on_segment is not None:
                        with contextlib.suppress(Exception):
                            seg_wav = pcm_to_wav(seg)
                            if seg_wav:
                                self._on_segment(seg_wav)
        except Exception as e:
            print(f"‼️ Recording error: {e}")
        finally:
            with contextlib.suppress(Exception):
                stream.stop()
                stream.close()

    def _to_wav(self) -> bytes | None:
        if not self._frames:
            return None
        return pcm_to_wav(b"".join(self._frames))
