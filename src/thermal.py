"""Термо-сторож: важка робота чекає, поки мак охолоне, замість того щоб пектись.

Чому саме пауза, а не «вбити процес» (так робить зовнішній ~/.claude/tools/guard.py):
транскрибація файлу йде годинами, і вбитий прогін — це втрачена година роботи.
Пауза між шматками не ламає нічого: whisper отримає наступний шматок пізніше,
ніж міг би, і все. Ідея Льоші 11.09.2026.

Температуру SoC читаємо БЕЗ пароля через IOHIDEventSystem — тим самим шляхом,
яким її читають Stats і macmon. `powermetrics` тут не потрібен (він вимагає root
і саме через нього раніше здавалося, що датчика немає).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

# ── Профілі порогів ───────────────────────────────────────────────────────────
# Пара чисел: (пауза при, продовжити при). Різниця між ними — гістерезис: без
# нього робота смикалась би «пауза-старт-пауза» по десять разів на хвилину,
# бо температура падає повільно, а підскакує за секунди.
#
# 🔴 Числа не з голови: 04.09.2026 замір під навантаженням дав розгін 29 °C/хв
# і полицю ~80 °C з підставкою; Льоша 10.09.2026: «я ставлю на підставку, робоча
# темпа реально десь 100 може буть — це не значить що треба гнати вище». У Каті
# підставки НЕМА: 18.08.2026 її мак під «Транскрибувати все» бачив 105 °C, і
# охолоджується він значно повільніше — тому «Бережно» існує саме для таких
# машин, і саме воно, а не «До упору», є нормальним вибором без підставки.
PROFILES: dict[str, tuple[float, float]] = {
    "off": (0.0, 0.0),
    "gentle": (88.0, 75.0),
    "normal": (95.0, 83.0),
    "hot": (101.0, 90.0),
}
DEFAULT_MODE = "normal"


def profile(mode: str) -> tuple[float, float]:
    return PROFILES.get(str(mode or ""), PROFILES[DEFAULT_MODE])


# ── Читання датчика ───────────────────────────────────────────────────────────
_sensor_lock = threading.Lock()
_sensor_broken = False


def read_temps() -> dict[str, float]:
    """{назва_сенсора: °C}. Порожньо — значить прочитати не вдалося.

    Порожній словник НЕ означає «холодно»: він означає «не знаю». Той, хто
    вирішує паузити, мусить розрізняти ці два випадки, інакше зламаний датчик
    зупинить роботу назавжди."""
    global _sensor_broken
    if _sensor_broken:
        return {}
    try:
        import ctypes
        import ctypes.util

        import objc
        from CoreFoundation import kCFStringEncodingUTF8

        iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
        cf = ctypes.CDLL(ctypes.util.find_library("CoreFoundation"))

        temperature_event = 15
        apple_vendor_page = 0xFF00
        temperature_sensor = 0x0005

        cf.CFDictionaryCreateMutable.restype = ctypes.c_void_p
        cf.CFNumberCreate.restype = ctypes.c_void_p
        cf.CFNumberCreate.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
        cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        cf.CFArrayGetCount.restype = ctypes.c_long
        cf.CFArrayGetCount.argtypes = [ctypes.c_void_p]
        cf.CFArrayGetValueAtIndex.restype = ctypes.c_void_p
        cf.CFArrayGetValueAtIndex.argtypes = [ctypes.c_void_p, ctypes.c_long]
        cf.CFDictionarySetValue.argtypes = [ctypes.c_void_p] * 3

        iokit.IOHIDEventSystemClientCreate.restype = ctypes.c_void_p
        iokit.IOHIDEventSystemClientCreate.argtypes = [ctypes.c_void_p]
        iokit.IOHIDEventSystemClientSetMatching.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDEventSystemClientCopyServices.restype = ctypes.c_void_p
        iokit.IOHIDEventSystemClientCopyServices.argtypes = [ctypes.c_void_p]
        iokit.IOHIDServiceClientCopyProperty.restype = ctypes.c_void_p
        iokit.IOHIDServiceClientCopyProperty.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        iokit.IOHIDServiceClientCopyEvent.restype = ctypes.c_void_p
        iokit.IOHIDServiceClientCopyEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int64,
            ctypes.c_int32,
            ctypes.c_int64,
        ]
        iokit.IOHIDEventGetFloatValue.restype = ctypes.c_double
        iokit.IOHIDEventGetFloatValue.argtypes = [ctypes.c_void_p, ctypes.c_int32]

        def _cfstr(s: str):
            return cf.CFStringCreateWithCString(None, s.encode(), kCFStringEncodingUTF8)

        def _cfnum(n: int):
            v = ctypes.c_int32(n)
            return cf.CFNumberCreate(None, 3, ctypes.byref(v))

        with _sensor_lock:
            m = cf.CFDictionaryCreateMutable(
                None,
                0,
                ctypes.byref(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
                ctypes.byref(ctypes.c_void_p.in_dll(cf, "kCFTypeDictionaryValueCallBacks")),
            )
            cf.CFDictionarySetValue(m, _cfstr("PrimaryUsagePage"), _cfnum(apple_vendor_page))
            cf.CFDictionarySetValue(m, _cfstr("PrimaryUsage"), _cfnum(temperature_sensor))
            client = iokit.IOHIDEventSystemClientCreate(None)
            iokit.IOHIDEventSystemClientSetMatching(client, m)
            services = iokit.IOHIDEventSystemClientCopyServices(client)
            if not services:
                return {}
            out: dict[str, float] = {}
            for i in range(cf.CFArrayGetCount(services)):
                sc = cf.CFArrayGetValueAtIndex(services, i)
                name_ref = iokit.IOHIDServiceClientCopyProperty(sc, _cfstr("Product"))
                name = objc.objc_object(c_void_p=ctypes.c_void_p(name_ref)) if name_ref else None
                ev = iokit.IOHIDServiceClientCopyEvent(sc, temperature_event, 0, 0)
                if ev:
                    val = iokit.IOHIDEventGetFloatValue(ev, temperature_event << 16)
                    out[str(name) if name else f"sensor{i}"] = round(float(val), 1)
            return out
    except Exception:
        # Немає pyobjc, інша архітектура, змінився приватний API — байдуже:
        # сторож просто зникає, робота йде далі. Другий раз не пробуємо.
        _sensor_broken = True
        return {}


def hottest() -> tuple[str, float] | None:
    """(назва, °C) найгарячішої точки кристала або None, якщо датчика немає.

    🔴 Беремо максимум САМЕ серед `tdie` — це температура кристала. Сліпий
    максимум по всіх 33 сенсорах небезпечний: поруч лежать `PMU tcal` /
    `PMU2 tcal`, і це не тепло, а калібрування — на M2 Air вони на спокої вищі
    за всі tdie (51,9 проти 50,2). Якби чиясь машина тримала tcal на високому
    числі постійно, робота стала б у вічну паузу, якої ніхто не міг би пояснити.
    Якщо tdie немає взагалі — чесний максимум по тому, що є."""
    temps = read_temps()
    if not temps:
        return None
    die = {k: v for k, v in temps.items() if "tdie" in k.lower()}
    name, value = max((die or temps).items(), key=lambda kv: kv[1])
    return name, float(value)


def available() -> bool:
    return hottest() is not None


# ── Ворота ────────────────────────────────────────────────────────────────────
class ThermalGate:
    """Одні ворота на весь застосунок: важкі місця питають у них дозволу.

    `wait()` повертає керування одразу, поки мак холодний, і тримає виклик, поки
    гарячий. Нічого не вбиває і нічого не скасовує."""

    def __init__(
        self,
        mode: str = DEFAULT_MODE,
        poll_sec: float = 5.0,
        cache_sec: float = 2.0,
        reader: Callable[[], tuple[str, float] | None] = hottest,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._lock = threading.Lock()
        self._pause_c, self._resume_c = profile(mode)
        self._mode = mode if mode in PROFILES else DEFAULT_MODE
        self._poll = poll_sec
        self._cache_sec = cache_sec
        self._reader = reader
        self._sleep = sleep
        self._last: tuple[str, float] | None = None
        self._last_at = 0.0
        self._holding = False

    # — налаштування —
    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        with self._lock:
            self._mode = mode if mode in PROFILES else DEFAULT_MODE
            self._pause_c, self._resume_c = profile(self._mode)

    @property
    def enabled(self) -> bool:
        return self._pause_c > 0

    @property
    def holding(self) -> bool:
        """Чи стоїть робота ПРЯМО ЗАРАЗ через тепло."""
        return self._holding

    def temperature(self) -> float | None:
        """°C найгарячішого місця, з коротким кешем: питати датчик на кожному
        шматку — теж робота, а мірити частіше ніж раз на дві секунди немає сенсу."""
        now = time.monotonic()
        if self._last is not None and now - self._last_at < self._cache_sec:
            return self._last[1]
        reading = self._reader()
        self._last_at = now
        self._last = reading
        return None if reading is None else reading[1]

    def wait(
        self,
        should_stop: Callable[[], bool] | None = None,
        on_state: Callable[[bool, float | None], None] | None = None,
    ) -> bool:
        """Тримає виклик, поки гаряче. False — просили спинитись, поки чекали.

        Датчика немає → одразу True: сторож без датчика мовчить, а не глушить
        роботу назавжди."""
        if not self.enabled:
            return True
        notified = False
        try:
            while True:
                temp = self.temperature()
                if temp is None:
                    return True
                if not self._holding:
                    if temp < self._pause_c:
                        return True
                    self._holding = True
                elif temp <= self._resume_c:
                    self._holding = False
                    return True
                if on_state is not None and not notified:
                    notified = True
                if on_state is not None:
                    on_state(True, temp)
                if should_stop is not None and should_stop():
                    return False
                self._sleep(self._poll)
        finally:
            if notified and on_state is not None:
                on_state(self._holding, self._last[1] if self._last else None)

    @property
    def threads(self) -> int:
        """Скільки потоків віддати CPU-рушію розділення голосів.

        🔴 Це друга половина лікування, і без неї пауза лише розтягує біду:
        whisper рахує на відеоядрі (Metal), а розділення голосів — ONNX на
        ПРОЦЕСОРІ в 4 потоки, тому воно гріє мак сильніше за саму розшифровку
        (помітив Льоша 11.09.2026). У «Бережно» ріжемо вдвічі: повільніше, але
        машина без охолоджуючої підставки взагалі доживає до кінця файлу."""
        return 2 if self._mode == "gentle" else 4


_gate: ThermalGate | None = None
_gate_lock = threading.Lock()


def gate() -> ThermalGate:
    """Один сторож на процес — інакше два прогони міряли б одне залізо нарізно."""
    global _gate
    with _gate_lock:
        if _gate is None:
            _gate = ThermalGate()
        return _gate
