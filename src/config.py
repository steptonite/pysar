"""Application constants. Tweak here — leave the other files alone."""

# ── Hotkeys ──────────────────────────────────────────────────────────────────
# Hotkeys are freely user-assignable, captured live in Settings. A binding is
# {"keycode": <macOS virtual keycode>, "mods": [<modifier names>]}, where a
# modifier name is one of "control"/"option"/"command"/"shift".
#
# The one hard rule (`is_bindable`): the event tap is listen-only — it observes
# keystrokes but can't swallow them — so a *printable* key bound on its own would
# still type its character. Therefore a binding must EITHER carry ≥1 modifier
# (⌃⌥⇧⌘ + any key, like a normal macOS shortcut) OR use a key that emits no text
# on its own (Caps Lock, a modifier key, or an F-key). The capture UI enforces it.

HOTKEY_KEYCODE = 57  # Caps Lock — the default toggle key (legacy constant)

# Default toggle binding, used on first run / when settings are missing or corrupt.
DEFAULT_HOTKEY = {"keycode": 57, "mods": []}  # Caps Lock, tap to start/stop

# Per-language switch shortcuts are defined further down, once MENU_MODES exists —
# see DEFAULT_LANG_HOTKEYS / LANG_HOTKEY_ACTIONS below. EVERY language is an
# assignable slot; only a few carry a default binding (the rest start unassigned,
# keycode=None, and can be bound from Settings).

# Modifier keycodes (left & right) → the flag name each carries. Right-hand ones
# also double as bindable bare keys (they emit no text).
MODIFIER_KEYCODES = {
    54: "command",
    55: "command",
    58: "option",
    61: "option",
    59: "control",
    62: "control",
    56: "shift",
    60: "shift",
}
# F1–F19 emit no text. F1–F12 exist on the built-in keyboard (bound to media);
# F13–F19 only on external keyboards. All bindable on their own.
_FN_KEYCODES = frozenset(
    {122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111}  # F1–F12
    | {105, 107, 113, 106, 64, 79, 80}  # F13–F19
)
# Keys allowed as a bare binding (no modifier needed) — they type nothing.
NONTYPING_KEYCODES = frozenset({57} | set(MODIFIER_KEYCODES) | _FN_KEYCODES)

# Apple's canonical display order + glyphs for a shortcut string.
MODIFIER_ORDER = ["control", "option", "shift", "command"]
MODIFIER_SYMBOLS = {"control": "⌃", "option": "⌥", "shift": "⇧", "command": "⌘"}

# Bare special keys get a spelled-out label; everything else uses KEY_LABELS.
_BARE_LABELS = {
    57: "Caps Lock",
    54: "Right ⌘",
    55: "Left ⌘",
    61: "Right ⌥",
    58: "Left ⌥",
    62: "Right ⌃",
    59: "Left ⌃",
    60: "Right ⇧",
    56: "Left ⇧",
}
# macOS virtual keycode → label (US layout). Only the printable/named keys we'd
# ever show; anything missing falls back to "Key N".
KEY_LABELS = {
    0: "A", 1: "S", 2: "D", 3: "F", 4: "H", 5: "G", 6: "Z", 7: "X", 8: "C",
    9: "V", 11: "B", 12: "Q", 13: "W", 14: "E", 15: "R", 16: "Y", 17: "T",
    18: "1", 19: "2", 20: "3", 21: "4", 22: "6", 23: "5", 24: "=", 25: "9",
    26: "7", 27: "-", 28: "8", 29: "0", 30: "]", 31: "O", 32: "U", 33: "[",
    34: "I", 35: "P", 37: "L", 38: "J", 39: "'", 40: "K", 41: ";", 42: "\\",
    43: ",", 44: "/", 45: "N", 46: "M", 47: ".", 49: "Space", 48: "Tab",
    36: "Return", 51: "Delete", 53: "Esc",
    122: "F1", 120: "F2", 99: "F3", 118: "F4", 96: "F5", 97: "F6", 98: "F7",
    100: "F8", 101: "F9", 109: "F10", 103: "F11", 111: "F12", 105: "F13",
    107: "F14", 113: "F15", 106: "F16", 64: "F17", 79: "F18", 80: "F19",
    123: "←", 124: "→", 125: "↓", 126: "↑",
}  # fmt: skip


def key_label(keycode: int) -> str:
    """Label for a single key (no modifiers), falling back to the raw code."""
    return _BARE_LABELS.get(keycode) or KEY_LABELS.get(keycode, f"Key {keycode}")


def binding_label(keycode: int, mods) -> str:
    """Human shortcut string, e.g. '⌃⌥U' or 'Caps Lock' or 'Right ⌥'."""
    mods = set(mods or [])
    if not mods and keycode in _BARE_LABELS:
        return _BARE_LABELS[keycode]
    prefix = "".join(MODIFIER_SYMBOLS[m] for m in MODIFIER_ORDER if m in mods)
    return prefix + key_label(keycode)


def is_bindable(keycode: int, mods) -> bool:
    """A binding is allowed if it carries a modifier, or the key emits no text."""
    return bool(mods) or keycode in NONTYPING_KEYCODES


def match_keydown(bindings, keycode: int, mods):
    """Pure matcher for the key-down path: return the action of the binding whose
    keycode and modifier set match exactly, else None. `bindings` is a list of
    {"action","keycode","mods"} dicts (the toggle uses action '__toggle__')."""
    want = set(mods or [])
    for b in bindings:
        if b.get("keycode") == keycode and set(b.get("mods") or []) == want:
            return b.get("action")
    return None


# ── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000  # whisper.cpp expects 16 kHz
CHANNELS = 1
CHUNK_SIZE = 1024

# ── Streaming dictation ──────────────────────────────────────────────────────
# Pause-based segmentation for the "streaming" dictation mode (see segmenter.py).
# A segment is cut when trailing silence reaches PAUSE_SEC *and* it holds at
# least MIN_SEG_SEC of voiced audio; MAX_SEG_SEC force-emits a run-on that never
# pauses. SILENCE_MARGIN is how many times above the adaptive noise floor a
# block's RMS must be to count as speech. Start values — tune against real audio.
# PAUSE/MIN raised from 0.6/0.8: cutting too eagerly sliced sub-sentence
# fragments, and whisper on a 1-second scrap with no context guesses (wrong words,
# random punctuation). Waiting for a fuller clause costs a little latency but the
# recognition is far better — and streaming carries the previous text forward as
# context (see VoiceTyper._stream_prompt), so the small extra wait is worth it.
PAUSE_SEC = 0.9  # trailing silence that ends a segment
MIN_SEG_SEC = 1.6  # min voiced audio before a pause can cut
MAX_SEG_SEC = 18.0  # hard cap; force-emit without a pause (rare run-on fallback)
SILENCE_MARGIN = 4.0  # block RMS > noise_floor × this ⇒ voiced

# Minimal per-language priming so the decoder keeps the right script even when no
# speech profile is active. large-v3-turbo drifts to Russian on Ukrainian audio
# with an empty prompt; a neutral one-line seed in the target language anchors it.
# Only languages that share a script with a "louder" neighbour need this.
LANG_SEED = {
    "uk": "Розшифровка диктовки українською мовою.",
    "ru": "Расшифровка диктовки на русском языке.",
}

# ── Whisper.cpp server ───────────────────────────────────────────────────────
WHISPER_URL = "http://localhost:8080/inference"
WHISPER_TIMEOUT = 180  # seconds — long dictations on 8 GB under load need headroom
# (a multi-minute recording can take well over 30 s to transcribe; too low a
# timeout aborts the request mid-decode → "HTTPConnectionPool / port 8080" error)

# ── Transcription modes ──────────────────────────────────────────────────────
# Whisper quirk: the encoder produces a language-agnostic representation of the
# audio, and the decoder emits whichever language is named in `language`. This
# works IN ANY DIRECTION between languages the model knows: speak Russian with
# language=en → English translation; speak English with language=ru → Russian
# translation; and so on.
#
# We don't use the native `translate=true` flag — on large-v3-turbo it's broken
# (the model was fine-tuned without translation data). Swapping `language`
# sidesteps the limitation.
#
# The trick doesn't work with "auto" — Whisper would honestly detect the source
# language and emit that.
MODES = {
    "ru": {"language": "ru", "translate": "false"},
    "en": {"language": "en", "translate": "false"},
    "translate": {"language": "en", "translate": "false"},  # alias for en, kept for UX clarity
    "auto": {"language": "auto", "translate": "false"},  # whisper auto-detects the spoken language
    "uk": {"language": "uk", "translate": "false"},
    "es": {"language": "es", "translate": "false"},
    "de": {"language": "de", "translate": "false"},
    "fr": {"language": "fr", "translate": "false"},
    "it": {"language": "it", "translate": "false"},
    "pt": {"language": "pt", "translate": "false"},
    "nl": {"language": "nl", "translate": "false"},
    "pl": {"language": "pl", "translate": "false"},
    "ja": {"language": "ja", "translate": "false"},
    "zh": {"language": "zh", "translate": "false"},
    "ko": {"language": "ko", "translate": "false"},
    "tr": {"language": "tr", "translate": "false"},
    "th": {"language": "th", "translate": "false"},
    "vi": {"language": "vi", "translate": "false"},
    "ar": {"language": "ar", "translate": "false"},
}

# Endonym labels — every language is named in its own script. This is the
# conventional best practice for language pickers.
MODE_LABELS = {
    "auto": "🅰️ Auto (detect)",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English",
    "translate": "🌐 → English (from any)",
    "uk": "🇺🇦 Українська",
    "es": "🇪🇸 Español",
    "de": "🇩🇪 Deutsch",
    "fr": "🇫🇷 Français",
    "it": "🇮🇹 Italiano",
    "pt": "🇵🇹 Português",
    "nl": "🇳🇱 Nederlands",
    "pl": "🇵🇱 Polski",
    "ja": "🇯🇵 日本語",
    "zh": "🇨🇳 中文",
    "ko": "🇰🇷 한국어",
    "tr": "🇹🇷 Türkçe",
    "th": "🇹🇭 ไทย",
    "vi": "🇻🇳 Tiếng Việt",
    "ar": "🇸🇦 العربية",
}

# Order of items in the «🌍 Languages» submenu. The current one gets a checkmark.
MENU_MODES = [
    "auto",
    "translate",
    "en",
    "uk",
    "es",
    "de",
    "fr",
    "it",
    "pt",
    "nl",
    "pl",
    "ja",
    "zh",
    "ko",
    "tr",
    "th",
    "vi",
    "ar",
    "ru",
]

# Display letters for the Ctrl+Option language-switch shortcuts (mirrors the
# default DEFAULT_LANG_HOTKEYS below: U→uk, R→ru, E→translate). The real binding
# is the global event tap in HotkeyListener; these just surface the default combo
# in the «🌍 Languages» menu as a native key-equivalent.
MODE_SHORTCUTS = {
    "uk": "U",
    "ru": "R",
    "translate": "E",
}

# ── Per-language switch shortcuts ─────────────────────────────────────────────
# Every language is an assignable slot (so any can get its own hotkey). Only a
# few ship with a default combo; the rest start unassigned (keycode=None) and can
# be bound — or cleared back to unassigned — from Settings. Order follows the menu.
LANG_HOTKEY_ACTIONS = list(MENU_MODES)
_DEFAULT_LANG_BINDINGS = {
    "translate": (14, ["control", "option"]),  # ⌃⌥E
    "ru": (15, ["control", "option"]),  # ⌃⌥R
    "uk": (32, ["control", "option"]),  # ⌃⌥U
}
DEFAULT_LANG_HOTKEYS = [
    {
        "action": action,
        "keycode": _DEFAULT_LANG_BINDINGS.get(action, (None, []))[0],
        "mods": list(_DEFAULT_LANG_BINDINGS.get(action, (None, []))[1]),
    }
    for action in LANG_HOTKEY_ACTIONS
]

# ── Profile-set hotkeys ───────────────────────────────────────────────────────
# A *profile set* is a named bundle of speech profiles, activated all at once by
# ⌃⌥<digit>, where the digit is the set's 1-based position in the list (max 9).
# The combo is fixed, not user-captured — the index IS the shortcut, so there's
# no per-set capture UI. Activating set N replaces the whole active selection.
SET_MODS = ("control", "option")
SET_DIGIT_KEYCODES = (18, 19, 20, 21, 23, 22, 26, 28, 25)  # virtual codes for 1..9
MAX_PROFILE_SETS = 9


def set_hotkey_bindings(profile_sets) -> list[dict]:
    """Each set's binding, by index, for the hotkey listener. A set with an
    explicit keycode overrides; otherwise it falls back to the default ⌃⌥<digit>.
    Returns {"action": "set:<i>", "keycode", "mods"} dicts (i is 0-based)."""
    out = []
    for i, s in enumerate(profile_sets[:MAX_PROFILE_SETS]):
        kc = s.get("keycode") if isinstance(s, dict) else None
        if kc is not None:
            out.append({"action": f"set:{i}", "keycode": kc, "mods": list(s.get("mods") or [])})
        else:
            out.append(
                {"action": f"set:{i}", "keycode": SET_DIGIT_KEYCODES[i], "mods": list(SET_MODS)}
            )
    return out


def set_hotkey_label(index: int, profile_set: dict | None = None) -> str:
    """Display string for set N's shortcut. An explicit per-set binding wins;
    otherwise the default ⌃⌥<digit> for this index (index is 0-based)."""
    if profile_set and profile_set.get("keycode") is not None:
        return binding_label(profile_set["keycode"], profile_set.get("mods") or [])
    if 0 <= index < MAX_PROFILE_SETS:
        return binding_label(SET_DIGIT_KEYCODES[index], list(SET_MODS))
    return ""


# Idle menu-bar icon per mode — shows the active language at a glance so a
# hotkey switch gives instant visual confirmation. Fallback to the mic glyph.
MODE_ICONS = {
    "auto": "🅰️",
    "uk": "🇺🇦",
    "ru": "🇷🇺",
    "en": "🇬🇧",
    "translate": "🌐",
}
IDLE_ICON_FALLBACK = "🎙"

DEFAULT_MODE = "uk"

# ── Behavior ─────────────────────────────────────────────────────────────────
MIN_RECORDING_SEC = 0.3  # shorter recordings are ignored (guards against accidental taps)
CLIPBOARD_RESTORE_DELAY = 0.4  # delay before restoring the clipboard after Cmd+V
# (long enough that the front app reads the paste before the old clipboard is
# swapped back in — was 0.15 s, too tight on 8 GB under memory pressure)
