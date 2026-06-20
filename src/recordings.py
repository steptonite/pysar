"""Optional on-disk recording archive + persisted settings.

By default Pysar keeps audio only in memory (private). When the user
turns on "Save recordings" in the menu bar, each dictation's WAV is written
here *before* transcription — so a failed/aborted run can be recovered
(re-transcribed without re-speaking). Only the newest N files are kept; older
ones are deleted automatically, so disk use stays bounded.
"""

import contextlib
import json
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_MODE, HOTKEY_KEYCODE
from .profiles import DEFAULT_PROFILES

_BASE = Path.home() / "Library" / "Application Support" / "Pysar"
_SETTINGS = _BASE / "settings.json"
_RECORDINGS = _BASE / "recordings"

DEFAULTS = {
    "save_recordings": False,
    "keep_last": 10,
    # Last-used language survives a restart (was reset to DEFAULT_MODE every launch).
    "mode": DEFAULT_MODE,
    # Input device *name* (stable across reconnect; index isn't). None = system default.
    "mic": None,
    # Reflects the macOS login-item registration; the real source of truth is
    # SMAppService, this is just so the menu checkmark survives a restart.
    "launch_at_login": False,
    # Speech profiles (whisper prompt priming — see profiles.py). `profiles` is
    # None until first load, then seeded with the shipped defaults so the user
    # can edit/import freely afterward. `active_profiles` maps a language code to
    # the profile names toggled on FOR THAT LANGUAGE — each language carries its
    # own group, and switching mode swaps which group composes into the prompt.
    "profiles": None,
    "active_profiles": {"uk": ["Суржик / розмова"]},
    # Push/toggle key. Caps Lock by default; remappable (applies on next launch).
    "hotkey_keycode": HOTKEY_KEYCODE,
    # Settings-window appearance: "auto" follows macOS, else forced light/dark.
    "ui_theme": "auto",
}
UI_THEMES = ("auto", "light", "dark")
KEEP_LAST_OPTIONS = (5, 10, 20)


def recordings_dir() -> Path:
    _RECORDINGS.mkdir(parents=True, exist_ok=True)
    return _RECORDINGS


def load_settings() -> dict:
    merged = dict(DEFAULTS)
    try:
        data = json.loads(_SETTINGS.read_text())
        for k in DEFAULTS:
            if k in data:
                merged[k] = data[k]
        if merged["keep_last"] not in KEEP_LAST_OPTIONS:
            merged["keep_last"] = DEFAULTS["keep_last"]
        if merged["ui_theme"] not in UI_THEMES:
            merged["ui_theme"] = DEFAULTS["ui_theme"]
    except Exception:
        pass  # missing/invalid settings file → fall back to defaults
    # Seed profiles on first run (None = never persisted), and always hand back
    # fresh mutable copies so a caller can't accidentally mutate DEFAULTS.
    if merged["profiles"] is None:
        merged["profiles"] = [dict(p) for p in DEFAULT_PROFILES]
    else:
        merged["profiles"] = [dict(p) for p in merged["profiles"]]
    # active_profiles: migrate the old flat list (pre-per-language) into the uk
    # group, then hand back a fresh dict of fresh lists.
    act = merged["active_profiles"]
    if isinstance(act, list):
        act = {"uk": act}
    merged["active_profiles"] = {lng: list(names) for lng, names in act.items()}
    return merged


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
    for old in files[max(keep_last, 1) :]:
        with contextlib.suppress(Exception):
            old.unlink()
