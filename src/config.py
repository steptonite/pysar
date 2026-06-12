"""Application constants. Tweak here — leave the other files alone."""

# ── Hotkey ───────────────────────────────────────────────────────────────────
# Caps Lock is a stateful key (with the LED). 1st tap = start, 2nd tap = stop.
HOTKEY_KEYCODE = 57  # Caps Lock

# ── Language-switch hotkeys ───────────────────────────────────────────────────
# Hold Control+Option and tap a letter to switch the output language without
# opening the menu. Listen-only: the combo also reaches the focused app, but
# Ctrl+Option+letter doesn't type anything, so it's harmless.
#   Ctrl+Option+U → Українська   Ctrl+Option+R → Русский   Ctrl+Option+E → English
# Keys are layout-independent virtual keycodes (E=14, R=15, U=32).
LANG_HOTKEYS = {
    14: "translate",  # E → 🌐 English-out (any spoken language → English text)
    15: "ru",  # R → Русский
    32: "uk",  # U → Українська
}

# ── Audio ────────────────────────────────────────────────────────────────────
SAMPLE_RATE = 16000  # whisper.cpp expects 16 kHz
CHANNELS = 1
CHUNK_SIZE = 1024

# ── Whisper.cpp server ───────────────────────────────────────────────────────
WHISPER_URL = "http://localhost:8080/inference"
WHISPER_TIMEOUT = 30  # seconds

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

# Idle menu-bar icon per mode — shows the active language at a glance so a
# hotkey switch gives instant visual confirmation. Fallback to the mic glyph.
MODE_ICONS = {
    "uk": "🇺🇦",
    "ru": "🇷🇺",
    "en": "🇬🇧",
    "translate": "🌐",
}
IDLE_ICON_FALLBACK = "🎙"

DEFAULT_MODE = "uk"

# ── Behavior ─────────────────────────────────────────────────────────────────
MIN_RECORDING_SEC = 0.3  # shorter recordings are ignored (guards against accidental taps)
CLIPBOARD_RESTORE_DELAY = 0.15  # delay before restoring the clipboard after Cmd+V
