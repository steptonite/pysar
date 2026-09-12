"""Мікрофон зустрічі через Apple Voice Processing IO (апаратний AEC).

🔴 12.09.2026. ScreenCaptureKit віддає мікрофон СИРИМ: у ньому стоїть ехо
власних динаміків, і на зустрічі без навушників кожна фраза співрозмовника
лягає в транскрипт двічі — чисто з системної доріжки і брудно з мікрофона.
Текстовий фільтр (meetingfilter) це лише прибирає ПОТІМ, і половину часу
прибирає не те: черга обробки не збігається з часом аудіо.

Архітектурна відповідь Apple — не фільтр, а Voice Processing IO: той самий
блок, на якому працює FaceTime. Він віднімає з мікрофона те, що сам мак
щойно віддав у динаміки, ще ДО того, як звук дійде до віспера. Власний
голос при цьому цілий — його в опорному сигналі немає за конструкцією.

Заміряно на Маку Льоші 12.09.2026 (вихід — динаміки MacBook Air, у браузері
грав твіч, тобто ЧУЖИЙ процес): ехо динаміків у мікрофоні −28,7 dBFS без
AEC проти −50,5 dBFS з ним, тобто −22 dB. Повні числа й джерела —
`~/.claude/projects/-/memory/ref_pysar_echo_needs_voice_processing_io_2026_09_12.md`.

Два підводні камені, обидва спіймані живим заміром, обидва обійдені нижче:

  * VPIO змінює формат входу — на цій машині 9 каналів проти 1 без нього, в
    інших звітах 3. Число МАШИНОЗАЛЕЖНЕ, тому читаємо `channelCount()` і
    беремо нульовий канал, а не сподіваємось на моно.
  * VPIO приглушує чужий звук, і системна доріжка SCK падає до −49 dBFS,
    тобто зустріч записує сама себе в тишу. Лікується на macOS 14+
    конфігурацією приглушення — і це не ObjC-клас, а C-СТРУКТУРА:
    `alloc()` на ній падає AttributeError.
"""

import contextlib
import threading
import time
from collections.abc import Callable

import numpy as np

try:
    import AVFAudio
    import AVFoundation

    AVAILABLE = True
except Exception:  # pragma: no cover - залежить від машини
    AVAILABLE = False


def _duck_min(inp) -> None:
    """Зняти приглушення чужого звуку, яке VPIO вмикає разом з AEC.

    Рівні в системі: Default = 0, Min = 10 — тобто «Min» означає МЕНШЕ
    приглушення, а не менший номер. Перевірено читанням властивості після
    запису."""
    inp.setVoiceProcessingOtherAudioDuckingConfiguration_(
        AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingConfiguration(
            False, AVFAudio.AVAudioVoiceProcessingOtherAudioDuckingLevelMin
        )
    )


def supported() -> bool:
    """Чи є на цій машині біндинги й сам селектор VPIO."""
    if not AVAILABLE:
        return False
    with contextlib.suppress(Exception):
        node = AVFoundation.AVAudioEngine.alloc().init().inputNode()
        return hasattr(node, "setVoiceProcessingEnabled_error_")
    return False


class VoiceProcessingMic:
    """Мікрофон із апаратним AEC. `on_block(mono_float32, sample_rate)`
    викликається з аудіо-потоку — робити в ньому треба мінімум."""

    # Скільки чекати, поки пристрій віддасть чинний формат. Після того, як
    # інший рушій щойно тримав вхід, `inputFormatForBus_` віддає нулі, і
    # встановлення тапу падає objc.error IsFormatSampleRateAndChannelCountValid.
    _FORMAT_WAIT_SEC = 3.0
    _TAP_FRAMES = 4096

    def __init__(self, on_block: Callable[[np.ndarray, int], None]):
        self._on_block = on_block
        self._engine = None
        self._input = None
        self._lock = threading.Lock()
        self._channels = 0

    @property
    def channels(self) -> int:
        """Скільки каналів віддав вхід (для лога — число машинозалежне)."""
        return self._channels

    def start(self) -> str | None:
        """Повертає None при успіху або текст помилки — чесну, не проглочену:
        якщо мікрофон зайнятий чужим застосунком, це має бути видно."""
        if not AVAILABLE:
            return "AVAudioEngine недоступний (нема pyobjc-біндингів)"
        try:
            engine = AVFoundation.AVAudioEngine.alloc().init()
            inp = engine.inputNode()
            ok, err = inp.setVoiceProcessingEnabled_error_(True, None)
            if not ok:
                return f"апаратний AEC не увімкнувся: {err}"
            with contextlib.suppress(Exception):
                _duck_min(inp)
            fmt = None
            deadline = time.monotonic() + self._FORMAT_WAIT_SEC
            while time.monotonic() < deadline:
                fmt = inp.inputFormatForBus_(0)
                if fmt.sampleRate() > 0 and fmt.channelCount() > 0:
                    break
                time.sleep(0.15)
            else:
                return "вхід не віддав формат (мікрофон тримає інший застосунок?)"
            self._channels = int(fmt.channelCount())
            sr = int(fmt.sampleRate())

            def tap(buf, when):
                # Аудіо-потік: без блокувань, без принтів, без винятків нагору.
                with contextlib.suppress(Exception):
                    n = buf.frameLength()
                    if not n:
                        return
                    # Нульовий канал: VPIO віддає їх кілька, корисний — перший.
                    ptr = buf.floatChannelData()[0]
                    mono = np.frombuffer(
                        memoryview(ptr.as_buffer(n * 4)), dtype=np.float32, count=n
                    ).copy()
                    self._on_block(mono, sr)

            inp.installTapOnBus_bufferSize_format_block_(0, self._TAP_FRAMES, fmt, tap)
            ok, err = engine.startAndReturnError_(None)
            if not ok:
                with contextlib.suppress(Exception):
                    inp.removeTapOnBus_(0)
                return f"мікрофон не відкрився: {err}"
        except Exception as e:  # pragma: no cover - залежить від машини
            return f"апаратний AEC: {e}"
        with self._lock:
            self._engine, self._input = engine, inp
        return None

    def stop(self) -> None:
        with self._lock:
            engine, inp = self._engine, self._input
            self._engine = self._input = None
        if inp is not None:
            with contextlib.suppress(Exception):
                inp.removeTapOnBus_(0)
        if engine is not None:
            with contextlib.suppress(Exception):
                engine.stop()
