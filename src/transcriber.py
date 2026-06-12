"""HTTP client for the local whisper.cpp server."""

import re

import requests

from .config import MODES, WHISPER_TIMEOUT, WHISPER_URL

# whisper.cpp breaks long output into segments and joins them with newlines
# (and sometimes leading spaces). Pasted verbatim, a word can land split across
# a wrap like "в т\nекст". Collapse all internal whitespace to single spaces.
_WS = re.compile(r"\s+")


def _clean(text: str) -> str:
    # Whitespace normalization ONLY. We never drop words by content: a
    # hallucinated subtitle "credit" is textually identical to a real sentence,
    # so any content blocklist inevitably eats real dictation. Silence
    # hallucinations are handled upstream by the server's VAD (it never feeds
    # quiet audio to the model), which is the correct place to stop them.
    return _WS.sub(" ", text).strip()


def transcribe(wav_bytes: bytes, mode: str = "ru") -> tuple[str | None, str | None]:
    """Returns (text, error). Exactly one of them is always None.

    mode: code from config.MODES (e.g. "ru", "en", "translate", "ja", ...).
    """
    params = MODES.get(mode, MODES["ru"])
    try:
        resp = requests.post(
            WHISPER_URL,
            files={"file": ("audio.wav", wav_bytes, "audio/wav")},
            # Without an explicit `language` the whisper-server defaults to "en"
            # and returns a translation instead of a transcription for non-EN speech.
            data=params,
            timeout=WHISPER_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json()
        raw = result.get("text", "") if isinstance(result, dict) else str(result)
        text = _clean(raw)
        return (text, None) if text else (None, None)
    except requests.exceptions.ConnectionError:
        return None, f"Whisper not running at {WHISPER_URL}. Run `make whisper`."
    except Exception as e:
        return None, f"Whisper error: {e}"


def is_alive() -> bool:
    """Pings the server. Used to show the startup health status in the menu."""
    try:
        requests.get(WHISPER_URL.replace("/inference", "/"), timeout=1)
        return True
    except Exception:
        return False
