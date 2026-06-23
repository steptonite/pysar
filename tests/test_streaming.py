"""Streaming-dictation worker logic + the pcm_to_wav helper.

The worker is exercised through VoiceTyper._process_segment with fakes for
transcribe / type_text, built without running the heavy __init__ (no tray)."""

import wave

import numpy as np

from pysar import app as app_mod
from pysar.app import VoiceTyper
from pysar.recorder import pcm_to_wav


class _FakePaster:
    def __init__(self, editable: bool = True):
        self.typed: list[str] = []
        self.clipboard: str | None = None
        self._editable = editable

    def type_text(self, text: str) -> None:
        self.typed.append(text)

    def has_editable_focus(self, target) -> bool:
        return self._editable

    def set_clipboard(self, text: str) -> None:
        self.clipboard = text


class _FakeTray:
    def __init__(self):
        self.notifications: list[tuple] = []

    def set_status(self, *a, **k):
        pass

    def show_hud(self, *a, **k):
        pass

    def hide_hud(self, *a, **k):
        pass

    def notify(self, *a, **k):
        self.notifications.append(a)


def _vt(paster):
    """A VoiceTyper shell with just the fields _process_segment touches."""
    vt = object.__new__(VoiceTyper)
    vt._paster = paster
    vt._tray = _FakeTray()
    vt._mode = "uk"
    vt._first_typed = False
    vt._typed_chars = 0
    vt._stream_err = None
    vt._buffered = []
    vt._buffer_mode = False
    vt._server_down = False
    vt._ctx_tail = ""
    vt._paste_target = None
    vt._t = lambda key, **kw: key
    return vt


def test_first_segment_no_prefix_then_space(monkeypatch):
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("Привіт", None))
    p = _FakePaster()
    vt = _vt(p)
    vt._process_segment(b"w1", "prompt")
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("світ", None))
    vt._process_segment(b"w2", "prompt")
    # First chunk has no leading space; the second joins with exactly one.
    assert p.typed == ["Привіт", " світ"]
    assert vt._first_typed is True


def test_transcription_error_is_skipped(monkeypatch):
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: (None, "Whisper down"))
    p = _FakePaster()
    vt = _vt(p)
    vt._process_segment(b"w1", "prompt")  # must not raise, must not type
    assert p.typed == []
    assert vt._first_typed is False
    assert vt._stream_err == "Whisper down"


def test_server_down_notifies_once_then_rearms(monkeypatch):
    # A dead server must surface immediately (not only at Stop): one push on the
    # first failed segment, none on the next failure, and the latch re-arms once a
    # segment succeeds — so a second outage notifies again.
    p = _FakePaster()
    vt = _vt(p)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: (None, "Whisper down"))
    vt._process_segment(b"w1", "prompt")
    vt._process_segment(b"w2", "prompt")
    assert len(vt._tray.notifications) == 1  # one push, not one per failed segment
    assert vt._server_down is True
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("ок", None))
    vt._process_segment(b"w3", "prompt")  # recovers → re-arms the notice
    assert vt._server_down is False
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: (None, "Whisper down"))
    vt._process_segment(b"w4", "prompt")  # second outage → notifies again
    assert len(vt._tray.notifications) == 2


def test_empty_text_skipped_keeps_first_flag(monkeypatch):
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: (None, None))
    p = _FakePaster()
    vt = _vt(p)
    vt._process_segment(b"w1", "prompt")
    assert p.typed == []
    assert vt._first_typed is False


def test_error_then_success_first_chunk_has_no_prefix(monkeypatch):
    p = _FakePaster()
    vt = _vt(p)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: (None, "boom"))
    vt._process_segment(b"w1", "prompt")  # skipped
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("Текст", None))
    vt._process_segment(b"w2", "prompt")
    # The skipped segment never set _first_typed, so the first *typed* chunk
    # still has no leading space.
    assert p.typed == ["Текст"]


def test_lost_field_buffers_without_touching_clipboard_per_sentence(monkeypatch):
    # No editable field focused (Spotlight/desktop) → sentences must NOT be typed
    # blind, and must NOT be written to the clipboard per sentence (that lands at
    # stop instead). They accumulate in memory; _first_typed stays False; one push.
    p = _FakePaster(editable=False)
    vt = _vt(p)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("Привіт", None))
    vt._process_segment(b"w1", "prompt")
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("світ", None))
    vt._process_segment(b"w2", "prompt")
    assert p.typed == []
    assert vt._buffered == ["Привіт", "світ"]
    assert p.clipboard is None  # written once at stop, not per sentence
    assert vt._first_typed is False
    assert vt._buffer_mode is True
    assert len(vt._tray.notifications) == 1  # single entry push, not per sentence


def test_buffer_mode_is_sticky_after_field_returns(monkeypatch):
    # Lose the field for one sentence → latch buffer mode. Even if a field
    # reappears, every later sentence keeps going to the buffer (we can't trust
    # where live typing would land mid-session), and only one push fires.
    p = _FakePaster(editable=False)
    vt = _vt(p)
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("геть", None))
    vt._process_segment(b"w1", "prompt")  # buffered, latches buffer mode
    p._editable = True  # field comes back…
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("ще", None))
    vt._process_segment(b"w2", "prompt")  # …but stays buffered (sticky)
    assert p.typed == []
    assert vt._buffered == ["геть", "ще"]
    assert len(vt._tray.notifications) == 1


def test_rolling_context_is_fed_to_the_next_segment(monkeypatch):
    # Each recognized segment extends the rolling context, and that context is
    # passed to whisper as the prompt on the *next* segment (continuity across
    # cuts). Capture the prompt the transcriber sees.
    seen_prompts = []

    def fake_transcribe(wav, mode="uk", prompt=""):
        seen_prompts.append(prompt)
        return ("Привіт", None)

    monkeypatch.setattr(app_mod, "transcribe", fake_transcribe)
    p = _FakePaster()
    vt = _vt(p)
    vt._process_segment(b"w1", "БАЗА")  # first: only the base prompt, no context yet
    vt._process_segment(b"w2", "БАЗА")  # second: base + the tail from the first
    assert seen_prompts[0] == "БАЗА"
    assert seen_prompts[1] == "БАЗА Привіт"
    assert vt._ctx_tail.endswith("Привіт")


def test_rolling_context_is_length_capped(monkeypatch):
    monkeypatch.setattr(app_mod, "transcribe", lambda *a, **k: ("слово " * 20, None))
    p = _FakePaster()
    vt = _vt(p)
    for _ in range(5):
        vt._process_segment(b"w", "")
    assert len(vt._ctx_tail) <= VoiceTyper._CTX_TAIL_CHARS


def test_pcm_to_wav_roundtrip():
    pcm = np.zeros(16000, dtype=np.float32)
    pcm[100:200] = 0.5  # a little signal so normalization runs
    wav = pcm_to_wav(pcm.tobytes())
    assert wav is not None
    import io

    with wave.open(io.BytesIO(wav), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getframerate() == 16000
        assert wf.getsampwidth() == 2
        assert wf.getnframes() == 16000


def test_pcm_to_wav_empty_is_none():
    assert pcm_to_wav(b"") is None
