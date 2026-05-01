"""HTTP client for the local whisper.cpp server."""

import requests

from .config import MODES, WHISPER_TIMEOUT, WHISPER_URL


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
        text = (result.get("text", "") if isinstance(result, dict) else str(result)).strip()
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
