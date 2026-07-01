"""Segmenter boundary logic — pure, no audio hardware."""

import numpy as np

from pysar.segmenter import Segmenter

SR = 16000
BLOCK = 1600  # 0.1 s per block → easy arithmetic


def _seg(pause=0.3, min_seg=0.2, max_seg=1.0, margin=4.0):
    return Segmenter(SR, BLOCK, pause, min_seg, max_seg, margin)


def _voiced(n=1, amp=0.1):
    return [np.full(BLOCK, amp, dtype=np.float32) for _ in range(n)]


def _silence(n=1):
    return [np.full(BLOCK, 1e-4, dtype=np.float32) for _ in range(n)]


def _feed(seg, blocks):
    return [out for b in blocks for out in [seg.feed(b)] if out is not None]


def test_emits_on_pause():
    seg = _seg()
    emitted = _feed(seg, _silence(2) + _voiced(5) + _silence(4))
    assert len(emitted) == 1
    # Leading silence dropped; cut fires once the pause reaches 0.3 s (3 blocks),
    # so the segment is the 5 voiced blocks + those 3 trailing-silence blocks.
    samples = np.frombuffer(emitted[0], dtype=np.float32)
    assert samples.size == BLOCK * (5 + 3)


def test_no_emit_below_min_seg():
    seg = _seg()
    # One voiced block (0.1 s) is under min_seg (0.2 s): a pause must NOT cut it.
    out = _feed(seg, _voiced(1) + _silence(6))
    assert out == []


def test_force_emit_at_max_seg():
    seg = _seg(pause=0.3, min_seg=0.2, max_seg=1.0)
    # Continuous speech, no pause: the 1.0 s cap force-emits at block 10. Buffering
    # starts on signal alone, so a run-on monologue is still cut even with no pause.
    emitted = _feed(seg, _voiced(15))
    assert len(emitted) >= 1
    first = np.frombuffer(emitted[0], dtype=np.float32)
    assert first.size == BLOCK * 10  # max_seg / block_dur


def test_flush_returns_trailing():
    seg = _seg()
    assert _feed(seg, _voiced(3)) == []  # no pause yet → nothing emitted
    tail = seg.flush()
    assert tail is not None
    assert np.frombuffer(tail, dtype=np.float32).size == BLOCK * 3


def test_flush_empty_is_none():
    seg = _seg()
    _feed(seg, _silence(5))  # never any speech
    assert seg.flush() is None


def test_leading_silence_dropped():
    seg = _seg()
    emitted = _feed(seg, _silence(10) + _voiced(4) + _silence(4))
    assert len(emitted) == 1
    # 10 blocks of leading silence are gone; only voiced + the 0.3 s pause remain.
    assert np.frombuffer(emitted[0], dtype=np.float32).size == BLOCK * (4 + 3)


def test_multiple_segments():
    seg = _seg()
    # Leading silence calibrates the noise floor (as in a real recording: tap the
    # hotkey, then speak), so both utterances are detected and cut on their pause.
    blocks = _silence(2) + _voiced(4) + _silence(4) + _voiced(4) + _silence(4)
    emitted = _feed(seg, blocks)
    assert len(emitted) == 2


def test_soft_cap_cuts_on_micro_pause():
    # Past the soft cap a micro-pause (shorter than the full pause) cuts, so a
    # run-on lands in a word gap instead of mid-word at the hard cap.
    seg = Segmenter(
        SR,
        BLOCK,
        pause_sec=0.5,
        min_seg_sec=0.2,
        max_seg_sec=2.0,
        silence_margin=4.0,
        soft_seg_sec=0.6,
        micro_pause_sec=0.1,
    )
    # Leading silence calibrates the noise floor (as in a real recording), then
    # 8 voiced (0.8s > soft 0.6) + 1 silence (0.1s micro-pause < full 0.5s) → cut.
    emitted = _feed(seg, _silence(3) + _voiced(8) + _silence(1))
    assert len(emitted) == 1
    first = np.frombuffer(emitted[0], dtype=np.float32)
    assert first.size == BLOCK * 9  # leading silence dropped; 8 voiced + 1 silence
