"""Optional on-disk recording archive + persisted settings.

By default Pysar keeps audio only in memory (private). When the user
turns on "Save recordings" in the menu bar, each dictation's WAV is written
here *before* transcription — so a failed/aborted run can be recovered
(re-transcribed without re-speaking). Only the newest N files are kept; older
ones are deleted automatically, so disk use stays bounded.
"""

import json
from datetime import datetime
from pathlib import Path

_BASE = Path.home() / "Library" / "Application Support" / "Pysar"
_SETTINGS = _BASE / "settings.json"
_RECORDINGS = _BASE / "recordings"

DEFAULTS = {"save_recordings": False, "keep_last": 10}
KEEP_LAST_OPTIONS = (5, 10, 20)


def recordings_dir() -> Path:
    _RECORDINGS.mkdir(parents=True, exist_ok=True)
    return _RECORDINGS


def load_settings() -> dict:
    try:
        data = json.loads(_SETTINGS.read_text())
        merged = dict(DEFAULTS)
        for k in DEFAULTS:
            if k in data:
                merged[k] = data[k]
        if merged["keep_last"] not in KEEP_LAST_OPTIONS:
            merged["keep_last"] = DEFAULTS["keep_last"]
        return merged
    except Exception:
        return dict(DEFAULTS)


def save_settings(settings: dict) -> None:
    try:
        _BASE.mkdir(parents=True, exist_ok=True)
        payload = {k: settings.get(k, DEFAULTS[k]) for k in DEFAULTS}
        _SETTINGS.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        print(f"⚠️ could not save settings: {e}")


def save_recording(wav_bytes: bytes, keep_last: int) -> Path | None:
    """Write the WAV with a timestamped name, then prune to the newest keep_last."""
    try:
        d = recordings_dir()
        # Microsecond timestamp + a collision guard, so rapid dictations never
        # overwrite each other.
        base = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        path = d / f"{base}.wav"
        i = 1
        while path.exists():
            path = d / f"{base}_{i}.wav"
            i += 1
        path.write_bytes(wav_bytes)
        _prune(d, keep_last)
        return path
    except Exception as e:
        print(f"⚠️ could not save recording: {e}")
        return None


def _prune(d: Path, keep_last: int) -> None:
    files = sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[max(keep_last, 1):]:
        try:
            old.unlink()
        except Exception:
            pass
