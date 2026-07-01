"""TranscriptFile speaker-source + timestamp headers — pure string I/O, no filesystem."""

import io
from datetime import datetime

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
