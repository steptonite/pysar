"""Самотест: чи глушить Писар звук дзвінка — без співрозмовника, приладом.

13.09.2026. «Дзвінок» імітують два сторонні процеси afplay: тон 6 кГц — це
голос співрозмовника з динаміків, синтезована мова — це «я говорю в кімнаті».
Окремий процес читає мікрофон напряму через HAL, як це робить Telegram/Zoom.
По черзі вмикаємо шляхи захоплення Писаря і міряємо:
  • динаміки: рівень тону в мікрофоні (упав ⇒ Писар приглушив чужий звук);
  • мік: рівень мови мінус рівень тону (упав ⇒ дзвінок отримує тихіший мік).
Звук грає вголос. Аудіо нікуди не пишеться, тільки числа.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TONE_HZ = 6000.0
PHASE_SEC = 12
SETTLE_SEC = 4


def meter() -> None:
    import sounddevice as sd

    sr = int(sd.query_devices(kind="input")["default_samplerate"])
    blk = sr // 10
    freqs = np.fft.rfftfreq(blk, 1 / sr)
    band = (freqs > 300) & (freqs < 3000)
    tone = np.abs(freqs - TONE_HZ) < 30
    win = np.hanning(blk)

    def cb(data, frames, t, status):
        spec = np.abs(np.fft.rfft(data[:, 0] * win))
        print(
            json.dumps(
                {
                    "t": time.time(),
                    "tone": float(np.sqrt((spec[tone] ** 2).sum()) + 1e-9),
                    "speech": float(np.sqrt((spec[band] ** 2).sum()) + 1e-9),
                }
            ),
            flush=True,
        )

    with sd.InputStream(samplerate=sr, blocksize=blk, channels=1, callback=cb):
        while True:
            time.sleep(1)


def _db(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


def main() -> None:
    from src.syscap import SystemAudioRecorder
    from tools.call_audio_test import snapshot

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp")
    work.mkdir(parents=True, exist_ok=True)
    total = PHASE_SEC * 5 + 10
    sr = 44100
    t = np.arange(int(sr * total)) / sr
    pcm = (0.15 * np.sin(2 * np.pi * TONE_HZ * t) * 32767).astype(np.int16)
    tone_wav = work / "tone.wav"
    with wave.open(str(tone_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    speech = work / "speech.aiff"
    text = "Перевіряємо, чи чути мене в дзвінку, поки Писар записує зустріч. " * 40
    subprocess.run(["say", "-o", str(speech), text], check=True)

    rows: list[dict] = []
    m = subprocess.Popen([sys.executable, __file__, "--meter"], stdout=subprocess.PIPE, text=True)
    threading.Thread(
        target=lambda: [rows.append(json.loads(ln)) for ln in m.stdout], daemon=True
    ).start()
    time.sleep(2)
    players = [
        subprocess.Popen(["afplay", str(tone_wav)]),
        subprocess.Popen(["afplay", str(speech)]),
    ]
    time.sleep(2)

    steps = [
        ("A", "нічого", None),
        ("B", "системний звук", dict(capture_mic=False)),
        ("C", "сист.+мік SCK (зараз)", dict(capture_mic=True, mic_aec=False)),
        ("D", "сист.+мік VPIO (до 13.09)", dict(capture_mic=True, mic_aec=True)),
        ("E", "нічого після", None),
    ]
    out = []
    for key, title, kw in steps:
        errs: list = []
        rec = None
        if kw is not None:
            try:
                rec = SystemAudioRecorder(**kw)
                rec.start(on_segment=lambda *a: None, on_error=errs.append)
            except Exception as e:
                errs.append(f"старт: {e}")
        t0 = time.time()
        time.sleep(PHASE_SEC / 2)
        snap = snapshot()
        time.sleep(PHASE_SEC / 2)
        win = [r for r in rows if t0 + SETTLE_SEC <= r["t"] <= t0 + PHASE_SEC]
        if rec is not None:
            try:
                rec.stop()
            except Exception as e:
                errs.append(f"стоп: {e}")
        tone = _db(float(np.median([r["tone"] for r in win]))) if win else float("nan")
        sp = _db(float(np.median([r["speech"] for r in win]))) if win else float("nan")
        out.append((key, title, tone, sp, snap, errs))
        time.sleep(2)

    for p in players:
        p.kill()
    m.kill()
    base_tone, base_sp = out[0][2], out[0][3]
    print(f"{'крок':4} {'що':28} {'динаміки Δ дБ':>14} {'мік Δ дБ':>10}")
    for key, title, tone, sp, snap, errs in out:
        dt = tone - base_tone
        dm = (sp - base_sp) - dt
        print(f"{key:4} {title:28} {dt:+14.1f} {dm:+10.1f}")
        print("      " + snap.replace("\n", "\n      "))
        if errs:
            print("      помилки:", "; ".join(map(str, errs))[:300])


if __name__ == "__main__":
    if "--meter" in sys.argv:
        meter()
    else:
        main()
