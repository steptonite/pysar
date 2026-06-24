"""Shared data directory + one-time migration from the pre-rebrand location.

Everything the app persists — `settings.json` (profiles, hotkeys, language),
the recording archive, and the log — lives in one Application Support folder.
That folder was named "Pysar" before the Pysar rebrand; on the first run
after the rename we move the whole folder to the new name, so existing settings
and recordings are preserved as a unit rather than orphaned.

Dependency-free on purpose: `logsetup` imports this at startup, before anything
else is wired up. The migration is a single atomic directory rename guarded so
it only fires when the old folder exists and the new one does not yet.
"""

import contextlib
from pathlib import Path

_SUPPORT = Path.home() / "Library" / "Application Support"
_NEW = _SUPPORT / "Pysar"
_OLD = _SUPPORT / "Pysar"  # pre-rebrand location
_migrated = False


def data_dir() -> Path:
    """Return the app's Application Support folder, migrating the pre-rebrand
    "Pysar" folder to "Pysar" once if needed. Never creates the folder —
    callers `mkdir(exist_ok=True)` when they actually write — so the migration
    guard (old exists AND new absent) stays meaningful."""
    global _migrated
    if not _migrated:
        with contextlib.suppress(Exception):
            if _OLD.exists() and not _NEW.exists():
                _OLD.rename(_NEW)
        _migrated = True
    return _NEW
