"""System-audio (+ microphone) capture via ScreenCaptureKit.

Powers the "transcribe everything" mode — meetings, calls, any audio playing on
the Mac — fully offline. It mirrors AudioRecorder's interface (``start(on_segment,
on_error)`` / ``stop()``) so the streaming pipeline (Segmenter → serialized
worker → whisper) in app.py is reused unchanged; only the *source* of audio
differs (this is the "separation of capture").

How it works:
  * One SCStream captures system audio (48 kHz, 2 ch, non-interleaved float32)
    and, on macOS 15+, the microphone (24 kHz, mono float32) in parallel. Both
    types are delivered on a single serial dispatch queue, so mixing needs no
    locking against itself.
  * Each delivered CMSampleBuffer is decoded to float32, downmixed to mono and
    resampled to 16 kHz. The two sources are summed sample-for-sample (rough
    wall-clock alignment by sample count, with a 1 s de-drift guard).
  * The mixed 16 kHz stream is re-blocked into fixed CHUNK_SIZE blocks and fed to
    the same Segmenter the mic path uses, so pause-based segmentation is identical.

Requires Screen Recording permission (granted to the Pysar app). The full clip is
*not* retained in memory — a meeting can run for hours on 8 GB, and the text is
autosaved to the transcript file instead.
"""

import contextlib
import threading
import time
from collections.abc import Callable

import numpy as np

from .config import (
    CHUNK_SIZE,
    MAX_SEG_SEC,
    MICRO_PAUSE_SEC,
    MIN_SEG_SEC,
    PAUSE_SEC,
    SAMPLE_RATE,
    SILENCE_MARGIN,
    SOFT_SEG_SEC,
)
from .recorder import pcm_to_wav
from .segmenter import Segmenter

# pyobjc frameworks are imported at module load but guarded: a machine missing a
# binding (or a non-macOS build) leaves AVAILABLE False and start() reports a
# clean error instead of crashing the app on import.
try:
    import CoreMedia as CM
    import libdispatch
    import objc
    import ScreenCaptureKit as SC
    from Foundation import NSObject

    AVAILABLE = True
except Exception:  # pragma: no cover - depends on the host
    AVAILABLE = False

# AudioStreamBasicDescription.mFormatFlags bits we care about.
_FLAG_IS_FLOAT = 1 << 0
_FLAG_NON_INTERLEAVED = 1 << 5


def _asbd(sbuf):
    """(sample_rate, channels, flags) from a buffer's ASBD. PyObjC returns the
    AudioStreamBasicDescription as a tuple in C struct field order:
    (mSampleRate, mFormatID, mFormatFlags, mBytesPerPacket, mFramesPerPacket,
     mBytesPerFrame, mChannelsPerFrame, mBitsPerChannel, mReserved)."""
    fmt = CM.CMSampleBufferGetFormatDescription(sbuf)
    a = CM.CMAudioFormatDescriptionGetStreamBasicDescription(fmt)
    if isinstance(a, (tuple, list)):
        return float(a[0]), int(a[6]), int(a[2])
    return float(a.mSampleRate), int(a.mChannelsPerFrame), int(a.mFormatFlags)


def _pcm_mono(sbuf):
    """CMSampleBuffer (LPCM float32) → (mono float32 ndarray, sample_rate).
    Handles interleaved and non-interleaved, mono or multi-channel."""
    sr, ch, flags = _asbd(sbuf)
    if not (flags & _FLAG_IS_FLOAT):
        return np.zeros(0, np.float32), sr  # we only deal with float LPCM here
    bb = CM.CMSampleBufferGetDataBuffer(sbuf)
    if bb is None:
        return np.zeros(0, np.float32), sr
    length = int(CM.CMBlockBufferGetDataLength(bb))
    status, data = CM.CMBlockBufferCopyDataBytes(bb, 0, length, None)
    if status != 0 or not data:
        return np.zeros(0, np.float32), sr
    arr = np.frombuffer(bytes(data), dtype=np.float32)
    if ch <= 1:
        return arr.copy(), sr
    if flags & _FLAG_NON_INTERLEAVED:  # planar: [c0 c0 …][c1 c1 …]
        per = arr.size // ch
        return arr[: per * ch].reshape(ch, per).mean(axis=0).astype(np.float32), sr
    frames = arr.size // ch  # interleaved: [f0c0 f0c1 …]
    return arr[: frames * ch].reshape(frames, ch).mean(axis=1).astype(np.float32), sr


def _to_16k(mono: np.ndarray, src_sr: int) -> np.ndarray:
    """Linear-interpolate a mono block to 16 kHz. Whisper is robust to linear
    resampling, and this keeps the module dependency-free (no scipy)."""
    if mono.size == 0 or src_sr == SAMPLE_RATE:
        return mono
    n_out = round(mono.size * SAMPLE_RATE / src_sr)
    if n_out <= 0:
        return np.zeros(0, np.float32)
    x_old = np.arange(mono.size, dtype=np.float64)
    x_new = np.linspace(0.0, mono.size - 1, n_out)
    return np.interp(x_new, x_old, mono).astype(np.float32)


if AVAILABLE:

    class _Output(NSObject):
        """SCStreamOutput + SCStreamDelegate. Forwards decoded buffers to the
        owning recorder; holds a plain Python ref (fine for a pyobjc object)."""

        def initWithOwner_(self, owner):
            self = objc.super(_Output, self).init()
            if self is None:
                return None
            self._owner = owner
            return self

        def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
            with contextlib.suppress(Exception):
                if not CM.CMSampleBufferIsValid(sbuf):
                    return
                if kind == SC.SCStreamOutputTypeAudio:
                    self._owner._ingest(0, sbuf)
                elif kind == SC.SCStreamOutputTypeMicrophone:
                    self._owner._ingest(1, sbuf)

        def stream_didStopWithError_(self, stream, error):
            with contextlib.suppress(Exception):
                self._owner._on_stream_stop(error)


class SystemAudioRecorder:
    """Drop-in capture source for the streaming pipeline, sourcing system audio
    (+ mic) instead of the microphone alone."""

    # If one source runs more than this far ahead of the other, zero-pad the
    # laggard to resync — guards against drift or a momentarily starved source.
    _MAX_DRIFT = SAMPLE_RATE  # 1 second

    def __init__(self, capture_mic: bool = True):
        self._capture_mic = capture_mic
        self._on_segment: Callable[[bytes], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._segmenter: Segmenter | None = None

        self._sys = np.zeros(0, np.float32)
        self._mic = np.zeros(0, np.float32)
        self._block_acc = np.zeros(0, np.float32)
        self._lock = threading.Lock()

        self._stream = None
        self._output = None
        self._queue = None
        self._started_at = 0.0
        self._stopped = threading.Event()

    def set_capture_mic(self, on: bool) -> None:
        """Takes effect on the next start()."""
        self._capture_mic = on

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(
        self,
        on_segment: Callable[[bytes], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_segment = on_segment
        self._on_error = on_error
        if not AVAILABLE:
            self._fail("ScreenCaptureKit is unavailable on this system")
            return

        self._sys = np.zeros(0, np.float32)
        self._mic = np.zeros(0, np.float32)
        self._block_acc = np.zeros(0, np.float32)
        self._started_at = time.time()
        self._stopped.clear()
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
        # SCShareableContent.getShareable…Handler runs its completion on the main
        # queue; the app's run loop (rumps) drives it, so just kick it off here.
        with contextlib.suppress(Exception):
            SC.SCShareableContent.getShareableContentWithCompletionHandler_(self._on_content)

    def stop(self) -> None:
        """Stop capture and flush the trailing segment. Returns nothing — the full
        clip is intentionally not retained (long meetings, 8 GB).

        Safe to call before the async setup finished: the stop flag is set FIRST so
        a still-pending `_on_content` bails out instead of starting a stream nobody
        holds a reference to — which was leaving the mic open (AirPods dropped to
        hands-free) until a reboot."""
        self._stopped.set()  # FIRST — closes the start-after-stop race
        stream, self._stream = self._stream, None
        if stream is not None:
            done = threading.Event()
            with contextlib.suppress(Exception):
                stream.stopCaptureWithCompletionHandler_(lambda e: done.set())
            done.wait(timeout=3)
        self._output = None
        self._queue = None
        # Flush the last partial segment so the meeting's final sentence isn't lost.
        if self._segmenter is not None and self._on_segment is not None:
            with contextlib.suppress(Exception):
                tail = self._segmenter.flush()
                if tail:
                    wav = pcm_to_wav(tail)
                    if wav:
                        self._on_segment(wav)
        self._stopped.set()

    # ── internals ─────────────────────────────────────────────────────────────
    def _fail(self, msg: str) -> None:
        if self._on_error:
            with contextlib.suppress(Exception):
                self._on_error(msg)

    def _on_content(self, content, error) -> None:
        if self._stopped.is_set():
            return  # stop() landed before setup ran — never open the stream/mic
        if content is None:
            self._fail("Screen Recording permission needed (grant it to Pysar)")
            return
        try:
            displays = content.displays()
            if not displays:
                self._fail("no display available to attach the audio stream")
                return
            cfg = SC.SCStreamConfiguration.alloc().init()
            cfg.setCapturesAudio_(True)
            cfg.setExcludesCurrentProcessAudio_(True)  # never capture Pysar's own output
            cfg.setCaptureMicrophone_(bool(self._capture_mic))
            cfg.setWidth_(2)  # minimal video config; we attach no screen output
            cfg.setHeight_(2)

            filt = SC.SCContentFilter.alloc().initWithDisplay_excludingWindows_(displays[0], [])
            self._output = _Output.alloc().initWithOwner_(self)
            self._stream = SC.SCStream.alloc().initWithFilter_configuration_delegate_(
                filt, cfg, self._output
            )
            self._queue = libdispatch.dispatch_queue_create(b"com.steptonite.pysar.sck", None)

            kinds = [SC.SCStreamOutputTypeAudio]
            if self._capture_mic:
                kinds.append(SC.SCStreamOutputTypeMicrophone)
            for kind in kinds:
                ok, err = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                    self._output, kind, self._queue, None
                )
                if not ok:
                    self._fail(f"could not attach audio output: {err}")
                    return

            # If stop() raced in while we were building, tear down now rather than
            # starting a stream nobody holds a reference to (it would keep the mic).
            if self._stopped.is_set():
                self._stream = self._output = self._queue = None
                return

            def started(err) -> None:
                if err is not None:
                    self._fail(f"capture failed to start: {err}")
                    return
                if self._stopped.is_set():
                    # stop() raced in during the async start — release immediately.
                    s, self._stream = self._stream, None
                    if s is not None:
                        with contextlib.suppress(Exception):
                            s.stopCaptureWithCompletionHandler_(lambda e: None)

            self._stream.startCaptureWithCompletionHandler_(started)
        except Exception as e:  # pragma: no cover - defensive
            self._fail(f"system capture setup failed: {e}")

    def _on_stream_stop(self, error) -> None:
        # SCK stopped on its own (display reconfigured, permission revoked, …).
        if error is not None and not self._stopped.is_set():
            self._fail(f"capture stopped: {error}")

    def _ingest(self, source: int, sbuf) -> None:
        """Decode one buffer (source 0 = system, 1 = mic), resample to 16 kHz and
        push into the mixer. Runs on the SCK serial queue."""
        mono, sr = _pcm_mono(sbuf)
        if mono.size == 0:
            return
        x = _to_16k(mono, int(sr))
        with self._lock:
            if source == 0:
                self._sys = np.concatenate((self._sys, x))
            else:
                self._mic = np.concatenate((self._mic, x))
            mixed = self._mix_locked()
            if mixed.size:
                self._feed_blocks_locked(mixed)

    def _mix_locked(self) -> np.ndarray:
        """Return the next run of mixed samples that both sources have covered,
        consuming them from the per-source buffers."""
        if not self._capture_mic:
            out, self._sys = self._sys, np.zeros(0, np.float32)
            return out

        # Resync if one source drifted far ahead (or the other stalled): pad the
        # laggard with silence so the leader can be released.
        if self._sys.size > self._mic.size + self._MAX_DRIFT:
            pad = self._sys.size - self._mic.size
            self._mic = np.concatenate((self._mic, np.zeros(pad, np.float32)))
        elif self._mic.size > self._sys.size + self._MAX_DRIFT:
            pad = self._mic.size - self._sys.size
            self._sys = np.concatenate((self._sys, np.zeros(pad, np.float32)))

        n = min(self._sys.size, self._mic.size)
        if n == 0:
            return np.zeros(0, np.float32)
        out = self._sys[:n] + self._mic[:n]
        self._sys = self._sys[n:]
        self._mic = self._mic[n:]
        return out

    def _feed_blocks_locked(self, mixed: np.ndarray) -> None:
        """Re-block the mixed stream into fixed CHUNK_SIZE blocks (the Segmenter
        times segments by block count) and feed it."""
        self._block_acc = np.concatenate((self._block_acc, mixed))
        while self._block_acc.size >= CHUNK_SIZE:
            block = self._block_acc[:CHUNK_SIZE]
            self._block_acc = self._block_acc[CHUNK_SIZE:]
            if self._segmenter is None or self._on_segment is None:
                continue
            seg = self._segmenter.feed(block)
            if seg is not None:
                with contextlib.suppress(Exception):
                    wav = pcm_to_wav(seg)
                    if wav:
                        self._on_segment(wav)
