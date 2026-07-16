"""HTTP client for the local whisper.cpp server."""

import re
import threading

import requests

from .config import MODES, WHISPER_TIMEOUT, WHISPER_URL

# One whisper server on a memory-tight Mac: dictation and the meeting capture
# may now run at the same time, so their transcription requests are serialized
# here — the server is never hit concurrently, whichever paths are active.
_whisper_lock = threading.Lock()

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


def transcribe(
    wav_bytes: bytes, mode: str = "ru", prompt: str = ""
) -> tuple[str | None, str | None]:
    """Returns (text, error). Exactly one of them is always None.

    mode:   code from config.MODES (e.g. "ru", "en", "translate", "ja", ...).
    prompt: optional initial prompt (glossary of names/jargon) — whisper.cpp
            biases decoding toward these spellings. Sent per-request, so it can
            be edited live from the menu without restarting the server.
    """
    params = dict(MODES.get(mode, MODES["ru"]))
    if prompt:
        params["prompt"] = prompt
    try:
        with _whisper_lock:
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


def transcribe_meeting(
    wav_bytes: bytes, mode: str = "ru", prompt: str = ""
) -> tuple[str | None, dict, str | None]:
    """Meeting-path variant of transcribe(): asks the server for verbose_json
    and returns (text, meta, error) where meta feeds meetingfilter.MeetingFilter:

      lang / lang_prob — best guess from language_probabilities (codes),
      no_speech / logprob — duration-weighted means over the result segments.

    meta is best-effort: any field the server didn't provide is simply absent,
    and the filter degrades to "keep". Text handling matches transcribe()."""
    params = dict(MODES.get(mode, MODES["ru"]))
    params["response_format"] = "verbose_json"
    if prompt:
        params["prompt"] = prompt
    try:
        with _whisper_lock:
            resp = requests.post(
                WHISPER_URL,
                files={"file": ("audio.wav", wav_bytes, "audio/wav")},
                data=params,
                timeout=WHISPER_TIMEOUT,
            )
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.ConnectionError:
        return None, {}, f"Whisper not running at {WHISPER_URL}. Run `make whisper`."
    except Exception as e:
        return None, {}, f"Whisper error: {e}"
    if not isinstance(result, dict):
        text = _clean(str(result))
        return (text or None), {}, None
    text = _clean(result.get("text", ""))
    meta: dict = {}
    probs = result.get("language_probabilities")
    if isinstance(probs, dict) and probs:
        lang, prob = max(probs.items(), key=lambda kv: kv[1])
        meta["lang"], meta["lang_prob"] = lang, float(prob)
    segs = result.get("segments")
    if isinstance(segs, list) and segs:
        try:
            durs = [max(float(s.get("end", 0)) - float(s.get("start", 0)), 0.01) for s in segs]
            total = sum(durs)
            meta["no_speech"] = (
                sum(float(s.get("no_speech_prob", 0)) * d for s, d in zip(segs, durs, strict=True))
                / total
            )
            meta["logprob"] = (
                sum(float(s.get("avg_logprob", 0)) * d for s, d in zip(segs, durs, strict=True))
                / total
            )
        except (TypeError, ValueError):
            pass  # malformed segment payload — keep whatever meta we already have
    return (text or None), meta, None


def is_alive() -> bool:
    """Pings the server. Used to show the startup health status in the menu."""
    try:
        requests.get(WHISPER_URL.replace("/inference", "/"), timeout=1)
        return True
    except Exception:
        return False
