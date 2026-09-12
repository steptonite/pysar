"""Ловля обриву системного захоплення (-3817) — по кроках, з журналом.

🔴 12.09.2026. У Каті запис зустрічі рвався на шматки, у логах — SCStreamError
-3817. Причина НЕ знайдена, є два підозрюваних: (1) `ZoomAudioDevice`, який Zoom
підсовує системі у момент входу в конференцію, і (2) сон/блокування екрана.
Сторож тепла тут ні до чого: він гейтить лише розділення спікерів і чергу
файлів, живого запису не торкається (`thermal.gate()` не викликається ні в
`syscap`, ні в шляху зустрічі).

Скрипт відтворює підозрюваних по черзі: піднімає ТЕ САМЕ захоплення, що й Писар,
і після кожної дії каже, живий потік чи вмер і з якою помилкою. Аудіо нікуди не
пишеться — лише події і час.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.syscap import SystemAudioRecorder

LOG = Path.home() / "Desktop" / "pysar-обрив-запису.txt"

STEPS: list[tuple[str, int]] = [
    ("НІЧОГО не роби — міряю спокійний фон", 20),
    ("Зайди в конференцію Zoom (саме вхід, не запуск застосунку)", 45),
    ("Перемкни вихід звуку: динаміки ↔ навушники (або Zoom-пристрій)", 30),
    ("Заблокуй екран ⌃⌘Q і за 15 секунд розблокуй", 45),
    ("Закрий кришку на 15 секунд і відкрий (або дай екрану заснути)", 45),
    ("Під'єднай або від'єднай зовнішній монітор — чи зміни роздільність", 30),
]


def main() -> None:
    lines: list[str] = [f"Перевірка обриву запису · {datetime.now():%d.%m.%Y %H:%M}"]

    def say(text: str) -> None:
        print(text, flush=True)
        lines.append(text)

    errors: list[str] = []
    rec = SystemAudioRecorder(capture_mic=False)
    rec.start(on_segment=lambda *a: None, on_error=errors.append)
    time.sleep(3.0)
    if errors:
        say(f"🔴 захоплення не стартувало: {errors[-1]}")
        say("Дай Писарю дозвіл «Запис екрана» в Системних параметрах і запусти ще раз.")
        LOG.write_text("\n".join(lines) + "\n")
        return

    say("✅ захоплення пішло. Далі — шість кроків; після кожного скажу, чи живе.\n")
    broke: list[str] = []
    for i, (what, secs) in enumerate(STEPS, 1):
        seen = len(errors)
        say(f"── Крок {i}/{len(STEPS)}: {what}")
        say(f"   (маю {secs} с; просто роби, я рахую)")
        for _ in range(secs):
            time.sleep(1.0)
        gap = rec.seconds_since_audio()
        new = errors[seen:]
        if new:
            broke.append(f"крок {i} ({what}): {new[0]}")
            say(f"   🔴 ПОТІК ВМЕР: {new[0]}")
            say("   Піднімаю назад, щоб перевірити решту кроків…")
            rec.stop()
            time.sleep(1.0)
            rec = SystemAudioRecorder(capture_mic=False)
            rec.start(on_segment=lambda *a: None, on_error=errors.append)
            time.sleep(2.0)
        elif gap is None or gap > 8.0:
            broke.append(f"крок {i} ({what}): звук перестав приходити (тиша {gap})")
            say(f"   🟠 помилки нема, але звук не йде: {gap}")
        else:
            say(f"   ✅ живий (останній звук {gap:.1f} с тому)")
        say("")

    rec.stop()
    if broke:
        say("🔴 ВИНУВАТЕЦЬ ЗНАЙДЕНИЙ:")
        for b in broke:
            say(f"   • {b}")
    else:
        say("✅ жоден із підозрюваних потік не вбив — тримай Писар довше або")
        say("   повтори під час справжньої зустрічі: обрив може чіплятись за час, не за дію.")
    say(f"\nЖурнал: {LOG}")
    LOG.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
