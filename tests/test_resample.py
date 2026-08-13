"""The 48→16 kHz decimator: right length, no aliasing, no block-boundary seams."""

import numpy as np

from pysar.config import CAPTURE_RATE, DECIMATION, SAMPLE_RATE
from pysar.resample import Downsampler


def _tone(freq: float, n: int, rate: int = CAPTURE_RATE) -> np.ndarray:
    t = np.arange(n, dtype=np.float32) / rate
    return np.sin(2 * np.pi * freq * t).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


def test_length_is_input_over_decimation():
    d = Downsampler()
    out = d.process(np.zeros(3072, dtype=np.float32))
    assert out.size == 3072 // DECIMATION
    assert out.dtype == np.float32


def test_speech_band_passes_through():
    """A 1 kHz tone must survive at essentially full level."""
    d = Downsampler()
    out = np.concatenate([d.process(b) for b in np.split(_tone(1000, 48000), 100)])
    # Skip the filter's warm-up region before measuring.
    assert _rms(out[200:]) > 0.65  # sine RMS is 0.707


def test_out_of_band_tone_is_suppressed():
    """10 kHz is above the 8 kHz output Nyquist — without the anti-alias filter
    it would fold back to 6 kHz at full level. It must be crushed instead."""
    d = Downsampler()
    out = np.concatenate([d.process(b) for b in np.split(_tone(10000, 48000), 100)])
    assert _rms(out[200:]) < 0.01  # < -37 dB vs the 0.707 it would fold in at


def test_blocked_matches_single_pass():
    """Streaming in blocks must equal one big call — i.e. filter state really
    carries across boundaries (a seam here is an audible click every block)."""
    sig = np.random.default_rng(0).standard_normal(9216).astype(np.float32) * 0.1
    whole = Downsampler().process(sig)
    d = Downsampler()
    blocked = np.concatenate([d.process(b) for b in np.split(sig, 3)])
    assert np.allclose(whole, blocked, atol=1e-6)


def test_odd_block_sizes_keep_phase():
    """Short/ragged reads must not shift the decimation phase of the stream."""
    sig = np.random.default_rng(1).standard_normal(3000).astype(np.float32) * 0.1
    whole = Downsampler().process(sig)
    d = Downsampler()
    ragged = np.concatenate([d.process(sig[a:b]) for a, b in ((0, 700), (700, 1601), (1601, 3000))])
    assert ragged.size == whole.size
    assert np.allclose(whole, ragged, atol=1e-6)


def test_rates_line_up():
    assert CAPTURE_RATE == SAMPLE_RATE * DECIMATION


def test_group_delay_is_a_whole_output_sample():
    """Measured 12.08.2026 against ffmpeg's resampler on a 48 kHz test signal:
    0.008% RMS error with an odd tap count, 17% with an even one — the gap is
    pure sub-sample timing smear from a half-sample group delay."""
    from pysar.resample import GROUP_DELAY

    assert GROUP_DELAY % DECIMATION == 0
