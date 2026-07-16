"""Proactive TCC permission requests (macOS).

macOS never raises the Input Monitoring dialog from CGEventTapCreate: a
listen-only keyboard tap is either created dead or returns NULL when the
permission is missing — or *stale*, e.g. after `make app` re-signs the bundle
and the old grant row no longer matches the new signature. The only call that
shows the system prompt is IOHIDRequestAccess, so the app asks explicitly at
startup. Same story for Accessibility (the paste path): the prompt appears
only via AXIsProcessTrustedWithOptions. Microphone and Screen Recording are
different — those services do prompt on first use, so they need no help here.
"""

import ctypes

# IOHIDAccessType values returned by IOHIDCheckAccess.
GRANTED = 0
DENIED = 1
UNKNOWN = 2

_LISTEN_EVENT = 1  # kIOHIDRequestTypeListenEvent

_lib = None


def _iokit():
    global _lib
    if _lib is None:
        lib = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
        lib.IOHIDCheckAccess.restype = ctypes.c_int
        lib.IOHIDCheckAccess.argtypes = [ctypes.c_int]
        lib.IOHIDRequestAccess.restype = ctypes.c_bool
        lib.IOHIDRequestAccess.argtypes = [ctypes.c_int]
        _lib = lib
    return _lib


def input_monitoring_status() -> int:
    """GRANTED / DENIED / UNKNOWN for keyboard listen access (Input Monitoring).

    UNKNOWN means the user was never asked — request_input_monitoring() will
    show the system dialog. Any failure reads as DENIED: the caller then warns
    and waits, which is the safe side.
    """
    try:
        return _iokit().IOHIDCheckAccess(_LISTEN_EVENT)
    except Exception:
        return DENIED


def request_input_monitoring() -> bool:
    """Show the Input Monitoring system prompt (no-op if already decided).

    Returns the *current* verdict; the dialog itself is asynchronous, so a
    False here just means "not granted yet" — the hotkey listener keeps
    polling input_monitoring_status() and attaches once the user flips it.
    """
    try:
        return bool(_iokit().IOHIDRequestAccess(_LISTEN_EVENT))
    except Exception:
        return False


def ensure_accessibility(prompt: bool = True) -> bool:
    """True if the app is trusted for Accessibility; optionally show the prompt.

    Needed by the paste path (AX insert / synthesized Cmd+V). Import is lazy:
    pyobjc's ApplicationServices is heavy and only exists in the mac venv.
    """
    try:
        from ApplicationServices import (
            AXIsProcessTrustedWithOptions,
            kAXTrustedCheckOptionPrompt,
        )

        return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: prompt}))
    except Exception:
        return False
