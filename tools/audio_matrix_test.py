"""Замір аудіо-шляхів зустрічі: хто тримає мікрофон, що робить Voice Processing IO.

Три питання, на які код відповісти не може — тільки живий мак:

  A. Чи відкривається мікрофон, коли його вже тримає інший застосунок (Zoom).
     Міряємо ТРИ шляхи: наш поточний (ScreenCaptureKit), чистий AVAudioEngine,
     і AVAudioEngine з апаратним AEC (Voice Processing IO).
  B. Чи VPIO не вбиває СИСТЕМНУ доріжку. Сторонні, що це пройшли, ловили
     системний звук на ~-51 dB, бо VPIO дакає інші джерела. Системна доріжка —
     наш головний сигнал, тому це стоп-фактор, а не косметика.
  C. Чи VPIO реально знімає ехо динаміків. Потребує ДИНАМІКІВ: у навушниках
     мікрофон ехо не чує, і замір нічого не означає. Фаза сама себе пропустить.

Фази A і B коректні й у навушниках. Запускати двічі: без Zoom і з Zoom у зустрічі.
"""

import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SPEAK = ["say", "-r", "170", "раз два три чотири п'ять шість сім вісім дев'ять десять"]
TMP = Path("/tmp/pysar-audio-matrix")


def db(x: float) -> float:
    return 20 * np.log10(max(float(x), 1e-9))


def rms_of(paths: list[Path]) -> float:
    """RMS усього, що записала доріжка (WAV 16 кГц моно від syscap)."""
    acc: list[np.ndarray] = []
    for p in paths:
        if not p.exists():
            continue
        with wave.open(str(p)) as w:
            raw = w.readframes(w.getnframes())
        a = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
        if a.size:
            acc.append(a)
    if not acc:
        return 0.0
    a = np.concatenate(acc)
    return float(np.sqrt(np.mean(a**2)))


def default_output() -> str:
    out = subprocess.run(
        ["system_profiler", "SPAudioDataType"], capture_output=True, text=True
    ).stdout
    name, block = "?", []
    for line in out.splitlines():
        s = line.strip()
        if s.endswith(":") and not s.startswith(("Default", "Output", "Input", "Manufacturer")):
            block = [s[:-1]]
        if "Default Output Device: Yes" in s and block:
            name = block[0]
    return name


def mic_holders() -> list[str]:
    """Хто зараз тримає мікрофон (за відкритими дескрипторами CoreAudio)."""
    out = subprocess.run(
        [
            "lsof",
            "-c",
            "zoom",
            "-c",
            "Zoom",
            "-c",
            "Google Chrome",
            "-c",
            "Safari",
            "-c",
            "Slack",
            "-c",
            "Teams",
            "-c",
            "Pysar",
            "-c",
            "FaceTime",
        ],
        capture_output=True,
        text=True,
    ).stdout
    apps = {
        line.split()[0]
        for line in out.splitlines()
        if "AppleHDA" in line or "AudioDriver" in line or "coreaudio" in line.lower()
    }
    return sorted(apps)


# ── A. мікрофон трьома шляхами ────────────────────────────────────────────────
def probe_sck_mic() -> str:
    """Наш поточний шлях: мік через ScreenCaptureKit."""
    from src import syscap

    if not syscap.AVAILABLE:
        return "🔴 ScreenCaptureKit недоступний (нема pyobjc-біндингів)"
    got: list[tuple] = []
    err: list[str] = []
    TMP.mkdir(parents=True, exist_ok=True)
    rec = syscap.SystemAudioRecorder(
        capture_mic=True, source_mode="smart", raw_dump_dir=TMP, raw_dump_stem="A-sck"
    )
    rec.start(on_segment=lambda w, s, sp: got.append((s, len(w))), on_error=err.append)
    time.sleep(1.0)
    subprocess.run(SPEAK, check=False)
    time.sleep(1.2)
    rec.stop()
    if err:
        return f"🔴 не стартував: {err[0]}"
    paths, secs = rec._dump_final
    mic = [p for p in paths if "-mic" in p.name]
    sysd = [p for p in paths if "-sys" in p.name]
    srcs = {s for s, _ in got if s}
    return (
        f"{'✅' if mic and rms_of(mic) > 0 else '🔴'} мік через SCK: "
        f"{db(rms_of(mic)):6.1f} dBFS  ·  система: {db(rms_of(sysd)):6.1f} dBFS  "
        f"·  сегментів {len(got)} {sorted(srcs) or '—'}  ·  {secs:.1f} с"
    )


def probe_engine(vpio: bool) -> str:
    """AVAudioEngine, з апаратним AEC або без."""
    import AVFoundation as AV

    eng = AV.AVAudioEngine.alloc().init()
    inp = eng.inputNode()
    if vpio:
        ok, e = inp.setVoiceProcessingEnabled_error_(True, None)
        if not ok:
            return f"🔴 VPIO не увімкнувся: {e}"
        import AVFAudio

        cfg = AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingConfiguration.alloc().init()
        cfg.setEnableAdvancedDucking_(False)
        cfg.setDuckingLevel_(AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingLevelMin)
        inp.setVoiceProcessingOtherAudioDuckingConfiguration_(cfg)
    fmt = inp.inputFormatForBus_(0)
    ch = fmt.channelCount()
    peaks: list[float] = []

    def tap(buf, when):
        n = buf.frameLength()
        if not n:
            return
        p = buf.floatChannelData()[0]
        a = np.frombuffer(memoryview(p.as_buffer(n * 4)), dtype=np.float32, count=n)
        peaks.append(float(np.sqrt(np.mean(a.astype(np.float64) ** 2))))

    inp.installTapOnBus_bufferSize_format_block_(0, 4096, fmt, tap)
    ok, e = eng.startAndReturnError_(None)
    if not ok:
        return f"🔴 мікрофон НЕ відкрився: {e}"
    time.sleep(0.8)
    subprocess.run(SPEAK, check=False)
    time.sleep(0.5)
    eng.stop()
    inp.removeTapOnBus_(0)
    if not peaks:
        return "🔴 відкрився, але БУФЕРИ НЕ ЙДУТЬ (мік зайнятий/зам'ючений)"
    loud = sorted(peaks)[len(peaks) // 2 :]
    return (
        f"✅ {db(np.mean(loud)):6.1f} dBFS  ·  каналів {ch}"
        f"{' (канал 0 — корисний)' if ch > 1 else ''}  ·  буферів {len(peaks)}"
    )


# ── B. чи VPIO не глушить системну доріжку ────────────────────────────────────
def probe_ducking() -> list[str]:
    from src import syscap

    if not syscap.AVAILABLE:
        return ["🔴 ScreenCaptureKit недоступний"]
    import AVFoundation as AV

    out = []
    for label, vpio, duck_min in (
        ("без VPIO         ", False, False),
        ("VPIO, дефолт     ", True, False),
        ("VPIO + duck .min ", True, True),
    ):
        eng = inp = None
        if vpio:
            eng = AV.AVAudioEngine.alloc().init()
            inp = eng.inputNode()
            ok, e = inp.setVoiceProcessingEnabled_error_(True, None)
            if not ok:
                out.append(f"{label}: 🔴 VPIO не увімкнувся: {e}")
                continue
            if duck_min:
                import AVFAudio

                cfg = AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingConfiguration.alloc().init()
                cfg.setEnableAdvancedDucking_(False)
                cfg.setDuckingLevel_(AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingLevelMin)
                inp.setVoiceProcessingOtherAudioDuckingConfiguration_(cfg)
            eng.startAndReturnError_(None)
        TMP.mkdir(parents=True, exist_ok=True)
        rec = syscap.SystemAudioRecorder(
            capture_mic=False,
            source_mode="off",
            raw_dump_dir=TMP,
            raw_dump_stem=f"B-{label.strip()[:4]}",
        )
        errs: list[str] = []
        rec.start(on_segment=lambda *a: None, on_error=errs.append)
        time.sleep(0.8)
        subprocess.run(SPEAK, check=False)
        time.sleep(1.0)
        rec.stop()
        if eng is not None:
            eng.stop()
        paths, _ = rec._dump_final
        lvl = db(rms_of([p for p in paths if "-sys" in p.name]))
        flag = "🔴 доріжка майже мертва" if lvl < -45 else "✅"
        out.append(
            f"{label}: системний звук {lvl:6.1f} dBFS  {flag}" + (f"  ({errs[0]})" if errs else "")
        )
    return out


def main() -> None:
    outp = default_output()
    speakers = any(k in outp.lower() for k in ("динамік", "speaker", "macbook"))
    print("═" * 78)
    print("ЗАМІР АУДІО-ШЛЯХІВ ЗУСТРІЧІ")
    print(f"вихід звуку зараз: {outp}")
    print(f"мікрофон тримають: {', '.join(mic_holders()) or 'тільки система'}")
    print("═" * 78)

    print("\n──── A. ЧИ ВІДКРИЄТЬСЯ МІКРОФОН (три шляхи) ────")
    print("   наш поточний  ", probe_sck_mic())
    print("   AVAudioEngine ", probe_engine(vpio=False))
    print("   + апаратний AEC", probe_engine(vpio=True))

    print("\n──── B. ЧИ VPIO НЕ ГЛУШИТЬ СИСТЕМНУ ДОРІЖКУ ────")
    for line in probe_ducking():
        print("  ", line)

    print("\n──── C. ЧИ AEC ЗНІМАЄ ЕХО ДИНАМІКІВ ────")
    if not speakers:
        print(f"   ⏭ ПРОПУЩЕНО: вихід — «{outp}», не вбудовані динаміки.")
        print("     У навушниках мікрофон ехо НЕ чує, і будь-яке число тут")
        print("     означало б лише прибирання шуму кімнати, а не ехо.")
        print("     Перемкнись на динаміки Мака і прогони ще раз.")
    else:
        print("   гучність мусить бути ≥50%, у кімнаті тихо.")
        a = probe_engine(vpio=False)
        b = probe_engine(vpio=True)
        print("   без AEC:", a)
        print("   з AEC  :", b)
        print("   🔑 різниця > 15 dB ⇒ AEC працює на ЧУЖИЙ процес, архітектура життєздатна.")

    print("\n" + "═" * 78)
    print("Прогнати ДВІЧІ: зараз і під час живої зустрічі в Zoom.")
    print("Порівняти блок A — чи мік відкривається, коли Zoom його тримає.")


if __name__ == "__main__":
    main()
