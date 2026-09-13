"""Що з Писаря псує звук ЖИВОГО дзвінка — замір по кроках, без здогадів.

13.09.2026: у дзвінку Telegram обидва кінці почули одне одного тихо, поки
писалась зустріч. Невідомо, хто винен: мікрофон через ScreenCaptureKit,
мікрофон через VPIO, захоплення системного звуку чи сам дзвінок. Тому
вмикаємо шляхи захоплення ПО ОДНОМУ посеред того самого дзвінка, і на
кожному кроці людина каже, як чути (1 добре · 2 тихіше · 3 погано), а скрипт
пише стан звукових пристроїв: частоти, гучності, хто тримає мік і динаміки.

Аудіо не пишеться нікуди. Звіт — ~/Desktop/pysar-звук-дзвінка.txt.
"""

from __future__ import annotations

import ctypes
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.syscap import SystemAudioRecorder

STEP_SEC = 25
REPORT = Path.home() / "Desktop" / "pysar-звук-дзвінка.txt"

_CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")


class _Addr(ctypes.Structure):
    _fields_ = [("sel", ctypes.c_uint32), ("scope", ctypes.c_uint32), ("elem", ctypes.c_uint32)]


def _cc(s: str) -> int:
    return int.from_bytes(s.encode(), "big")


def _get(obj: int, sel: str, ctype, scope: str = "glob"):
    addr = _Addr(_cc(sel), _cc(scope), 0)
    val = ctype()
    size = ctypes.c_uint32(ctypes.sizeof(val))
    err = _CA.AudioObjectGetPropertyData(
        ctypes.c_uint32(obj), ctypes.byref(addr), 0, None, ctypes.byref(size), ctypes.byref(val)
    )
    return None if err else val.value


def _processes() -> tuple[list[str], list[str]]:
    """Хто зараз реально тримає вхід і вихід звуку (macOS 14+)."""
    addr = _Addr(_cc("prs#"), _cc("glob"), 0)
    size = ctypes.c_uint32(0)
    if _CA.AudioObjectGetPropertyDataSize(1, ctypes.byref(addr), 0, None, ctypes.byref(size)):
        return [], []
    n = size.value // 4
    arr = (ctypes.c_uint32 * n)()
    if _CA.AudioObjectGetPropertyData(1, ctypes.byref(addr), 0, None, ctypes.byref(size), arr):
        return [], []
    ins, outs = [], []
    for obj in arr:
        pid = _get(obj, "ppid", ctypes.c_int32)
        if not pid:
            continue
        name = subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True)
        name = Path(name.stdout.strip()).name or str(pid)
        if _get(obj, "piri", ctypes.c_uint32):
            ins.append(name)
        if _get(obj, "piro", ctypes.c_uint32):
            outs.append(name)
    return sorted(set(ins)), sorted(set(outs))


def snapshot() -> str:
    din = _get(1, "dIn ", ctypes.c_uint32)
    dout = _get(1, "dOut", ctypes.c_uint32)
    sr_in = _get(din, "nsrt", ctypes.c_double) if din else None
    sr_out = _get(dout, "nsrt", ctypes.c_double) if dout else None
    vol = subprocess.run(
        ["osascript", "-e", "get volume settings"], capture_output=True, text=True
    ).stdout.strip()
    ins, outs = _processes()
    return (
        f"мік {sr_in and int(sr_in)} Гц · динаміки {sr_out and int(sr_out)} Гц · {vol}\n"
        f"      тримають мік: {', '.join(ins) or '—'}\n"
        f"      грають звук:  {', '.join(outs) or '—'}"
    )


STEPS = [
    ("A", "Писар НІЧОГО не пише (еталон)", None),
    ("B", "тільки системний звук", dict(capture_mic=False)),
    (
        "C",
        "системний звук + мікрофон через ScreenCaptureKit (як зараз)",
        dict(capture_mic=True, mic_aec=False),
    ),
    (
        "D",
        "системний звук + мікрофон через VPIO (як було до 13.09)",
        dict(capture_mic=True, mic_aec=True),
    ),
    ("E", "знову НІЧОГО — чи відпускає після зупинки", None),
]


def ask(prompt: str) -> str:
    while True:
        a = input(prompt).strip()
        if a in ("1", "2", "3"):
            return {"1": "добре", "2": "тихіше", "3": "погано"}[a]
        print("   натисни 1, 2 або 3 і Enter")


def main() -> None:
    lines = [f"Замір звуку дзвінка · {datetime.now():%d.%m.%Y %H:%M}", ""]
    print("Будь у дзвінку (Telegram/Zoom). У самому Писарі запис НЕ вмикай.")
    print("На кожному кроці говоріть одне з одним ~25 секунд, потім скажеш, як чути.\n")
    input("Готовий — натисни Enter… ")
    for key, title, kw in STEPS:
        print(f"\n━━ Крок {key}: {title}")
        lines.append(f"━━ Крок {key}: {title}")
        lines.append(f"   до:    {snapshot()}")
        rec, errors = None, []
        if kw is not None:
            try:
                rec = SystemAudioRecorder(**kw)
                rec.start(on_segment=lambda *a: None, on_error=errors.append)
            except Exception as e:
                errors.append(f"старт: {e}")
        during: list[str] = []

        def probe(stop: threading.Event, out: list[str]) -> None:
            while not stop.wait(5.0):
                out.append(snapshot())

        stop = threading.Event()
        threading.Thread(target=probe, args=(stop, during), daemon=True).start()
        for left in range(STEP_SEC, 0, -1):
            print(f"\r   говоріть… {left:2d} с ", end="", flush=True)
            time.sleep(1)
        print()
        me = ask("   Як ТИ чуєш співрозмовника? 1 добре · 2 тихіше · 3 погано: ")
        them = ask("   Як ТЕБЕ чує співрозмовник (спитай)? 1 · 2 · 3: ")
        stop.set()
        lines.append(f"   під час: {during[-1] if during else snapshot()}")
        if rec is not None:
            try:
                rec.stop()
            except Exception as e:
                errors.append(f"стоп: {e}")
        time.sleep(2)
        lines.append(f"   після: {snapshot()}")
        lines.append(f"   ✅ я чую: {me} · мене чують: {them}")
        if errors:
            lines.append(f"   помилки: {'; '.join(map(str, errors))[:300]}")
        lines.append("")
        REPORT.write_text("\n".join(lines))
    print(f"\nЗвіт: {REPORT}")


if __name__ == "__main__":
    main()
