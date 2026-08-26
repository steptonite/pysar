"""CoreAudio probes for the output side of the machine — via ctypes, no new deps.

Why this exists (26.08.2026, the GoIT webinar): ScreenCaptureKit is not the only
witness to what the speakers are doing, and it turned out to be an unreliable one.
Its system tap detached from the output device and went on delivering buffers of
exact digital zeros for 50 minutes while the machine was audibly playing a
webinar — SCK reported a healthy stream throughout. A watchdog that only ever
asks SCK how SCK is doing can never catch that. So we ask the audio system
directly, from outside the capture, and compare the two stories.

Everything here is best-effort: a probe that cannot answer returns None, and the
caller treats "don't know" as "no evidence", never as "broken". Losing the probe
must never be worse than not having it."""

from __future__ import annotations

import ctypes
import threading

_LOAD_LOCK = threading.Lock()
_ca: ctypes.CDLL | None = None
_load_failed = False

_FRAMEWORK = "/System/Library/Frameworks/CoreAudio.framework/CoreAudio"
_SYSTEM_OBJECT = 1  # kAudioObjectSystemObject


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


_SCOPE_GLOBAL = _fourcc("glob")  # kAudioObjectPropertyScopeGlobal
_DEFAULT_OUTPUT = _fourcc("dOut")  # kAudioHardwarePropertyDefaultOutputDevice
_RUNNING_SOMEWHERE = _fourcc("gone")  # kAudioDevicePropertyDeviceIsRunningSomewhere


class _Address(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _lib() -> ctypes.CDLL | None:
    global _ca, _load_failed
    if _ca is not None or _load_failed:
        return _ca
    with _LOAD_LOCK:
        if _ca is None and not _load_failed:
            try:
                _ca = ctypes.CDLL(_FRAMEWORK)
            except OSError:
                _load_failed = True
    return _ca


def _u32(obj: int, selector: int) -> int | None:
    """One UInt32 property, or None if CoreAudio declined to answer."""
    lib = _lib()
    if lib is None:
        return None
    addr = _Address(selector, _SCOPE_GLOBAL, 0)
    out = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(out))
    try:
        status = lib.AudioObjectGetPropertyData(
            ctypes.c_uint32(obj),
            ctypes.byref(addr),
            ctypes.c_uint32(0),
            None,
            ctypes.byref(size),
            ctypes.byref(out),
        )
    except Exception:  # pragma: no cover - defensive, ctypes should not raise here
        return None
    if status != 0 or size.value != ctypes.sizeof(out):
        return None
    return out.value


def default_output_device() -> int | None:
    """CoreAudio ID of the device sound is currently going to, or None.

    The ID changes when the user switches output — AirPods off, back to the
    built-in speakers — which is precisely the moment an already-running SCK tap
    is most likely to be left pointing at the device that just went away."""
    dev = _u32(_SYSTEM_OBJECT, _DEFAULT_OUTPUT)
    return dev or None  # device 0 is kAudioObjectUnknown, not an answer


def output_is_running(device: int | None = None) -> bool | None:
    """True when some process holds the output device's IO open, or None.

    ⚠️ This is "an audio stream is open", NOT "sound is actually coming out":
    measured 26.08.2026, the flag lags ~1.5 s behind the first sample and stays
    on for a while after the last one, and a conferencing app keeps it on for the
    whole call including every pause. So it is only ever useful as a weak
    corroborating witness — it can say "silence here is suspicious", never
    "silence here is a fault"."""
    dev = device if device is not None else default_output_device()
    if dev is None:
        return None
    val = _u32(dev, _RUNNING_SOMEWHERE)
    return None if val is None else bool(val)
