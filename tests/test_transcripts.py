"""TranscriptFile speaker-source grouping — pure string I/O, no filesystem."""

import io

from pysar.transcripts import TranscriptFile


def _tf():
    tf = TranscriptFile()
    tf._fh = io.StringIO()  # bypass open(); append() only needs a writable handle
    tf.set_source_labels({"sys": "System", "mic": "You"})
    return tf


def test_no_source_writes_plain_text():
    tf = _tf()
    tf.append("hello")
    tf.append("world")
    assert tf._fh.getvalue() == "hello\n\nworld\n\n"  # no labels when source is None


def test_label_emitted_once_per_speaker_run():
    tf = _tf()
    tf.append("a", source="sys")
    tf.append("b", source="sys")  # same speaker → no repeated label
    tf.append("c", source="mic")  # speaker change → new label
    out = tf._fh.getvalue()
    assert out == "**System**\n\na\n\nb\n\n**You**\n\nc\n\n"


def test_label_reappears_when_speaker_returns():
    tf = _tf()
    tf.append("a", source="sys")
    tf.append("b", source="mic")
    tf.append("c", source="sys")  # back to sys after mic → label again
    assert tf._fh.getvalue().count("**System**") == 2
