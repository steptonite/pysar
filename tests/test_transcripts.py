"""TranscriptFile speaker-source + timestamp headers — pure string I/O, no filesystem."""

import io
from datetime import datetime

from pysar import transcripts
from pysar.transcripts import TranscriptFile

_TS = datetime(2026, 6, 30, 14, 32, 7)  # fixed clock → "14:32"


def _tf():
    tf = TranscriptFile()
    tf._fh = io.StringIO()  # bypass open(); append() only needs a writable handle
    tf.set_source_labels({"sys": "System", "mic": "You"})
    return tf


def test_no_source_stamps_time_only():
    tf = _tf()
    tf.append("hello", ts=_TS)
    # Unknown source (mixed "off" mode) → header carries the time only.
    assert tf._fh.getvalue() == "**14:32**\n\nhello\n\n"


def test_every_block_gets_source_and_time_header():
    tf = _tf()
    tf.append("a", source="sys", ts=_TS)
    tf.append("b", source="sys", ts=_TS)  # same speaker → header still repeats per block
    tf.append("c", source="mic", ts=_TS)
    out = tf._fh.getvalue()
    assert out == ("**System · 14:32**\n\na\n\n**System · 14:32**\n\nb\n\n**You · 14:32**\n\nc\n\n")


def test_source_label_resolves_from_map():
    tf = _tf()
    tf.append("x", source="mic", ts=_TS)
    assert "**You · 14:32**" in tf._fh.getvalue()


# ── User-chosen output folder ────────────────────────────────────────────────


def test_transcripts_dir_override_is_created_and_used(tmp_path):
    target = tmp_path / "Efiry" / "out"
    try:
        transcripts.set_transcripts_dir(target)
        assert transcripts.transcripts_dir() == target
        assert target.is_dir()  # created on demand, not required to pre-exist
    finally:
        transcripts.set_transcripts_dir(None)
    assert transcripts.transcripts_dir() == transcripts.default_transcripts_dir()


def test_transcripts_dir_falls_back_when_unwritable(tmp_path):
    """An unplugged external disk must not lose transcripts — nor silently
    erase the user's choice, so the override survives the fallback."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    try:
        transcripts.set_transcripts_dir(blocker / "inside")
        assert transcripts.transcripts_dir() == transcripts.default_transcripts_dir()
        assert transcripts._override is not None  # choice remembered, not reset
    finally:
        transcripts.set_transcripts_dir(None)


def test_transcript_file_writes_into_chosen_folder(tmp_path):
    target = tmp_path / "chosen"
    try:
        transcripts.set_transcripts_dir(target)
        tf = transcripts.TranscriptFile()
        path = tf.open()
        tf.append("привіт")
        tf.close()
        assert path.parent == target
        assert "привіт" in path.read_text(encoding="utf-8")
    finally:
        transcripts.set_transcripts_dir(None)
