"""Longest unbroken digital silence in the recovery file (26.08.2026).

The webinar dump was 98 minutes long and reported as a success while half of it
was zeros. The end-of-meeting verdict now rests on the number these tests pin, so
the counting has to be right at the buffer seams — that is exactly where a naive
per-buffer tally turns one 50-minute hole into thousands of harmless small ones.
"""

import numpy as np

from pysar.syscap import SAMPLE_RATE, _RawDump


def _dump(tmp_path):
    return _RawDump(tmp_path / "cap-sys.wav")


def _sound(n=100):
    return np.full(n, 0.2, dtype=np.float32)


def _silence(n=100):
    return np.zeros(n, dtype=np.float32)


def test_silence_runs_are_joined_across_buffers(tmp_path):
    d = _dump(tmp_path)
    d.write(_sound(50))
    for _ in range(10):
        d.write(_silence(100))
    d.close()
    assert d.max_silent_run == 1000  # not 100


def test_sound_between_silences_breaks_the_run(tmp_path):
    d = _dump(tmp_path)
    for _ in range(5):
        d.write(_silence(100))
        d.write(_sound(10))
    d.close()
    # Five separate 100-frame gaps, not one 500-frame one: normal turn-taking
    # must never add up into something that looks like a dead tap.
    assert d.max_silent_run == 100


def test_gap_inside_a_single_buffer_is_counted(tmp_path):
    d = _dump(tmp_path)
    x = np.zeros(300, dtype=np.float32)
    x[0] = 0.5
    x[299] = 0.5  # 298 zeros strictly between two samples
    d.write(x)
    d.close()
    assert d.max_silent_run == 298


def test_trailing_silence_counts_even_though_nothing_follows_it(tmp_path):
    """The failure mode ends the file: silence to the last frame, no sound after
    it to close the run. Reading only completed runs would report zero."""
    d = _dump(tmp_path)
    d.write(_sound(10))
    d.write(_silence(700))
    d.close()
    assert d.max_silent_run == 700


def test_a_healthy_capture_reports_a_small_run(tmp_path):
    d = _dump(tmp_path)
    for _ in range(20):
        d.write(_sound(100))
    d.close()
    assert d.max_silent_run == 0


def test_run_is_reported_in_seconds_by_the_recorder(tmp_path):
    d = _dump(tmp_path)
    d.write(_sound(10))
    d.write(_silence(SAMPLE_RATE * 3))
    d.close()
    assert abs(d.max_silent_run / float(SAMPLE_RATE) - 3.0) < 0.01
