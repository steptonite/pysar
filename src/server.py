"""On-demand whisper.cpp server lifecycle (lazy start).

The server is normally launched once by scripts/start.sh at app start. But on a
memory-tight Mac (8 GB) macOS' jetsam reaps it during standby — it's the biggest
RAM consumer (~1 GB model resident) while the tiny menu-bar app survives. So you
wake up to "Whisper not running" and broken dictation.

Rather than keep a daemon alive (deliberately NOT doing background autostart —
see the user's no-autostart rule), we bring the server back *on demand*: when a
dictation starts and the server is down, spin it up so it's warm by the time the
user stops talking. Anything we start here we also kill on app exit, so there's
always a clean STOP and nothing outlives the app.
"""

import atexit
import contextlib
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from .transcriber import is_alive

_LOG = "/tmp/cream-whisper.log"
_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def is_up() -> bool:
    """True if the whisper server answers right now."""
    return is_alive()


def _script() -> Path:
    return Path(__file__).resolve().parents[1] / "scripts" / "whisper_server.sh"


def ensure_running(timeout: float = 30.0) -> bool:
    """If the server is down, start it and wait until healthy (up to `timeout`).
    Returns True if the server is up by the end.

    Safe to call concurrently and when the server is already up (then it's just a
    quick health-check no-op — the normal case, so the hot path stays cheap)."""
    if is_alive():
        return True
    script = _script()
    if not script.exists():
        return False
    with _lock:
        if is_alive():  # another caller won the race while we waited on the lock
            return True
        global _proc
        if _proc is None or _proc.poll() is not None:
            # Detached session (start_new_session) so an incidental SIGHUP on
            # sleep/logout can't reach it; we still kill it deliberately on exit.
            # whisper_server.sh exec's the binary, so _proc.pid IS the server.
            with open(_LOG, "ab") as log:
                _proc = subprocess.Popen(
                    ["/bin/bash", str(script)],
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            atexit.register(shutdown)
    # Poll for health outside the lock so concurrent callers see is_alive() fast.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_alive():
            return True
        if _proc is not None and _proc.poll() is not None:
            return False  # died on startup — caller surfaces the usual error
        time.sleep(0.5)
    return is_alive()


def shutdown() -> None:
    """SIGKILL a server WE started, with its process group. SIGKILL (not TERM)
    because whisper-server's own signal handler calls exit(), which aborts inside
    the Metal teardown and litters DiagnosticReports with a crash report on every
    quit; it's stateless, so a hard kill loses nothing. No-op if we didn't start
    one (then scripts/start.sh owns it and cleans it up via its own trap)."""
    global _proc
    p = _proc
    _proc = None
    if p is not None and p.poll() is None:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(p.pid), signal.SIGKILL)
