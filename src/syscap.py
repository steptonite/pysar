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
    resampled to 16 kHz.
  * In "off" and "fast" modes the two sources are summed sample-for-sample (rough
    wall-clock alignment by sample count, with a 1 s de-drift guard). In "smart"
    mode no mixing happens — system and mic are kept entirely separate, each
    feeding its own Segmenter.
  * The 16 kHz stream(s) are re-blocked into fixed CHUNK_SIZE blocks and fed to
    the Segmenter(s) so pause-based segmentation is identical.

Requires Screen Recording permission (granted to the Pysar app). The full clip is
*not* retained in memory — a meeting can run for hours on 8 GB, and the text is
autosaved to the transcript file instead.
"""

import contextlib
import threading
import time
import wave
from collections.abc import Callable
from pathlib import Path

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
from .micvpio import VoiceProcessingMic
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


def displays_present() -> bool:
    """Чи існує зараз бодай один активний екран.

    🔴 12.09.2026, заміряно на Маку Льоші: закрита кришка валить захоплення з
    `SCStreamErrorDomain Code=-3815` («не вдалося знайти екран чи вікно») — бо
    ScreenCaptureKit тягне СИСТЕМНИЙ звук через дисплей, і без дисплея йому
    нема до чого чіплятись. Це не поломка Писаря і не лікується ретраєм: поки
    екрана нема, кожна спроба впаде миттєво.

    Невідомість трактуємо як «є»: якщо Quartz недоступний, вигадувати блокер на
    порожньому місці гірше, ніж спробувати підняти потік і побачити чесну
    помилку."""
    try:
        from Quartz import CGGetActiveDisplayList

        err, _ids, count = CGGetActiveDisplayList(16, None, None)
        if err:
            return True
        return int(count) > 0
    except Exception:
        return True


def mic_pinning_supported() -> bool:
    """Whether SCK can be told WHICH microphone to capture (macOS 15+).

    Without the selector the stream silently uses the system default input, so
    the device picked in the menu is a promise the capture cannot keep — worth
    saying out loud rather than discovering it in the finished recording.
    """
    if not AVAILABLE:
        return False
    with contextlib.suppress(Exception):
        cfg = SC.SCStreamConfiguration.alloc().init()
        return hasattr(cfg, "setMicrophoneCaptureDeviceID_")
    return False


# s16le: сегментер віддає СИРІ БАЙТИ, не numpy — два байти на семпл.
_BYTES_PER_SAMPLE = 2


class _RawDump:
    """Append-only 16 kHz WAV written straight off the capture thread.

    The point is recovery, not quality: a meeting or a phone call happens ONCE.
    Before 24.08.2026 audio lived only in memory ("WAV is kept in memory, never
    written to disk") — so when transcription produced nothing, nothing at all
    remained. Now the raw stream hits the disk from the first buffer, and stays
    there whatever the rest of the pipeline does.

    One file per source (sys / mic): they arrive at different rates and a lost
    sync would make a recovered file useless. Opened lazily — a source that never
    delivers a buffer leaves no file, and that absence is itself the diagnosis.
    """

    def __init__(self, path: Path):
        self._path = path
        self._wav: wave.Wave_write | None = None
        self._frames = 0
        # 26.08.2026: length alone lied. The webinar dump ran the full 98 minutes
        # and half of it was digital zeros, so "💾 raw audio kept (5871s)" read
        # like a success. What matters is not HOW MUCH silence a file holds — a
        # call where the far side rarely talks is mostly silence and is fine —
        # but whether it holds one UNBROKEN block of it. That is the shape a dead
        # tap leaves, and ordinary quiet does not.
        self._silent_run = 0
        self._max_silent_run = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def frames(self) -> int:
        return self._frames

    @property
    def max_silent_run(self) -> int:
        """Longest unbroken run of digital-zero frames, in frames."""
        return max(self._max_silent_run, self._silent_run)

    def write(self, x: np.ndarray) -> None:
        if x.size == 0:
            return
        try:
            if self._wav is None:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                # Held open for the whole capture on purpose: a context manager
                # would close it after one buffer, and the point is a continuous file.
                w = wave.open(str(self._path), "wb")  # noqa: SIM115
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                self._wav = w
            pcm = np.clip(x, -1.0, 1.0)
            self._wav.writeframes((pcm * 32767.0).astype("<i2").tobytes())
            self._frames += int(x.size)
            # Runs are tracked ACROSS buffer boundaries: a dead tap does not
            # respect them, and a per-buffer tally would see 50 minutes of
            # silence as thousands of harmless little ones.
            nz = np.flatnonzero(x)
            if nz.size == 0:
                self._silent_run += int(x.size)
            else:
                head = self._silent_run + int(nz[0])
                gap = int(np.diff(nz).max()) - 1 if nz.size > 1 else 0
                self._max_silent_run = max(self._max_silent_run, head, gap)
                self._silent_run = int(x.size - nz[-1] - 1)
        except Exception as e:  # never let the dump kill the capture
            print(f"⚠️ raw dump write failed ({self._path.name}): {e}")
            self._wav = None

    def close(self) -> None:
        w, self._wav = self._wav, None
        if w is not None:
            with contextlib.suppress(Exception):
                w.close()


class SystemAudioRecorder:
    """Drop-in capture source for the streaming pipeline, sourcing system audio
    (+ mic) instead of the microphone alone."""

    # If one source runs more than this far ahead of the other, zero-pad the
    # laggard to resync — guards against drift or a momentarily starved source.
    _MAX_DRIFT = SAMPLE_RATE  # 1 second

    def __init__(
        self,
        capture_mic: bool = True,
        source_mode: str = "off",
        raw_dump_dir: "Path | str | None" = None,
        raw_dump_stem: str = "",
        mic_device_uid: str | None = None,
        mic_aec: bool = False,
    ):
        self._capture_mic = capture_mic
        # 🔴 12.09.2026. Мікрофон через VPIO замість SCK: апаратний AEC знімає
        # ехо динаміків ДО віспера (заміряно −22 dB). Системна доріжка лишається
        # на ScreenCaptureKit — VPIO її лише приглушує, і те лікується.
        self._mic_aec = bool(mic_aec)
        self._vpio: VoiceProcessingMic | None = None
        # Чи мікрофон цієї сесії справді пішов через VPIO. Ставиться в start():
        # якщо AEC не піднявся, вертаємось на мік SCK — без мікрофона зустріч
        # гірша, ніж із ехом, — але кажемо про це вголос, а не тихо.
        self._mic_from_vpio = False
        # Raw recovery buffer (see _RawDump) and the mic SCK must capture from.
        self._raw_dump_dir = Path(raw_dump_dir) if raw_dump_dir else None
        self._raw_dump_stem = raw_dump_stem
        self._mic_device_uid = mic_device_uid
        self._dump_sys: _RawDump | None = None
        self._dump_mic: _RawDump | None = None
        # (paths, seconds) of the last finished capture — survives _close_dumps.
        self._dump_final: tuple[list[Path], float] = ([], 0.0)
        self._dump_silence_final = 0.0  # longest unbroken silence in the system dump, s
        self._source_mode = source_mode if source_mode in ("off", "fast", "smart") else "off"
        self._on_segment: Callable[[bytes, str | None, tuple[float, float] | None], None] | None = (
            None
        )
        self._on_error: Callable[[str], None] | None = None

        # Segmenters
        self._segmenter: Segmenter | None = None
        self._seg_sys: Segmenter | None = None
        self._seg_mic: Segmenter | None = None

        # Mixed‑path buffers
        self._sys = np.zeros(0, np.float32)
        self._mic = np.zeros(0, np.float32)
        self._block_acc = np.zeros(0, np.float32)

        # Smart‑path accumulators
        self._acc_sys = np.zeros(0, np.float32)
        self._acc_mic = np.zeros(0, np.float32)

        # Fast‑mode energy accumulators
        self._e_sys = 0.0
        self._e_mic = 0.0

        self._lock = threading.Lock()
        self._stream = None
        self._output = None
        self._queue = None
        self._started_at = 0.0
        self._stopped = threading.Event()
        # Liveness heartbeat: monotonic ts of the most recent valid audio buffer.
        # A watchdog reads it to detect a silent SCK stall (mute/sleep/reconfigure
        # that never fires didStop — regression 23.07.2026).
        self._last_audio_monotonic = 0.0
        # 26.08.2026 — arrival is NOT liveness. During the GoIT webinar the system
        # tap detached on a foreground-app switch and kept delivering buffers of
        # exact digital zeros for 50 minutes: _last_audio_monotonic stayed fresh,
        # the watchdog never fired, and the raw dump — the last line of defence —
        # recorded the silence too. So track sound, not delivery.
        self._first_buffer_monotonic = 0.0
        self._last_sound_monotonic = 0.0

    def heard_sound(self) -> bool:
        """True once a system buffer has carried a non-zero sample. Lets a caller
        tell "this tap has been working" from "this tap has never said anything",
        which is what a mute-recovery backoff needs to reset honestly."""
        return self._last_sound_monotonic != 0.0

    def seconds_since_sound(self) -> float | None:
        """Seconds since the system tap last carried a non-zero sample (measured
        from the first buffer while it has never carried one), or None when the
        capture is stopped or no buffer has arrived yet.

        A detached tap is indistinguishable from a genuinely quiet machine — both
        yield exact zeros — so this is a suspicion, not a verdict. The caller acts
        on it because acting is cheap: restarting the stream during real silence
        costs a sub-second gap in a stretch that carries nothing."""
        if self._stopped.is_set() or self._first_buffer_monotonic == 0.0:
            return None
        base = self._last_sound_monotonic or self._first_buffer_monotonic
        return time.monotonic() - base

    def seconds_since_audio(self) -> float | None:
        """Seconds since the last valid audio buffer, or None if the capture has
        not delivered a buffer yet or has been stopped. A value above a sane
        threshold (≈8 s) means the SCK stream is likely stalled while SCK still
        believes it is running. Reads a primitive + Event under the GIL."""
        if self._stopped.is_set() or self._last_audio_monotonic == 0.0:
            return None
        return time.monotonic() - self._last_audio_monotonic

    def dump_paths(self) -> "list[Path]":
        """Recovery files this capture actually wrote to (non-empty ones only).

        Readable after stop() as well: the caller that reports the outcome runs
        once the capture is already torn down, so the tally outlives the dumps."""
        if self._dump_sys is None and self._dump_mic is None:
            return list(self._dump_final[0])
        return [d.path for d in (self._dump_mic, self._dump_sys) if d and d.frames > 0]

    def dump_seconds(self) -> float:
        """Longest recovery file, in seconds — 0.0 when nothing was ever captured.
        A meeting that ends with 0.0 here had no audio at all, and that is a
        failure the app must say out loud, not a quiet empty transcript."""
        if self._dump_sys is None and self._dump_mic is None:
            return self._dump_final[1]
        best = max((d.frames for d in (self._dump_mic, self._dump_sys) if d), default=0)
        return best / float(SAMPLE_RATE)

    def dump_silent_run_seconds(self) -> float:
        """Longest unbroken stretch of digital silence in the SYSTEM recovery
        file, in seconds.

        The number the caller needs when a meeting ends: a dump can run the
        full length of the call and still hold nothing, which is what happened
        on 26.08.2026. Deliberately the longest RUN and not the total — an
        hour-long call where the far side rarely speaks is mostly silence and
        perfectly healthy, while one unbroken block is a tap that died.
        """
        if self._dump_sys is not None:
            return self._dump_sys.max_silent_run / float(SAMPLE_RATE)
        return self._dump_silence_final

    def _close_dumps(self) -> None:
        paths = [d.path for d in (self._dump_mic, self._dump_sys) if d and d.frames > 0]
        best = max((d.frames for d in (self._dump_mic, self._dump_sys) if d), default=0)
        if paths or best:
            # Keep the tally: stop() closes the dumps, and the caller asks after.
            self._dump_final = (paths, best / float(SAMPLE_RATE))
            if self._dump_sys is not None:
                self._dump_silence_final = self._dump_sys.max_silent_run / float(SAMPLE_RATE)
        for d in (self._dump_sys, self._dump_mic):
            if d is not None:
                d.close()
        self._dump_sys = self._dump_mic = None

    def set_capture_mic(self, on: bool) -> None:
        """Takes effect on the next start()."""
        self._capture_mic = on

    def set_source_mode(self, mode: str) -> None:
        """Source separation mode — takes effect on the next start()."""
        if mode in ("off", "fast", "smart"):
            self._source_mode = mode

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(
        self,
        on_segment: Callable[[bytes, str | None, tuple[float, float] | None], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._on_segment = on_segment
        self._on_error = on_error
        if not AVAILABLE:
            self._fail("ScreenCaptureKit is unavailable on this system")
            return

        # Reset all buffers
        self._sys = np.zeros(0, np.float32)
        self._mic = np.zeros(0, np.float32)
        self._block_acc = np.zeros(0, np.float32)
        self._acc_sys = np.zeros(0, np.float32)
        self._acc_mic = np.zeros(0, np.float32)
        self._e_sys = 0.0
        self._e_mic = 0.0
        self._started_at = time.time()
        self._stopped.clear()
        # Reset heartbeat so a reused recorder reports None (not a stale gap) until
        # its first fresh buffer — otherwise the watchdog could fire a spurious
        # recover in the first seconds of the next meeting.
        self._last_audio_monotonic = 0.0

        # Arm the recovery buffer. A recover-restart mid-meeting gets its own
        # suffixed pair rather than truncating what the dead stream already saved.
        self._close_dumps()
        self._dump_final = ([], 0.0)
        if self._raw_dump_dir is not None:
            stem = self._raw_dump_stem or time.strftime("%Y-%m-%d_%H-%M-%S")
            n, base = 1, self._raw_dump_dir / stem
            while (base.with_name(f"{base.name}-mic.wav")).exists() or (
                base.with_name(f"{base.name}-sys.wav")
            ).exists():
                n += 1
                base = self._raw_dump_dir / f"{stem}-{n}"
            self._dump_sys = _RawDump(base.with_name(f"{base.name}-sys.wav"))
            self._dump_mic = _RawDump(base.with_name(f"{base.name}-mic.wav"))

        segmenter_kw = dict(
            sample_rate=SAMPLE_RATE,
            block_size=CHUNK_SIZE,
            pause_sec=PAUSE_SEC,
            min_seg_sec=MIN_SEG_SEC,
            max_seg_sec=MAX_SEG_SEC,
            silence_margin=SILENCE_MARGIN,
            soft_seg_sec=SOFT_SEG_SEC,
            micro_pause_sec=MICRO_PAUSE_SEC,
        )

        if on_segment is not None:
            if self._source_mode == "smart":
                self._seg_sys = Segmenter(**segmenter_kw)
                self._seg_mic = Segmenter(**segmenter_kw)
                self._segmenter = None
            else:
                self._segmenter = Segmenter(**segmenter_kw)
                self._seg_sys = None
                self._seg_mic = None
        else:
            self._segmenter = None
            self._seg_sys = None
            self._seg_mic = None

        # Мікрофон з AEC підіймаємо ТУТ, до SCK: у _on_content уже треба знати,
        # просити в ScreenCaptureKit мікрофон чи ні.
        self._mic_from_vpio = False
        if self._vpio is not None:
            with contextlib.suppress(Exception):
                self._vpio.stop()
            self._vpio = None
        if self._capture_mic and self._mic_aec:
            vpio = VoiceProcessingMic(
                on_block=lambda mono, sr: self._ingest_pcm(1, mono, sr, heartbeat=False),
            )
            err = vpio.start()
            if err is None:
                self._vpio = vpio
                self._mic_from_vpio = True
                print(f"🎧 мік з апаратним AEC, каналів {vpio.channels}")
            else:
                # Чесна відмова: мік лишається, але з ехом — і про це кажемо.
                self._fail(f"ехо не ріжеться ({err}) — мікрофон пише як раніше")

        # SCShareableContent.getShareable…Handler runs its completion on the main
        # queue; the app's run loop (rumps) drives it, so just kick it off here.
        with contextlib.suppress(Exception):
            SC.SCShareableContent.getShareableContentWithCompletionHandler_(self._on_content)

    def stop(self) -> None:
        """Stop capture and flush the trailing segment(s). Returns nothing — the
        full clip is intentionally not retained (long meetings, 8 GB).

        Safe to call before the async setup finished: the stop flag is set FIRST so
        a still-pending `_on_content` bails out instead of starting a stream nobody
        holds a reference to — which was leaving the mic open (AirPods dropped to
        hands-free) until a reboot."""
        self._stopped.set()  # FIRST — closes the start-after-stop race
        vpio, self._vpio = self._vpio, None
        if vpio is not None:
            with contextlib.suppress(Exception):
                vpio.stop()
        stream, self._stream = self._stream, None
        if stream is not None:
            done = threading.Event()
            with contextlib.suppress(Exception):
                stream.stopCaptureWithCompletionHandler_(lambda e: done.set())
            done.wait(timeout=3)
        self._output = None
        self._queue = None
        self._close_dumps()

        # Flush the trailing segment(s) so the meeting's final sentence isn't lost.
        if self._on_segment is not None:
            if self._source_mode == "smart":
                for seg, src in ((self._seg_sys, "sys"), (self._seg_mic, "mic")):
                    if seg is None:
                        continue
                    with contextlib.suppress(Exception):
                        tail = seg.flush()
                        if tail:
                            wav = pcm_to_wav(tail)
                            if wav:
                                self._on_segment(wav, src, self._span(src, tail))
            else:
                if self._segmenter is not None:
                    with contextlib.suppress(Exception):
                        tail = self._segmenter.flush()
                        if tail:
                            wav = pcm_to_wav(tail)
                            if wav:
                                if self._source_mode == "fast":
                                    src = "sys" if self._e_sys >= self._e_mic else "mic"
                                    self._e_sys = 0.0
                                    self._e_mic = 0.0
                                else:
                                    src = None
                                self._on_segment(wav, src, self._span(src, tail))
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
            cfg.setCaptureMicrophone_(bool(self._capture_mic and not self._mic_from_vpio))
            # 24.08.2026 — the mic chosen in the menu never reached this stream:
            # SCK silently used the system default, so picking "MacBook Air mic"
            # to dodge a dead AirPods link changed nothing. macOS 15 exposes the
            # device explicitly; older systems keep the old (default) behaviour.
            if self._capture_mic and not self._mic_from_vpio and self._mic_device_uid:
                if hasattr(cfg, "setMicrophoneCaptureDeviceID_"):
                    with contextlib.suppress(Exception):
                        cfg.setMicrophoneCaptureDeviceID_(self._mic_device_uid)
                else:
                    print("⚠️ mic device pinning unsupported (needs macOS 15+)")
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
        # SCK stopped on its own (display reconfigured, permission revoked,
        # system sleep, …). A silent stop carries no error object but is just
        # as dead — without reporting it the owner keeps believing the capture
        # is live forever (stuck "stop transcription" state, stress test
        # 08.07.2026), so any stop we didn't request goes through _fail().
        if not self._stopped.is_set():
            self._fail(
                f"capture stopped: {error}" if error is not None else "capture stopped by macOS"
            )

    def _ingest(self, source: int, sbuf) -> None:
        """Decode one buffer (source 0 = system, 1 = mic), resample to 16 kHz and
        push into the mixer or the appropriate smart-path accumulator. Runs on the
        SCK serial queue."""
        mono, sr = _pcm_mono(sbuf)
        if mono.size == 0:
            return
        self._ingest_pcm(source, mono, int(sr))

    def _ingest_pcm(self, source: int, mono: np.ndarray, sr: int, heartbeat: bool = True) -> None:
        """Спільний шлях для обох джерел мікрофона (SCK і VPIO) та системи.

        🔴 12.09.2026. `heartbeat=False` для мікрофона з VPIO — і це не деталь.
        Доки мік їхав тим самим потоком SCK, його буфери ДОКАЗУВАЛИ, що потік
        живий. Тепер мік — окремий рушій: якщо він стукає в серце, сторож
        ніколи не побачить, що системне захоплення вмерло (SCStreamError
        -3817 у Каті), і зустріч дописуватиметься без звуку співрозмовника."""
        # Heartbeat: a delivered buffer proves the stream is alive. Ambient mic
        # data keeps flowing even in silence — only a dead stream yields zero
        # buffers, so the watchdog can tell a stall from a legitimate pause.
        if heartbeat:
            self._last_audio_monotonic = time.monotonic()
        if source == 0:
            # Sound heartbeat, system source only: the mic floor is never exactly
            # zero, so mic buffers would mask a dead system tap (bug 26.08.2026).
            if self._first_buffer_monotonic == 0.0:
                self._first_buffer_monotonic = self._last_audio_monotonic
            if mono.any():
                self._last_sound_monotonic = self._last_audio_monotonic
        x = _to_16k(mono, sr)
        # Recovery buffer first: it must survive even if everything downstream
        # (segmenter, whisper, transcript file) fails.
        dump = self._dump_sys if source == 0 else self._dump_mic
        if dump is not None:
            dump.write(x)
        with self._lock:
            if self._source_mode == "smart":
                if source == 0:
                    self._feed_source_locked(self._seg_sys, "_acc_sys", "sys", x)
                else:
                    self._feed_source_locked(self._seg_mic, "_acc_mic", "mic", x)
                return
            # off / fast mixed path
            if source == 0:
                self._sys = np.concatenate((self._sys, x))
            else:
                self._mic = np.concatenate((self._mic, x))
            mixed = self._mix_locked()
            if mixed.size:
                self._feed_blocks_locked(mixed)

    def _feed_source_locked(self, seg, acc_attr: str, source: str, x: np.ndarray) -> None:
        """Append *x* to the accumulator named *acc_attr*, re-block into
        CHUNK_SIZE blocks, feed *seg*, and emit each complete segment with the
        given *source* tag."""
        arr = getattr(self, acc_attr)
        arr = np.concatenate((arr, x))
        # Process complete blocks
        while arr.size >= CHUNK_SIZE:
            block = arr[:CHUNK_SIZE]
            arr = arr[CHUNK_SIZE:]
            if seg is not None and self._on_segment is not None:
                seg_res = seg.feed(block)
                if seg_res is not None:
                    with contextlib.suppress(Exception):
                        wav = pcm_to_wav(seg_res)
                        if wav:
                            self._on_segment(wav, source, self._span(source, seg_res))
        setattr(self, acc_attr, arr)

    # ── Позиція сегмента в записі (фіча «мітки секунд», 06.09.2026) ──────────
    # Час беремо з КІЛЬКОСТІ ЗАПИСАНИХ СЕМПЛІВ у сирому дампі, не з годинника:
    # годинник пливе на затримку черги транскрибації (сегмент розшифровується
    # через секунди після того, як прозвучав), а лічильник семплів — ні. Саме ці
    # межі потім дозволяють розділити спікерів БЕЗ перерозшифровки.
    def _seg_for(self, source: str | None):
        """Який саме сегментер віддав цей сегмент: у «розумному» режимі їх двоє,
        по одному на джерело, в інших — один спільний."""
        # getattr, а не пряме звернення: цей метод кличеться з `_span`, який НЕ
        # має права впасти — його виключення знищило б не мітку, а весь сегмент.
        if getattr(self, "_source_mode", None) == "smart":
            return {
                "sys": getattr(self, "_seg_sys", None),
                "mic": getattr(self, "_seg_mic", None),
            }.get(source)
        return getattr(self, "_segmenter", None)

    def _span(self, source: str | None, data) -> tuple[float, float] | None:
        """(t0, t1) у секундах від початку захоплення, або None якщо міток нема.

        🔴 НІКОЛИ НЕ КИДАЄ. Виклик стоїть усередині `suppress(Exception)`, який
        обгортає САМ `_on_segment`, тож будь-яке виключення звідси знищувало б
        не мітку, а ВЕСЬ сегмент — мовчки. Саме це сталось 06.09.2026: сегментер
        віддає `bytes`, а тут стояло `.size` (атрибут numpy) ⇒ AttributeError на
        КОЖНОМУ сегменті ⇒ 18 реплік запису перетворились на порожній транскрипт
        при повних 7 МБ сирого аудіо. Мітка часу — прикраса; звук — ні.
        """
        try:
            # 🔴 Спершу питаємо САМ сегментер, де стояв цей сегмент у потоці.
            # Рахунок по дампу (нижче) бреше: між кінцем фрази і видачею сегмента
            # у дамп устигає натекти ще звук, а сегментер до того ж зрізає тишу
            # на початку. У записі 06.09.2026 через це сусідні мітки лізли одна
            # на одну на 10-20 с, і розділення голосів чесно ставило одного
            # «Спікера 1» на відрізок, де говорили троє. Дамп лишаємо запасним
            # шляхом — краще приблизна мітка, ніж жодної.
            seg = self._seg_for(source)
            span = getattr(seg, "last_span_samples", None) if seg is not None else None
            if span:
                return (
                    round(span[0] / float(SAMPLE_RATE), 2),
                    round(span[1] / float(SAMPLE_RATE), 2),
                )
            if isinstance(data, int):  # уже полічені семпли
                n_samples = data
            elif hasattr(data, "size"):  # numpy
                n_samples = data.size
            else:  # сирі байти s16le — саме це віддає сегментер
                n_samples = len(data) // _BYTES_PER_SAMPLE
            dumps = {"sys": self._dump_sys, "mic": self._dump_mic}
            d = dumps.get(source)
            frames = (
                d.frames
                if d is not None
                else max(
                    (x.frames for x in (self._dump_sys, self._dump_mic) if x is not None), default=0
                )
            )
            if not frames:
                return None
            t1 = frames / float(SAMPLE_RATE)
            t0 = max(t1 - n_samples / float(SAMPLE_RATE), 0.0)
            return (round(t0, 2), round(t1, 2))
        except Exception:
            return None

    def _mix_locked(self) -> np.ndarray:
        """Return the next run of mixed samples that both sources have covered,
        consuming them from the per-source buffers. In "fast" mode also
        accumulates per-source energy."""
        if not self._capture_mic:
            out, self._sys = self._sys, np.zeros(0, np.float32)
            if self._source_mode == "fast":
                self._e_sys += float(np.dot(out, out))
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
        if self._source_mode == "fast":
            self._e_sys += float(np.dot(self._sys[:n], self._sys[:n]))
            self._e_mic += float(np.dot(self._mic[:n], self._mic[:n]))
        out = self._sys[:n] + self._mic[:n]
        self._sys = self._sys[n:]
        self._mic = self._mic[n:]
        return out

    def _feed_blocks_locked(self, mixed: np.ndarray) -> None:
        """Re-block the mixed stream into fixed CHUNK_SIZE blocks (the Segmenter
        times segments by block count) and feed it. In "fast" mode tags each
        emitted segment with the louder source."""
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
                        if self._source_mode == "fast":
                            src = "sys" if self._e_sys >= self._e_mic else "mic"
                        else:
                            src = None
                        self._on_segment(wav, src, self._span(src, seg))
                        if self._source_mode == "fast":
                            self._e_sys = 0.0
                            self._e_mic = 0.0
