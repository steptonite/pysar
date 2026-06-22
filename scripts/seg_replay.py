#!/usr/bin/env python3
"""Dev tool: replay a saved WAV through the Segmenter to see how streaming mode
would cut it, and transcribe each segment vs the whole clip.

Usage:
    python scripts/seg_replay.py [WAV ...] [--mode uk] [--pause 0.6] [--min 0.8] [--max 12]

With no WAV args it picks the newest recordings in the app's recordings folder.
Lets us tune PAUSE_SEC / MIN_SEG_SEC against real audio before a live test.
"""

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cream_typer.config import (
    CHUNK_SIZE,
    MAX_SEG_SEC,
    MIN_SEG_SEC,
    PAUSE_SEC,
    SAMPLE_RATE,
    SILENCE_MARGIN,
)
from cream_typer.recorder import pcm_to_wav
from cream_typer.segmenter import Segmenter
from cream_typer.transcriber import is_alive, transcribe

REC_DIR = Path.home() / "Library" / "Application Support" / "Cream Typer" / "recordings"


def load_wav_float32(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        n, sw, ch = wf.getnframes(), wf.getsampwidth(), wf.getnchannels()
        raw = wf.readframes(n)
    if sw != 2:
        raise SystemExit(f"{path.name}: expected 16-bit PCM, got sampwidth={sw}")
    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    return data


def run(path: Path, mode: str, pause: float, min_seg: float, max_seg: float, do_tx: bool):
    audio = load_wav_float32(path)
    dur = len(audio) / SAMPLE_RATE
    seg = Segmenter(SAMPLE_RATE, CHUNK_SIZE, pause, min_seg, max_seg, SILENCE_MARGIN)

    segments: list[bytes] = []
    for i in range(0, len(audio), CHUNK_SIZE):
        block = audio[i : i + CHUNK_SIZE]
        out = seg.feed(block)
        if out is not None:
            segments.append(out)
    tail = seg.flush()
    if tail:
        segments.append(tail)

    print(f"\n=== {path.name}  ({dur:.1f}s, {len(segments)} segment(s)) ===")
    for j, s in enumerate(segments, 1):
        seg_dur = len(np.frombuffer(s, dtype=np.float32)) / SAMPLE_RATE
        line = f"  [{j}] {seg_dur:5.1f}s"
        if do_tx:
            text, err = transcribe(pcm_to_wav(s), mode=mode)
            line += f"  → {text if text else ('⚠️ ' + (err or 'silence'))}"
        print(line)

    if do_tx:
        full, err = transcribe(pcm_to_wav(audio.astype(np.float32).tobytes()), mode=mode)
        print(f"  FULL-CLIP: {full if full else ('⚠️ ' + (err or 'silence'))}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wavs", nargs="*", type=Path)
    ap.add_argument("--mode", default="uk")
    ap.add_argument("--pause", type=float, default=PAUSE_SEC)
    ap.add_argument("--min", type=float, default=MIN_SEG_SEC)
    ap.add_argument("--max", type=float, default=MAX_SEG_SEC)
    ap.add_argument("--n", type=int, default=10, help="how many newest recordings if none given")
    a = ap.parse_args()

    wavs = a.wavs
    if not wavs:
        wavs = sorted(REC_DIR.glob("*.wav"), key=lambda p: p.stat().st_mtime, reverse=True)[: a.n]
    if not wavs:
        raise SystemExit("no WAVs found")

    do_tx = is_alive()
    if not do_tx:
        print("⚠️ whisper not running — segmentation only (no transcription)")

    for w in wavs:
        run(w, a.mode, a.pause, a.min, a.max, do_tx)


if __name__ == "__main__":
    main()
