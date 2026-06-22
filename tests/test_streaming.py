"""Streaming-dictation worker logic + the pcm_to_wav helper.

The worker is exercised through VoiceTyper._process_segment with fakes for
transcribe / type_text, built without running the heavy __init__ (no tray)."""

import wave

import numpy as np

from cream_typer import app as app_mod
from cream_typer.app import VoiceTyper
from cream_typer.recorder import pcm_to_wav


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
