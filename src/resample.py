"""48 kHz → 16 kHz decimation with an anti-alias FIR. numpy only, no scipy.

The mic is captured at CAPTURE_RATE so the TTS-dataset copy keeps the full band,
but whisper.cpp wants 16 kHz — so every block is low-passed and decimated on the
way in. Dropping every 3rd sample without the filter would fold everything above
8 kHz back into the speech band (sibilants turn into a metallic wash), which is
exactly the kind of damage that would show up as worse transcripts.

Streaming, not one-shot: audio arrives in blocks, so the filter carries its tail
across block boundaries (overlap-save). Without that state each block would start
from zeros and stamp a click every 64 ms.
"""

import numpy as np

from .config import CAPTURE_RATE, DECIMATION, SAMPLE_RATE

# Windowed-sinc low-pass. Cutoff sits just under the output Nyquist (8 kHz);
# 7.6 kHz leaves the Hann transition band room to reach the stopband before
# anything can fold back. 96 taps ⇒ ~1 ms group delay at 48 kHz, inaudible and
# irrelevant to a VAD working on 64 ms blocks.
# Odd tap count on purpose: the group delay is then (_TAPS-1)/2 = 48 whole input
# samples = exactly 16 output samples, so the decimated stream is a clean integer
# shift of the input. An even count leaves half a sample of delay, which shows up
# as a sub-sample timing smear against any other resampler.
_TAPS = 97
_CUTOFF_HZ = 7600.0
# np.sinc wants the cutoff as a fraction of the SAMPLE rate (not of Nyquist) —
# normalising against Nyquist here is a silent bug: the filter passes 15 kHz and
# does nothing at all.
_FC = _CUTOFF_HZ / CAPTURE_RATE
_KERNEL = np.sinc(2 * _FC * (np.arange(_TAPS) - (_TAPS - 1) / 2)) * np.hanning(_TAPS)
_KERNEL = (_KERNEL / np.sum(_KERNEL)).astype(np.float32)  # unity gain at DC

# Group delay of a linear-phase FIR, in input samples.
GROUP_DELAY = (_TAPS - 1) // 2


class Downsampler:
    """Streaming ``CAPTURE_RATE`` → ``SAMPLE_RATE`` decimator (÷DECIMATION).

    Feed it float32 mono blocks whose length is a multiple of DECIMATION; get
    back blocks 1/DECIMATION as long. State is per-recording — call `reset()`
    (or make a new one) when a take starts.
    """

    def __init__(self) -> None:
        self._tail = np.zeros(_TAPS - 1, dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._tail = np.zeros(_TAPS - 1, dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def process(self, block: np.ndarray) -> np.ndarray:
        """Filter + decimate one block. Returns float32 at SAMPLE_RATE."""
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        if self._carry.size:
            block = np.concatenate((self._carry, block))
        # Keep the decimation phase locked to the stream, not to the block: a
        # short read must not shift which samples get kept from then on.
        usable = (block.size // DECIMATION) * DECIMATION
        self._carry = block[usable:].copy()
        block = block[:usable]
        if block.size == 0:
            return block
        padded = np.concatenate((self._tail, block))
        # Full convolution, then keep the window that lines up with `block`:
        # index (_TAPS-1)+i is the causal FIR output for input sample i, with
        # every tap fed from real audio (that's what the tail is for).
        filtered = np.convolve(padded, _KERNEL)[_TAPS - 1 : _TAPS - 1 + block.size]
        # Refresh the tail from the end of the *input* stream.
        keep = _TAPS - 1
        self._tail = (
            padded[-keep:].astype(np.float32)
            if padded.size >= keep
            else np.concatenate((np.zeros(keep - padded.size, dtype=np.float32), padded))
        )
        return np.ascontiguousarray(filtered[::DECIMATION], dtype=np.float32)


__all__ = ["CAPTURE_RATE", "GROUP_DELAY", "SAMPLE_RATE", "Downsampler"]
