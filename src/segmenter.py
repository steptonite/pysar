"""Pause-based audio segmentation for streaming dictation.

Pure logic, no audio library and no I/O — it is fed normalized float32 audio
blocks and decides where one spoken segment ends and the next begins, so each
segment can be transcribed independently *while the user keeps talking*.

A segment is cut on a natural pause (trailing silence) once enough voiced audio
has accumulated — never mid-word. A hard `max_seg_sec` cap force-emits a run-on
monologue that never pauses (the only place a cut may not land on a sentence
boundary; a rare fallback).

The "is this block speech or silence" decision uses an **adaptive** RMS noise
floor that tracks the quietest level seen (fast down, slow leak up), so it works
on a hot built-in mic and a quiet external one alike without a hard-coded
threshold. Test it with synthetic block sequences — see tests/test_segmenter.py.
"""

import numpy as np


class Segmenter:
    # Absolute RMS floor so a near-silent (normalized) noise bed never reads as
    # speech even when the adaptive floor collapses toward zero.
    _ABS_MIN = 0.003
    # Slow upward leak of the noise floor (per block) — lets it recover if the
    # room gets louder, without chasing speech peaks.
    _LEAK = 0.002
    # Trailing buffer shorter than this at stop is dropped — it's the residue
    # after the last real cut (a click/breath blip), not a final utterance, and
    # transcribes to nothing (a wasted whisper call).
    _MIN_FLUSH_SEC = 0.25

    def __init__(
        self,
        sample_rate: int,
        block_size: int,
        pause_sec: float,
        min_seg_sec: float,
        max_seg_sec: float,
        silence_margin: float,
        soft_seg_sec: float | None = None,
        micro_pause_sec: float = 0.3,
    ):
        self._block_dur = block_size / sample_rate
        self._pause_sec = pause_sec
        self._min_seg_sec = min_seg_sec
        self._max_seg_sec = max_seg_sec
        self._margin = silence_margin
        self._soft_seg_sec = soft_seg_sec
        self._micro_pause_sec = micro_pause_sec

        self._buf: list[np.ndarray] = []  # blocks of the segment being built
        self._voiced_sec = 0.0  # voiced audio accumulated in _buf
        self._silence_sec = 0.0  # trailing silence run length
        self._noise: float | None = None  # adaptive noise-floor RMS
        self._buffering = False  # True once leading silence has been dropped

    def feed(self, block: np.ndarray) -> bytes | None:
        """Consume one audio block; return a finished segment's raw float32 bytes
        when a boundary is hit, else None."""
        rms = float(np.sqrt(np.mean(np.square(block)))) if block.size else 0.0

        # Adaptive noise floor: track the quietest level fast, leak up slowly.
        if self._noise is None or rms < self._noise:
            self._noise = rms
        else:
            self._noise += self._LEAK * (rms - self._noise)

        threshold = max((self._noise or 0.0) * self._margin, self._ABS_MIN)
        voiced = rms > threshold

        if not self._buffering:
            # Drop leading silence so a segment starts on speech. Use the absolute
            # floor (not the adaptive margin) to decide there's *signal at all*:
            # if the user starts talking immediately the noise floor seeds to the
            # speech level, so a margin test would never trip — but a run-on
            # monologue still must buffer and hit the MAX_SEG cap.
            if rms > self._ABS_MIN:
                self._buffering = True
                self._buf.append(block)
                self._voiced_sec = self._block_dur if voiced else 0.0
                self._silence_sec = 0.0
            return None

        self._buf.append(block)
        if voiced:
            self._voiced_sec += self._block_dur
            self._silence_sec = 0.0
        else:
            self._silence_sec += self._block_dur

        buffered_sec = len(self._buf) * self._block_dur
        ended_on_pause = (
            self._silence_sec >= self._pause_sec and self._voiced_sec >= self._min_seg_sec
        )
        # Past the soft cap, a much shorter micro-pause is enough to cut, so a long
        # run-on lands the boundary in a between-word gap instead of being force-cut
        # mid-word when it finally hits the hard MAX_SEG_SEC cap.
        ended_on_soft = (
            self._soft_seg_sec is not None
            and buffered_sec >= self._soft_seg_sec
            and self._silence_sec >= self._micro_pause_sec
            and self._voiced_sec >= self._min_seg_sec
        )
        if ended_on_pause or ended_on_soft or buffered_sec >= self._max_seg_sec:
            return self._emit()
        return None

    def flush(self) -> bytes | None:
        """Return the trailing buffered segment at stop (or None). Buffering only
        starts once there's real signal, so any sizeable buffer holds speech worth
        transcribing — even if the cold-start noise floor never classified it as
        'voiced'. A sub-_MIN_FLUSH_SEC scrap (click/breath after the last cut) is
        dropped: it transcribes to nothing and just wastes a whisper call."""
        if self._buf and len(self._buf) * self._block_dur >= self._MIN_FLUSH_SEC:
            return self._emit()
        self._reset()
        return None

    def _emit(self) -> bytes:
        data = np.concatenate(self._buf).astype(np.float32)
        self._reset()
        return data.tobytes()

    def _reset(self) -> None:
        self._buf = []
        self._voiced_sec = 0.0
        self._silence_sec = 0.0
        self._buffering = False
