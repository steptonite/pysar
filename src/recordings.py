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

from .config import (
    DEFAULT_HOTKEY,
    DEFAULT_LANG_HOTKEYS,
    DEFAULT_MODE,
    LANG_HOTKEY_ACTIONS,
    MAX_PROFILE_SETS,
)
from .i18n import UI_LANGS
from .paths import data_dir
from .profiles import DEFAULT_PROFILES

_BASE = data_dir()
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
    # Hotkeys, freely user-assignable (captured live in Settings). Each binding is
    # {"keycode", "mods"}; the toggle defaults to Caps Lock, the language switches
    # to ⌃⌥U/R/E. Normalised on load (legacy "hotkey_keycode" int is migrated).
    "hotkey": DEFAULT_HOTKEY,
    "lang_hotkeys": DEFAULT_LANG_HOTKEYS,
    # Settings-window appearance: "auto" follows macOS, else forced light/dark.
    "ui_theme": "auto",
    # App language — currently drives only the copied AI prompt's language.
    "ui_lang": "uk",
    # Dictation mode: "batch" transcribes the whole clip once at the end (default,
    # highest quality); "streaming" cuts on pauses and types each sentence live.
    "dictation_mode": "batch",
    # Profile sets: named bundles of profile names, each activated all-at-once by
    # ⌃⌥<digit> (digit = 1-based index). Empty until the user creates one.
    "profile_sets": [],
    # "Transcribe everything" (meeting/call) mode — system audio + mic → live
    # transcript window. Configurable from its own Settings screen:
    #   meeting_capture_mic — also capture the mic, not just system audio
    #   meeting_save_file   — write the transcript to ~/…/Pysar/transcripts
    #   meeting_on_top      — float the transcript window above other windows
    #   meeting_mode        — transcription language; None = inherit the dictation
    #                         mode live (so the picker can stay out of the way)
    #   meeting_prompt      — custom names/jargon to bias decoding (our edge; empty
    #                         falls back to the active speech profiles, as before)
    "meeting_capture_mic": True,
    "meeting_save_file": True,
    "meeting_on_top": False,
    "meeting_mode": None,
    "meeting_prompt": "",
    #   meeting_prompt_source — "custom" = use the field above; "profiles" = use
    #                           the active dictation profiles of the meeting language
    "meeting_prompt_source": "custom",
    #   meeting_source_mode — speaker separation in the transcript:
    #     "off"   = mixed stream, no source labels (as before)
    #     "fast"  = one mixed pass; label each segment by the louder source (RMS)
    #     "smart" = two separate whisper passes (system / mic) → reliable labels,
    #               ~2× slower when both speak (one shared model, RAM unchanged)
    "meeting_source_mode": "off",
    #   meeting_island_frame — last position/size of the floating transcript island
    #                          ({x,y,w,h}); None = default (top-right of the screen)
    "meeting_island_frame": None,
    #   meeting_hidden — record to file only, never show the floating island
    "meeting_hidden": False,
}
UI_THEMES = ("auto", "light", "dark")
KEEP_LAST_OPTIONS = (5, 10, 20)


def recordings_dir() -> Path:
    _RECORDINGS.mkdir(parents=True, exist_ok=True)
    return _RECORDINGS


_VALID_MODS = ("control", "option", "command", "shift")


def _norm_binding(b) -> dict | None:
    """Coerce a stored binding into {"keycode": int, "mods": [valid mods]} or None."""
    if isinstance(b, dict) and isinstance(b.get("keycode"), int):
        mods = [m for m in (b.get("mods") or []) if m in _VALID_MODS]
        return {"keycode": b["keycode"], "mods": mods}
    return None


def _norm_hotkey(hk, legacy_keycode) -> dict:
    """The toggle binding, migrating the pre-combo `hotkey_keycode` int if needed."""
    b = _norm_binding(hk)
    if b:
        return b
    if isinstance(legacy_keycode, int):
        return {"keycode": legacy_keycode, "mods": []}
    return {"keycode": DEFAULT_HOTKEY["keycode"], "mods": list(DEFAULT_HOTKEY["mods"])}


def _norm_lang_hotkeys(lst) -> list[dict]:
    """One binding per language slot, in LANG_HOTKEY_ACTIONS order; any missing or
    invalid slot falls back to its default."""
    stored = {}
    if isinstance(lst, list):
        for h in lst:
            if not isinstance(h, dict) or h.get("action") not in LANG_HOTKEY_ACTIONS:
                continue
            if h.get("keycode") is None:  # explicitly unassigned (user cleared it)
                stored[h["action"]] = {"action": h["action"], "keycode": None, "mods": []}
                continue
            b = _norm_binding(h)
            if b:
                stored[h["action"]] = {"action": h["action"], **b}
    defaults = {h["action"]: h for h in DEFAULT_LANG_HOTKEYS}
    out = []
    for action in LANG_HOTKEY_ACTIONS:
        if action in stored:
            out.append(stored[action])
        else:
            d = defaults[action]
            out.append({"action": action, "keycode": d["keycode"], "mods": list(d["mods"])})
    return out


def _norm_profile_sets(lst) -> list[dict]:
    """Structural clean of stored profile sets: keep up to MAX_PROFILE_SETS, each
    a {"name": str, "members": [str], "keycode": int|None, "mods": [str]}. A
    keycode of None means "use the default ⌃⌥<digit> for this index"; an explicit
    binding overrides it. Bad entries are dropped; member existence isn't checked
    here (profiles may load later), the activator skips strays."""
    out: list[dict] = []
    if isinstance(lst, list):
        for s in lst:
            if not isinstance(s, dict):
                continue
            name = str(s.get("name", "")).strip()
            if not name:
                continue
            members = [str(m) for m in (s.get("members") or []) if isinstance(m, str)]
            b = _norm_binding(s)  # an explicit override binding, or None → default
            out.append(
                {
                    "name": name,
                    "members": members,
                    "keycode": b["keycode"] if b else None,
                    "mods": b["mods"] if b else [],
                }
            )
            if len(out) >= MAX_PROFILE_SETS:
                break
    return out


def load_settings() -> dict:
    merged = dict(DEFAULTS)
    data: dict = {}
    try:
        data = json.loads(_SETTINGS.read_text())
        for k in DEFAULTS:
            if k in data:
                merged[k] = data[k]
        if merged["keep_last"] not in KEEP_LAST_OPTIONS:
            merged["keep_last"] = DEFAULTS["keep_last"]
        if merged["ui_theme"] not in UI_THEMES:
            merged["ui_theme"] = DEFAULTS["ui_theme"]
        if merged["ui_lang"] not in UI_LANGS:
            merged["ui_lang"] = DEFAULTS["ui_lang"]
        if merged["dictation_mode"] not in ("batch", "streaming"):
            merged["dictation_mode"] = DEFAULTS["dictation_mode"]
    except Exception:
        pass  # missing/invalid settings file → fall back to defaults
    # Normalise hotkeys into fresh dicts (also migrates the legacy int keycode).
    merged["hotkey"] = _norm_hotkey(merged.get("hotkey"), data.get("hotkey_keycode"))
    merged["lang_hotkeys"] = _norm_lang_hotkeys(merged.get("lang_hotkeys"))
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
    merged["profile_sets"] = _norm_profile_sets(merged.get("profile_sets"))
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
