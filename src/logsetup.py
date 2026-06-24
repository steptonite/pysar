"""Persistent logging for the .app build.

Launched from the bundle via `open`, the app has no terminal — every `print`
and traceback would otherwise vanish. This tees stdout/stderr to a rotating
file in Application Support and records uncaught exceptions (main thread *and*
worker threads), so the paste path, recorder, or Whisper leave a trail we can
read after the fact.

Footprint is deliberately tiny (a single line-buffered file, ~2 MB cap): on an
8 GB machine the logger must never become part of any memory problem.
"""

import contextlib
import datetime
import sys
import threading
import traceback
from pathlib import Path

from .paths import data_dir

_LOG_DIR = data_dir()
_LOG = _LOG_DIR / "pysar.log"
_MAX_BYTES = 2 * 1024 * 1024  # rotate past ~2 MB; keep one .1 backup
_installed = False


class _Tee:
    """Writes to the real stream *and* the log file. Keeps terminal output
    intact for `make up` while persisting everything for the .app."""

    def __init__(self, stream, fh):
        self._stream = stream
        self._fh = fh

    def write(self, data):
        for t in (self._stream, self._fh):
            with contextlib.suppress(Exception):
                t.write(data)

    def flush(self):
        for t in (self._stream, self._fh):
            with contextlib.suppress(Exception):
                t.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()


def _rotate() -> None:
    """Keep the log bounded: once it passes the cap, move it to pysar.log.1
    (overwriting any previous backup) and start fresh."""
    with contextlib.suppress(Exception):
        if _LOG.exists() and _LOG.stat().st_size > _MAX_BYTES:
            backup = _LOG.with_suffix(".log.1")
            backup.unlink(missing_ok=True)
            _LOG.replace(backup)


def setup_logging() -> Path | None:
    """Tee stdout/stderr to the log file and hook uncaught exceptions. Safe to
    call once; later calls are no-ops. Returns the log path (or None on failure
    — logging must never block the app from starting)."""
    global _installed
    if _installed:
        return _LOG
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _rotate()
        fh = open(_LOG, "a", buffering=1, encoding="utf-8")  # noqa: SIM115 — lives for the app's lifetime
    except Exception as e:
        # No log file? Run anyway — diagnostics are a nice-to-have, not a gate.
        print(f"⚠️ could not open log file: {e}")
        return None

    stamp = datetime.datetime.now().isoformat(timespec="seconds")
    fh.write(f"\n==== Pysar start {stamp} ====\n")

    sys.stdout = _Tee(sys.__stdout__, fh)
    sys.stderr = _Tee(sys.__stderr__, fh)

    def _log_exc(prefix, exc_type, exc, tb) -> None:
        when = datetime.datetime.now().isoformat(timespec="seconds")
        print(f"‼️ {when} {prefix}:", file=sys.stderr)
        traceback.print_exception(exc_type, exc, tb, file=sys.stderr)

    def _excepthook(exc_type, exc, tb) -> None:
        _log_exc("uncaught exception", exc_type, exc, tb)

    sys.excepthook = _excepthook

    def _thread_excepthook(args) -> None:
        _log_exc(
            f"uncaught in thread {args.thread.name}",
            args.exc_type,
            args.exc_value,
            args.exc_traceback,
        )

    threading.excepthook = _thread_excepthook

    _installed = True
    return _LOG


def log(msg: str) -> None:
    """Timestamped one-liner to stdout (→ the log file once setup_logging ran).
    Used for the paste-path breadcrumbs so we can see, per dictation, what was
    captured and how the text was delivered."""
    when = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{when}] {msg}")
