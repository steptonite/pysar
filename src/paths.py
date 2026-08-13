"""Shared data directory.

Everything the app persists — `settings.json` (profiles, hotkeys, language),
the recording archive, and the log — lives in one Application Support folder.

Dependency-free on purpose: `logsetup` imports this at startup, before anything
else is wired up.
"""

from pathlib import Path

_SUPPORT = Path.home() / "Library" / "Application Support"
_DATA = _SUPPORT / "Pysar"


def data_dir() -> Path:
    """Return the app's Application Support folder. Never creates it — callers
    `mkdir(exist_ok=True)` when they actually write."""
    return _DATA
