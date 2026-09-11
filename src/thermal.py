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

import math
import threading
import time
from collections.abc import Callable

SETTLE_SEC = 20.0  # скільки ядро має протриматись нижче порога, щоб пауза скінчилась


def _log(line: str) -> None:
    # stdout застосунку йде в pysar.log (див. logsetup.py). Час — щоб видно було
    # ЦИКЛ: скільки робота гріла до паузи і скільки пауза тривала насправді.
    print(f"{time.strftime('%H:%M:%S')} {line}", flush=True)


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


# ── SMC: ті самі градуси, що людина бачить у меню ─────────────────────────────
# 🔴 11.09.2026. Льоша: «в мене 107 і воно жодного разу не спинилось». Замір у
# ту саму секунду: у нього на екрані 95 °C, наш HID `tdie` — 77 °C; на спокої
# 74,9 проти 60,9. Це не збій датчика, це ДВІ РІЗНІ ШКАЛИ. HID `PMU tdie` —
# усереднена температура кластера кристала; менюшні монітори (Stats, iStat)
# читають SMC-ключі `Tp**` — найгарячіше ЯДРО, а воно стабільно на 15-18 °C
# вище. Пороги 88/95/101 складались із чисел, які Льоша й Катя називали З МЕНЮ,
# тобто зі шкали SMC, — а код міряв нижчу. Тому сторож чесно мовчав: за його
# шкалою до порога бракувало рівно цієї різниці.
# Тепер міряємо те саме, що видно на екрані. HID лишається запасним шляхом.
_SMC: dict = {"read": None, "broken": False}


def _smc_reader():
    """Функція читання ключів SMC або None. Пароля не потребує.

    Перелік ключів знімається ОДИН раз (1677 ключів — це помітна робота), далі
    читаються тільки ядра."""
    import ctypes
    import ctypes.util
    import struct

    iokit = ctypes.CDLL(ctypes.util.find_library("IOKit"))
    libc = ctypes.CDLL(ctypes.util.find_library("c"))

    class _Version(ctypes.Structure):
        _fields_ = [
            ("major", ctypes.c_ubyte),
            ("minor", ctypes.c_ubyte),
            ("build", ctypes.c_ubyte),
            ("reserved", ctypes.c_ubyte),
            ("release", ctypes.c_ushort),
        ]

    class _PLimit(ctypes.Structure):
        _fields_ = [
            ("version", ctypes.c_ushort),
            ("length", ctypes.c_ushort),
            ("cpuPLimit", ctypes.c_uint32),
            ("gpuPLimit", ctypes.c_uint32),
            ("memPLimit", ctypes.c_uint32),
        ]

    class _KeyInfo(ctypes.Structure):
        _fields_ = [
            ("dataSize", ctypes.c_uint32),
            ("dataType", ctypes.c_uint32),
            ("dataAttributes", ctypes.c_ubyte),
        ]

    class _KeyData(ctypes.Structure):
        _fields_ = [
            ("key", ctypes.c_uint32),
            ("vers", _Version),
            ("pLimitData", _PLimit),
            ("keyInfo", _KeyInfo),
            ("result", ctypes.c_ubyte),
            ("status", ctypes.c_ubyte),
            ("data8", ctypes.c_ubyte),
            ("data32", ctypes.c_uint32),
            ("bytes", ctypes.c_ubyte * 32),
        ]

    iokit.IOServiceMatching.restype = ctypes.c_void_p
    iokit.IOServiceGetMatchingService.restype = ctypes.c_uint
    iokit.IOServiceGetMatchingService.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    iokit.IOServiceOpen.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_uint),
    ]
    iokit.IOConnectCallStructMethod.argtypes = [
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    libc.mach_task_self.restype = ctypes.c_uint

    service = iokit.IOServiceGetMatchingService(0, iokit.IOServiceMatching(b"AppleSMC"))
    if not service:
        return None
    conn = ctypes.c_uint(0)
    if iokit.IOServiceOpen(service, libc.mach_task_self(), 0, ctypes.byref(conn)) != 0:
        return None

    def call(payload):
        out = _KeyData()
        size = ctypes.c_size_t(ctypes.sizeof(_KeyData))
        rc = iokit.IOConnectCallStructMethod(
            conn,
            2,  # kSMCHandleYPCEvent
            ctypes.byref(payload),
            ctypes.sizeof(payload),
            ctypes.byref(out),
            ctypes.byref(size),
        )
        return out if rc == 0 and out.result == 0 else None

    def as_key(text: str) -> int:
        return struct.unpack(">I", text.encode())[0]

    def as_text(num: int) -> str:
        return struct.pack(">I", num).decode(errors="replace")

    def read(name: str):
        info = _KeyData()
        info.key, info.data8 = as_key(name), 9  # kSMCGetKeyInfo
        got = call(info)
        if got is None:
            return None
        size, dtype = got.keyInfo.dataSize, as_text(got.keyInfo.dataType)
        payload = _KeyData()
        payload.key, payload.data8 = as_key(name), 5  # kSMCReadKey
        payload.keyInfo.dataSize = size
        got = call(payload)
        if got is None:
            return None
        raw = bytes(got.bytes[:size])
        if dtype == "flt " and size == 4:
            return struct.unpack("<f", raw)[0]
        if dtype == "sp78" and size == 2:
            return struct.unpack(">h", raw)[0] / 256.0
        if dtype == "ioft" and size == 8:
            return struct.unpack("<Q", raw)[0] / 65536.0
        return None

    # Скільки всього ключів → пройтись по індексах і відібрати ядра `Tp**`.
    info = _KeyData()
    info.key, info.data8 = as_key("#KEY"), 9
    got = call(info)
    if got is None:
        return None
    payload = _KeyData()
    payload.key, payload.data8 = as_key("#KEY"), 5
    payload.keyInfo.dataSize = got.keyInfo.dataSize
    got = call(payload)
    if got is None:
        return None
    total = struct.unpack(">I", bytes(got.bytes[:4]))[0]

    cores: list[str] = []
    for i in range(total):
        payload = _KeyData()
        payload.data8, payload.data32 = 8, i  # kSMCGetKeyFromIndex
        got = call(payload)
        if got is None:
            continue
        name = as_text(got.key)
        # `Tp**` — ядра процесора. Саме їх показує «Hottest CPU» у Stats.
        if name.startswith("Tp") and read(name) is not None:
            cores.append(name)
    if not cores:
        return None

    def read_cores() -> dict[str, float]:
        out: dict[str, float] = {}
        for name in cores:
            value = read(name)
            # Відкинуті нулі й дурні числа: непідключений сенсор віддає 0 або
            # -127, і сліпий максимум по них зробив би «холодно» з гарячого маку.
            if value is not None and 10.0 < value < 130.0:
                out[name] = round(float(value), 1)
        return out

    return read_cores


def read_cores() -> dict[str, float]:
    """{ключ_ядра: °C} з SMC. Порожньо — читати не вдалося."""
    if _SMC["broken"]:
        return {}
    try:
        if _SMC["read"] is None:
            with _sensor_lock:
                if _SMC["read"] is None:
                    _SMC["read"] = _smc_reader()
            if _SMC["read"] is None:
                _SMC["broken"] = True
                return {}
        return _SMC["read"]()
    except Exception:
        _SMC["broken"] = True
        return {}


def hottest() -> tuple[str, float] | None:
    """(назва, °C) найгарячішої точки або None, якщо датчика немає.

    🔴 Порядок джерел має значення. СПЕРШУ SMC-ядра `Tp**` — рівно те число,
    що людина бачить у своєму меню (див. коментар до `_SMC` вище): сторож і
    людина мусять говорити про одні градуси, інакше «в мене 107, а воно не
    спиняється» — і обидва мають рацію.

    Запасний шлях — HID, і там беремо максимум САМЕ серед `tdie`. Сліпий
    максимум по всіх 33 сенсорах небезпечний: поруч лежать `PMU tcal` /
    `PMU2 tcal`, і це не тепло, а калібрування — на M2 Air вони на спокої вищі
    за всі tdie (51,9 проти 50,2). Якби чиясь машина тримала tcal на високому
    числі постійно, робота стала б у вічну паузу, якої ніхто не міг би пояснити.
    Якщо tdie немає взагалі — чесний максимум по тому, що є."""
    cores = read_cores()
    if cores:
        name, value = max(cores.items(), key=lambda kv: kv[1])
        return f"ядро {name}", float(value)
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
        poll_sec: float = 2.0,
        cache_sec: float = 2.0,
        reader: Callable[[], tuple[str, float] | None] = hottest,
        sleep: Callable[[float], None] = time.sleep,
        settle_sec: float = 0.0,
    ) -> None:
        self._lock = threading.Lock()
        self._settle = settle_sec
        self._pause_c, self._resume_c = profile(mode)
        self._mode = mode if mode in PROFILES else DEFAULT_MODE
        self._poll = poll_sec
        self._cache_sec = cache_sec
        self._reader = reader
        self._sleep = sleep
        self._last: tuple[str, float] | None = None
        self._last_at = 0.0
        self._holding = False
        # 🔴 11.09.2026. Поріг спільний, а вмикач — окремий на кожну ділянку
        # роботи: запис зустрічі людина хоче стерегти НЕ обовʼязково тоді ж,
        # коли пакетну розшифровку файлів. Один тумблер на двох означав би, що
        # ввімкнувши сторожа для черги файлів, ти мовчки поставив паузи й на
        # розділення голосів після «Стоп» — а це різні сценарії за терміновістю.
        self._scopes: dict[str, bool] = {"meeting": True, "files": True}
        self._checked: dict[str, float] = {}

    # — журнал —
    # 🔴 11.09.2026, Льоша: «важливо щоб реальна темпа захоплювалась… поки що має
    # писатись, бо як ми зрозуміємо що він адекватно включається». Плашка показує
    # лише останнє число, а меню — вже інше. Тому в pysar.log іде СИРИЙ замір:
    # старт паузи, кожен замір під час неї, кінець із тривалістю й піком, а поза
    # паузою — що бачив сторож, не частіше ніж раз на CHECK_LOG_SEC.
    CHECK_LOG_SEC = 30.0

    def _where(self) -> str:
        return self._last[0] if self._last else "?"

    def _log_check(self, tag: str, temp: float) -> None:
        now = time.monotonic()
        if now - self._checked.get(tag, -self.CHECK_LOG_SEC) < self.CHECK_LOG_SEC:
            return
        self._checked[tag] = now
        _log(f"🌡 guard check [{tag}] {self._where()} {temp:.1f}° < {self._pause_c:.0f}° — працюємо")

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

    def set_scope(self, scope: str, on: bool) -> None:
        """Увімкнути/вимкнути сторожа на одній ділянці («meeting» / «files»)."""
        with self._lock:
            self._scopes[scope] = bool(on)

    def enabled_for(self, scope: str | None) -> bool:
        """Чи стереже сторож цю ділянку. Незнайома назва = стереже: нова
        ділянка має бути під охороною за замовчуванням, а не без неї."""
        if not self.enabled:
            return False
        return True if scope is None else self._scopes.get(scope, True)

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
        scope: str | None = None,
    ) -> bool:
        """Тримає виклик, поки гаряче. False — просили спинитись, поки чекали.

        Датчика немає → одразу True: сторож без датчика мовчить, а не глушить
        роботу назавжди."""
        if not self.enabled_for(scope):
            return True
        notified = False
        started = time.monotonic()
        peak = 0.0
        tag = scope or "-"
        # 🔴 11.09.2026, лог першого ж запису: 4 паузи, КОЖНА рівно 2 с — ядро
        # падало з 113° до 88° за один замір, робота вертались і знов розганяла
        # його. Пауза студила число, а не мак. Тому відпускаємо лише після того,
        # як нижче порога протрималось `settle_sec` поспіль.
        need = 1 if self._poll <= 0 or self._settle <= 0 else math.ceil(self._settle / self._poll)
        cool_n = 0
        try:
            while True:
                temp = self.temperature()
                if temp is None:
                    return True
                peak = max(peak, temp)
                if not self._holding:
                    if temp < self._pause_c:
                        self._log_check(tag, temp)
                        return True
                    self._holding = True
                    started = time.monotonic()
                    _log(
                        f"🌡 guard pause [{tag}] {self._where()} {temp:.1f}° ≥ {self._pause_c:.0f}° "
                        f"(mode {self._mode})"
                    )
                elif temp <= self._resume_c:
                    cool_n += 1
                    if cool_n >= need:
                        self._holding = False
                        _log(
                            f"🌡 guard resume [{tag}] {self._where()} {temp:.1f}° ≤ {self._resume_c:.0f}° "
                            f"after {time.monotonic() - started:.0f}s, peak {peak:.1f}°"
                        )
                        return True
                    _log(f"🌡 guard cool [{tag}] {self._where()} {temp:.1f}° ({cool_n}/{need})")
                else:
                    cool_n = 0
                    _log(
                        f"🌡 guard hold [{tag}] {self._where()} {temp:.1f}° ({time.monotonic() - started:.0f}s)"
                    )
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
            _gate = ThermalGate(settle_sec=SETTLE_SEC)
        return _gate
