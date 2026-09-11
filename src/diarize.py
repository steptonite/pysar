"""Розділення спікерів усередині ОДНОГО каналу — після Стоп, не в реалтаймі.

Канальне розділення (мікрофон окремо від системи) вже робить `syscap` у режимі
`smart` — воно безкоштовне й точне, бо джерела фізично різні. Цей модуль вирішує
іншу задачу: в одному каналі говорять кілька людей (зустріч у Zoom через колонки,
ефір, урок), і їх треба розчепити за голосом.

ЧОМУ ПІСЛЯ СТОП, А НЕ В РЕАЛТАЙМІ
  Кластеризація голосів потребує всього запису: хто «спікер 1», видно лише коли
  почуто всіх. Реалтаймова версія неминуче перейменовує людей заднім числом —
  саме за це діаризацію в Pysar відхилили 12.07.2026. Тому прохід іде ОДИН раз,
  над готовим записом, і кладе результат ОКРЕМИМ файлом: живий `.md` не чіпається
  взагалі, тож зламати робочий транскрипт цей код не може за побудовою.

ЧОМУ ДВІ МОДЕЛІ, А НЕ ОДНА
  Кластеризувати вектори цілих сегментів whisper не можна: сегмент часто містить
  двох людей, і вектор виходить змішаний (заміряно 04–05.09.2026 — 217 із 237
  реплік злиплись в одного спікера). Спершу КАДРОВА сегментація (pyannote, крок
  ~17 мс) ріже потік на однорідні шматки, і лише потім на них рахуються вектори
  (TitaNet). На тому ж ефірі це дало ×6,3 швидше за реальний час.

ЧОМУ МОДЕЛІ ДОКАЧУЮТЬСЯ
  ~110 МБ моделей + `sherpa-onnx` не мають лежати в інсталяторі: більшість
  користувачів диктує, а не розділяє спікерів. Тягнемо на першу вимогу — коли
  користувач сам увімкнув режим (рішення Льоші 06.09.2026).
"""

import contextlib
import importlib
import json
import os
import re
import shutil
import site
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
import wave
from pathlib import Path

from .paths import data_dir

# Версія рушія закріплена: у Льоші воно вже працює, і свіжий реліз не має права
# зламати установку на ЧУЖОМУ маку, який ставиться пізніше (Катя, Аня). 1.x
# тримає той самий API OfflineSpeakerDiarization.
ENGINE_SPEC = "sherpa-onnx>=1.10,<2"

# ── Моделі ────────────────────────────────────────────────────────────────────
SEG_DIRNAME = "sherpa-onnx-pyannote-segmentation-3-0"
SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-recongition-models/nemo_en_titanet_large.onnx"  # (так, у тега тайпо в upstream)
)
DOWNLOAD_MB = 110  # те, що побачить користувач перед натисканням «Завантажити»
NEED_FREE_MB = 400  # моделі + колесо + розпакування, із запасом

# Діаризація — важка локальна робота (пікова памʼять на годинному записі йде на
# сотні МБ). Дві одночасні на 8 ГБ кладуть машину у своп, тож прохід у процесі
# рівно один: друга спроба чесно каже «зайнято», а не тихо конкурує за памʼять.
_JOB_LOCK = threading.Lock()

# Готова копія моделей на цій машині — беремо звідси замість мережі, якщо є.
# Не вимога, а економія: у Льоші вони вже лежать для позарепозиторного рушія.
_LOCAL_CACHES = (Path.home() / ".claude" / "tools" / "subs" / "models" / "diar",)

# ── Пороги кластеризації ──────────────────────────────────────────────────────
# Краще ПЕРЕ-розділити, ніж НЕДО-розділити: два кластери з одним іменем людина
# зливає в редакторі однією дією, а розчепити злиплий кластер не може ніяк.
CLUSTER_THRESHOLD = 0.60
MIN_CLUSTER_SEC = 15.0  # коротші кластери йдуть у «❓», а не тонуть у сусіді
WINDOW_SEC = 1800.0  # прохід вікнами по 30 хв — стеля памʼяті 8 ГБ
STITCH_THRESHOLD = 0.55  # схожість, за якої кластери сусідніх вікон — одна людина
SAMPLE_RATE = 16000


def models_dir() -> Path:
    return data_dir() / "models" / "diar"


def seg_model() -> Path:
    return models_dir() / SEG_DIRNAME / "model.onnx"


def emb_model() -> Path:
    return models_dir() / "nemo_en_titanet_large.onnx"


# ── Готовність ────────────────────────────────────────────────────────────────
def have_engine() -> bool:
    """Чи стоїть `sherpa_onnx` у цьому інтерпретаторі."""
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def have_models() -> bool:
    return seg_model().exists() and emb_model().exists()


def is_ready() -> bool:
    return have_engine() and have_models()


def status() -> dict:
    """Знімок для екрана налаштувань — що є, чого бракує, скільки качати."""
    engine, models = have_engine(), have_models()
    return {
        "engine": engine,
        "models": models,
        "ready": engine and models,
        "download_mb": DOWNLOAD_MB,
        "local_cache": any(_cached_pair(c) for c in _LOCAL_CACHES),
        "models_dir": str(models_dir()),
    }


def _cached_pair(cache: Path) -> bool:
    return (cache / SEG_DIRNAME / "model.onnx").exists() and (
        cache / "nemo_en_titanet_large.onnx"
    ).exists()


# ── Встановлення на вимогу ────────────────────────────────────────────────────
def ensure_ready(progress=None) -> tuple[bool, str]:
    """Доставити рушій і моделі. Повертає (готово, повідомлення для людини).

    Ніколи не кидає: викликається з UI-потоку користувача, і збій докачки має
    бути рядком у вікні, а не падінням застосунку."""

    def say(msg: str) -> None:
        if progress:
            with contextlib.suppress(Exception):
                progress(msg)

    try:
        if not is_ready():
            data_dir().mkdir(parents=True, exist_ok=True)
            free = _free_mb(data_dir())
            if free < NEED_FREE_MB:
                return False, f"Мало місця на диску: треба ~{NEED_FREE_MB} МБ, вільно {free:.0f}."
        if not have_engine():
            say("Встановлюю рушій (sherpa-onnx)…")
            ok, msg = _pip_install(ENGINE_SPEC)
            if not ok:
                return False, msg
        if not have_models():
            models_dir().mkdir(parents=True, exist_ok=True)
            src = next((c for c in _LOCAL_CACHES if _cached_pair(c)), None)
            if src is not None:
                say("Копіюю моделі з локального кешу…")
                _copy_models(src)
            else:
                if not emb_model().exists():
                    say("Завантажую модель голосів (~97 МБ)…")
                    _retry(lambda: _download(EMB_URL, emb_model()))
                if not seg_model().exists():
                    say("Завантажую модель сегментації (~8 МБ)…")
                    _retry(_download_seg)
        if not have_engine():
            return False, "Рушій не піднявся після встановлення — перезапусти Pysar."
        if not have_models():
            return False, "Моделі не встановились — спробуй ще раз."
        # Останній гейт: файли на місці ≠ воно працює. Формат моделі, архітектура
        # процесора, битий байт — усе це видно тільки на реальному завантаженні,
        # і краще дізнатись про це тут, ніж під час першої зустрічі.
        say("Перевіряю рушій…")
        ok, msg = self_check()
        if not ok:
            return False, msg
    except Exception as e:  # мережа, диск, права — усе сюди
        return False, f"Не вдалося: {e}"
    return True, "Готово — розділення спікерів увімкнеться після наступного запису"


def self_check() -> tuple[bool, str]:
    """Підняти моделі на секунді тиші. Дешево, але ловить усе, що ламається
    мовчки: не ту архітектуру колеса, обрізаний .onnx, зниклу теку."""
    try:
        import numpy as np
        import sherpa_onnx
    except Exception as e:
        return False, f"рушій не імпортується: {e}"
    if not have_models():
        return False, "немає файлів моделей"
    try:
        cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(seg_model())
                ),
                num_threads=1,
            ),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(emb_model()), num_threads=1
            ),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.6),
        )
        sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
        sd.process(np.zeros(SAMPLE_RATE * 3, dtype=np.float32))
    except Exception as e:
        return False, f"моделі не запускаються: {str(e)[:120]}"
    return True, "ok"


def venv_python() -> str:
    """Інтерпретатор, у site-packages якого застосунок РЕАЛЬНО читає пакети.

    🔴 Не `sys.executable`. Встановлена `/Applications/Pysar.app` запускає КОПІЮ
    фреймворкового Python зі свого бандла, а venv проєкту підмішується збоку
    через `PYSAR_SITE` (scripts/_app_main.py). Постав ми пакет у sys.executable —
    він ліг би у фреймворковий Python (або впав на правах доступу), кнопка
    відзвітувала б «готово», а розділення спікерів не працювало б. Саме такий
    мовчазний розрив коштував нам вечора 07.08.2026 в іншому місці.
    """
    sp = os.environ.get("PYSAR_SITE") or ""
    if not sp:
        with contextlib.suppress(Exception):
            sp = site.getsitepackages()[0]
    if sp:
        # .../venv/lib/python3.12/site-packages → .../venv/bin/python
        cand = Path(sp).parents[2] / "bin" / "python"
        if cand.exists():
            return str(cand)
    cand = Path(sys.prefix) / "bin" / "python"
    if cand.exists():
        return str(cand)
    return sys.executable


def _pip_install(spec: str) -> tuple[bool, str]:
    py = venv_python()
    try:
        r = subprocess.run(
            [py, "-m", "pip", "install", "--disable-pip-version-check", spec],
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except FileNotFoundError:
        return False, "не знайдено Python застосунку — перевстанови Pysar (make setup)"
    except subprocess.TimeoutExpired:
        return False, "встановлення рушія не вклалось у 30 хв — перевір інтернет"
    except Exception as e:
        return False, f"pip не запустився: {e}"
    if r.returncode != 0:
        tail = [ln for ln in (r.stderr or r.stdout or "").strip().splitlines() if ln.strip()]
        hint = tail[-1] if tail else "помилка"
        if "No matching distribution" in (r.stderr or ""):
            hint = "немає збірки під цей Mac/версію Python"
        return False, f"рушій не встановився: {hint}"
    # Пакет ліг у site-packages, який уже в sys.path цього процесу — але кеш
    # імпортера про нього не знає, тож без цього перший прогін падає «немає
    # модуля» рівно після успішної установки.
    importlib.invalidate_caches()
    return True, "ok"


def _free_mb(path: Path) -> float:
    with contextlib.suppress(Exception):
        return shutil.disk_usage(path).free / (1024 * 1024)
    return float("inf")


def _retry(fn, attempts: int = 3, pause: float = 3.0):
    """Дві додаткові спроби з паузою. Не перестраховка: установка Pysar на Intel
    Air 2015 уже падала одного разу на `Could not resolve host` до PyPI, а
    повтор через хвилину пройшов (13.07.2026). Разова мережева гикавка не має
    коштувати людині всієї докачки."""
    last = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i + 1 < attempts:
                time.sleep(pause)
    raise last


def _download(url: str, dest: Path) -> None:
    """Качаємо у сусідній .part і перейменовуємо — обірваний файл ніколи не
    виглядає як готова модель (інакше наступний запуск падає всередині onnx
    незрозумілою помилкою). Розмір звіряється з Content-Length: тиха недокачка
    на слабкому вайфаї — найгірший зі сценаріїв, бо файл на місці й «є»."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=60) as r:
        expect = int(r.headers.get("Content-Length") or 0)
        with open(part, "wb") as f:
            shutil.copyfileobj(r, f, 1024 * 256)
    got = part.stat().st_size
    if expect and got != expect:
        part.unlink(missing_ok=True)
        raise OSError(f"файл докачався не повністю ({got} з {expect} байтів)")
    part.replace(dest)


def _download_seg() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        arc = Path(tmp) / "seg.tar.bz2"
        _download(SEG_URL, arc)
        with tarfile.open(arc, "r:bz2") as t:
            members = [m for m in t.getmembers() if _safe_member(m.name)]
            t.extractall(models_dir(), members=members)


def _safe_member(name: str) -> bool:
    """Тільки шляхи всередині цільової теки — архів із мережі не має права
    писати за її межі."""
    p = Path(name)
    return not p.is_absolute() and ".." not in p.parts


def _copy_models(cache: Path) -> None:
    shutil.copy2(cache / "nemo_en_titanet_large.onnx", emb_model())
    dst = models_dir() / SEG_DIRNAME
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cache / SEG_DIRNAME / "model.onnx", dst / "model.onnx")


# ── Аудіо ─────────────────────────────────────────────────────────────────────
def load_wav16k(path: Path):
    """16 кГц моно у float32. Дампи зустрічей уже такі — читаємо напряму, без
    ffmpeg; усе інше приводить до цього виду `to_wav16k`."""
    import numpy as np

    with contextlib.closing(wave.open(str(path))) as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError("очікується 16 кГц моно 16-біт")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def load_raw16k(path: Path):
    """s16le 16 кГц моно без заголовка — саме такий тимчасовий файл лишає по
    собі ffmpeg у транскрибації файлів, тож другий прохід декодування не
    потрібен (а на годинному відео це помітні хвилини)."""
    import numpy as np

    return np.fromfile(str(path), dtype=np.int16).astype(np.float32) / 32768.0


def load_audio(path: Path):
    """WAV чи сирий PCM — за розширенням."""
    path = Path(path)
    return load_raw16k(path) if path.suffix == ".raw" else load_wav16k(path)


def to_wav16k(src: Path, dest: Path) -> Path:
    """Будь-яке відео/аудіо → 16 кГц моно WAV через ffmpeg (він уже є вимогою
    транскрибації файлів)."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-ar", "16000", "-ac", "1", str(dest)],
        check=True,
        timeout=3600,
    )
    return dest


# ── Кадрова діаризація ────────────────────────────────────────────────────────
def diarize_wav(
    path: Path, progress=None, speakers: int = 0, gate=None
) -> list[tuple[float, float, int]]:
    """(t0, t1, кластер) для одного файлу. Кластер −1 = «невпізнано»."""
    return diarize_samples(load_audio(path), progress=progress, speakers=speakers, gate=gate)


def diarize_samples(
    x, progress=None, speakers: int = 0, gate=None
) -> list[tuple[float, float, int]]:
    """`gate` — функція, яку рушій питає між внутрішніми шматками: вона має
    право затримати виклик (термо-пауза) і нічого не повертає."""
    import numpy as np
    import sherpa_onnx

    threads = 4
    if gate is None:
        from . import thermal

        g = thermal.gate()
        threads = g.threads
        gate = g.wait if g.enabled else None

    cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(
                model=str(seg_model())
            ),
            num_threads=threads,
        ),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(emb_model()), num_threads=threads
        ),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=CLUSTER_THRESHOLD),
        min_duration_on=0.3,
        min_duration_off=0.5,
    )
    sd = sherpa_onnx.OfflineSpeakerDiarization(cfg)
    step = int(WINDOW_SEC * SAMPLE_RATE)
    windows = max(1, (len(x) + step - 1) // step)
    ivs: list[list] = []
    for wi in range(windows):
        chunk = x[wi * step : (wi + 1) * step]
        if len(chunk) < SAMPLE_RATE * 2:
            continue
        off = wi * WINDOW_SEC

        def _beat(_done: int, _total: int, _gate=gate) -> int:
            # Рушій кличе це між своїми внутрішніми шматками — єдине місце
            # всередині 30-хвилинного вікна, де можна перевести дух. Затримка
            # тут нічого не ламає: повертаємо 0 = «працюй далі».
            if _gate is not None:
                with contextlib.suppress(Exception):
                    _gate()
            return 0

        for s in sd.process(chunk, callback=_beat).sort_by_start_time():
            ivs.append([off + s.start, off + s.end, f"w{wi}_{s.speaker}"])
        if progress:
            with contextlib.suppress(Exception):
                progress(f"Розділяю спікерів: {wi + 1}/{windows}")
    return _stitch(x, ivs, np, speakers=speakers)


def _stitch(x, ivs, np, speakers: int = 0) -> list[tuple[float, float, int]]:
    """Локальні кластери різних вікон → один глобальний мовець.

    Без цього людина на 40-й хвилині стає «новим спікером» просто тому, що
    почалося друге вікно.

    `speakers` ≥ 2 — людина ПАМʼЯТАЄ, скільки голосів було. Число застосовується
    саме тут, на глобальних центроїдах, а не в конфізі кластеризатора: там воно
    діяло б на КОЖНЕ 30-хвилинне вікно окремо й розпилило б одного мовця на
    трьох у тихому вікні. Тут же ми просто зливаємо найсхожіші голоси, поки їх
    не лишиться рівно стільки, скільки сказала людина.
    """
    import sherpa_onnx

    if not ivs:
        return []
    ex = sherpa_onnx.SpeakerEmbeddingExtractor(
        sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(emb_model()), num_threads=4)
    )

    def vec(a: float, b: float):
        seg = x[int(a * SAMPLE_RATE) : int(b * SAMPLE_RATE)]
        if len(seg) < SAMPLE_RATE * 0.9:
            return None
        if len(seg) > SAMPLE_RATE * 6:  # довгий шматок ріжемо до 6 с із середини
            m = len(seg) // 2
            seg = seg[m - SAMPLE_RATE * 3 : m + SAMPLE_RATE * 3]
        try:
            st = ex.create_stream()
            st.accept_waveform(SAMPLE_RATE, seg)
            st.input_finished()
            e = np.array(ex.compute(st), dtype=np.float32)
            return e / (np.linalg.norm(e) + 1e-9)
        except Exception:
            return None

    cent = {}
    for key in sorted({k for _, _, k in ivs}):
        parts = sorted(
            [(a, b) for a, b, k in ivs if k == key], key=lambda p: p[1] - p[0], reverse=True
        )
        es = [v for a, b in parts[:12] if (v := vec(a, b)) is not None]
        if es:
            m = np.mean(es, 0)
            cent[key] = m / (np.linalg.norm(m) + 1e-9)

    glob: dict[str, int] = {}
    gcent: list = []
    for key in sorted(cent):
        best, bi = -1.0, -1
        for i, g in enumerate(gcent):
            s = float(cent[key] @ g)
            if s > best:
                best, bi = s, i
        if best >= STITCH_THRESHOLD:
            glob[key] = bi
            merged = gcent[bi] + cent[key]
            gcent[bi] = merged / (np.linalg.norm(merged) + 1e-9)
        else:
            glob[key] = len(gcent)
            gcent.append(cent[key].copy())

    if speakers >= 2 and len(gcent) > speakers:
        # Тривалість КОЖНОГО глобального голосу — щоб злиття знало, хто тут
        # людина, а хто сміття. Без цього воно зливало двох справжніх мовців
        # (вони схожі: той самий кодек трансляції) і лишало окремо секундний
        # уривок музики — 11.09.2026 саме так два стрімери стали одним.
        gdur: dict[int, float] = {}
        for a, b, key in ivs:
            g = glob.get(key)
            if g is not None:
                gdur[g] = gdur.get(g, 0.0) + (b - a)
        glob, gcent = _merge_to(glob, gcent, speakers, np, gdur)

    out = [(a, b, glob.get(k, -1)) for a, b, k in ivs]
    if speakers >= 2:
        # Число назвала людина — не маємо права перетворювати «зайвий» голос на
        # «❓»: короткий мовець тут очікуваний, а не сміття.
        return out
    dur: dict[int, float] = {}
    for a, b, g in out:
        dur[g] = dur.get(g, 0.0) + (b - a)
    tiny = {g for g, d in dur.items() if d < MIN_CLUSTER_SEC}
    return [(a, b, (-1 if g in tiny else g)) for a, b, g in out]


def _merge_to(glob: dict, gcent: list, k: int, np, dur: dict | None = None):
    """Звести глобальні голоси до рівно `k`, поглинаючи НАЙКОРОТШІ.

    🔴 11.09.2026. Раніше тут щоразу зливалася найсхожіша ПАРА — і на записі
    двох стрімерів це дало рівно протилежне тому, що просив користувач: два
    справжні голоси (схожі, бо йдуть через один кодек трансляції) злилися в
    один, а окремим «мовцем» лишився секундний шматок музики. Тому тепер
    рахуємо, скільки кожен голос ГОВОРИТЬ, і поглинаємо найкоротший у найсхожий
    із решти. Довгі голоси — це люди, вони мають дожити до кінця.

    Якщо рушій знайшов МЕНШЕ голосів, ніж назвала людина, нічого не вигадуємо —
    розділити наявне на більше ми не можемо чесно."""
    cents = [g.copy() for g in gcent]
    remap = {i: i for i in range(len(cents))}
    secs = {i: float((dur or {}).get(i, 0.0)) for i in range(len(cents))}
    while len({remap[i] for i in remap}) > k:
        alive = sorted({remap[i] for i in remap})
        drop = min(alive, key=lambda i: (secs.get(i, 0.0), i))
        best, keep = -2.0, None
        for i in alive:
            if i == drop:
                continue
            sim = float(cents[drop] @ cents[i])
            if sim > best:
                best, keep = sim, i
        if keep is None:
            break
        # Центроїд зважуємо тривалістю: хвилина мовлення не має важити стільки
        # ж, скільки півсекунди, інакше довгий голос «попливе» до короткого.
        wk, wd = max(secs.get(keep, 0.0), 1e-6), max(secs.get(drop, 0.0), 1e-6)
        merged = cents[keep] * wk + cents[drop] * wd
        cents[keep] = merged / (np.linalg.norm(merged) + 1e-9)
        secs[keep] = wk + wd
        for i, v in list(remap.items()):
            if v == drop:
                remap[i] = keep
    order = {old: new for new, old in enumerate(sorted({remap[i] for i in remap}))}
    return (
        {key: order[remap[v]] for key, v in glob.items()},
        [cents[old] for old in sorted({remap[i] for i in remap})],
    )


# ── Накладання на текст (чисті функції — тестуються без моделей) ──────────────
def audio_map(paths) -> dict[str, Path]:
    """Файли дампів зустрічі → {джерело: шлях}. Джерело читається з імені, яке
    дає `syscap` (`…-sys.wav` / `…-mic.wav`); чуже імʼя ігнорується мовчки."""
    out: dict[str, Path] = {}
    for path in paths or []:
        name = Path(path).name
        if name.endswith("-sys.wav"):
            out["sys"] = Path(path)
        elif name.endswith("-mic.wav"):
            out["mic"] = Path(path)
    return out


def read_sidecar(path: Path) -> tuple[dict, list[dict]]:
    """(_meta, рядки) із `.сегменти.jsonl`. Побитий рядок пропускається: сайдкар
    пишеться на живому записі й може обірватись на будь-якому байті."""
    meta: dict = {}
    rows: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if "_meta" in obj:
                meta = obj["_meta"]
            elif obj.get("text"):
                rows.append(obj)
    return meta, rows


def _cluster_at(cand: list, t: float):
    """Хто говорить у момент `t`: (джерело, кластер) або None."""
    for src, (a, b, g) in cand:
        if a <= t <= b:
            return (src, g)
    return None


def _best_overlap(cand: list, t0: float, t1: float):
    best, share = None, 0.0
    for src, (a, b, g) in cand:
        ov = min(t1, b) - max(t0, a)
        if ov > share:
            best, share = (src, g), ov
    return best if share > 0 else None


def _tokens_to_words(tokens: list) -> list[tuple[float, float, str]]:
    """Зібрати токени whisper назад у слова.

    🔴 11.09.2026, «ріже по буквах»: у `w` лежать не слова, а ТОКЕНИ —
    `' Д'`, `'е'`, `','`, `' брат'`, `'ик'`. Нове слово починає лише токен із
    пробілом попереду; решта (шматки слова, розділові знаки) дописується до
    попереднього. Якщо пробілу немає ні в кого, це вже готові слова."""
    parsed = [(float(t[0]), float(t[1]), str(t[2])) for t in tokens]
    if not any(text[:1].isspace() for _, _, text in parsed):
        return parsed
    out: list[list] = []
    for a, b, text in parsed:
        if out and not text[:1].isspace():
            out[-1][1] = b
            out[-1][2] += text
        else:
            out.append([a, b, text])
    return [(a, b, text) for a, b, text in out]


def _split_row_by_words(row: dict, cand: list) -> list[dict]:
    """Порізати рядок там, де МІНЯЄТЬСЯ ГОЛОС, а не там, де whisper поставив крапку.

    🔴 11.09.2026, зауваження Льоші: «сам віспер в цьому не надійний і далеко не
    завжди сам розділяє». Так і є — межі сегментів whisper ставить за паузами й
    розділовими знаками, а не за тим, хто говорить. Тому кожному СЛОВУ (whisper
    віддає їх з часом) шукаємо голос за серединою слова, а сусідні слова одного
    голосу збираємо назад у репліку. Слів немає — повертаємо рядок як є.

    Дрібні прошарки (одне-два слова чужим голосом усередині чужої фрази) не
    ріжемо: це майже завжди похибка кластеризації на 0,2 с, а не перебивання."""
    try:
        words = _tokens_to_words(row.get("w") or [])
    except (TypeError, ValueError, IndexError):
        return []
    if len(words) < 2:
        return []
    runs: list[tuple] = []
    for a, b, text in words:
        who = _cluster_at(cand, (a + b) / 2.0) or _best_overlap(cand, a, b)
        if runs and runs[-1][0] == who:
            runs[-1][3].append(text)
            runs[-1][2] = b
        else:
            runs.append([who, a, b, [text]])
    if len(runs) < 2:
        return []
    # Склеюємо назад прошарки, коротші за пів секунди й одне слово: вони частіше
    # похибка межі кластера, ніж справжня репліка.
    merged: list[list] = []
    for run in runs:
        who, a, b, text = run
        tiny = (b - a) < 0.5 and len(text) <= 1
        if merged and (tiny or merged[-1][0] == who):
            merged[-1][2] = b
            merged[-1][3].extend(text)
        else:
            merged.append([who, a, b, list(text)])
    if len(merged) < 2:
        return []
    out = []
    for who, a, b, text in merged:
        body = " ".join(t.strip() for t in text if t.strip()).strip()
        body = re.sub(r"\s+([,.!?…:;])", r"\1", body)
        if not body:
            continue
        out.append(
            {
                **{k: v for k, v in row.items() if k != "w"},
                "t0": a,
                "t1": b,
                "text": body,
                "speaker": f"{(who[0] if who else None) or 'mix'}#{who[1]}" if who else None,
            }
        )
    return out if len(out) > 1 else []


def assign_speakers(rows: list[dict], intervals: dict[str, list]) -> list[dict]:
    """Кожному рядку сайдкара — мовця, за перекриттям у часі.

    `intervals` — {джерело: [(t0, t1, кластер)]}; ключ None означає «джерело
    невідоме» (режим без розділення каналів), і тоді дивимось усі доріжки.
    Рядок без міток часу лишається без мовця — це чесніше, ніж вгадати.

    Якщо в рядку встигли поговорити ДВОЄ, а слова з часом є — рядок ріжеться
    по словах (див. `_split_row_by_words`), бо інакше вся репліка дістається
    тому, хто перекрив її більше, і діалог злипається в монолог."""
    out = []
    for r in rows:
        t0, t1, src = r.get("t0"), r.get("t1"), r.get("src")
        if t0 is None or t1 is None:
            out.append({**{k: v for k, v in r.items() if k != "w"}, "speaker": None})
            continue
        pool = intervals.get(src) if src in intervals else None
        cand = (
            [(src, iv) for iv in pool]
            if pool is not None
            else [(s, iv) for s, lst in intervals.items() for iv in lst]
        )
        pieces = _split_row_by_words(r, cand)
        if pieces:
            out.extend(pieces)
            continue
        best = _best_overlap(cand, t0, t1)
        out.append(
            {
                **{k: v for k, v in r.items() if k != "w"},
                "speaker": f"{best[0] or 'mix'}#{best[1]}" if best else None,
            }
        )
    return out


def speaker_names(rows: list[dict], labels: dict[str, str] | None = None) -> dict[str, str]:
    """Стабільні людські імена: нумерація за ПОРЯДКОМ ПЕРШОЇ появи, не за
    внутрішнім номером кластера — «Спікер 1» має бути тим, хто заговорив першим."""
    labels = labels or {}
    order: list[str] = []
    for r in rows:
        s = r.get("speaker")
        if s and s not in order:
            order.append(s)
    names = {}
    for i, key in enumerate(order, 1):
        src = key.split("#", 1)[0]
        if key.endswith("#-1"):
            names[key] = "❓ Невпізнаний"
            continue
        prefix = labels.get(src)
        names[key] = f"{prefix} · Спікер {i}" if prefix else f"Спікер {i}"
    return names


def _clock(sec: float | None) -> str:
    if sec is None:
        return "--:--"
    s = int(sec)
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def render_markdown(rows: list[dict], names: dict[str, str], title: str) -> str:
    """Той самий вигляд, що й у живого транскрипту, але заголовок — мовець.

    Сусідні репліки одного мовця зливаються в один хід: інакше кожні 5 секунд
    зʼявляється новий заголовок і читати неможливо."""
    parts = [f"# {title}\n"]
    cur, buf, start = object(), [], None
    for r in rows:
        key = r.get("speaker")
        if key != cur and buf:
            parts.append(_block(names, cur, start, buf))
            buf = []
        if key != cur:
            cur, start = key, r.get("t0")
        buf.append(r["text"].strip())
    if buf:
        parts.append(_block(names, cur, start, buf))
    return "\n".join(parts)


def _block(names: dict[str, str], key, start, buf: list[str]) -> str:
    who = names.get(key, "❓ Невпізнаний") if key else "❓ Невпізнаний"
    return f"\n**{who} · {_clock(start)}**\n\n" + " ".join(buf) + "\n"


# ── Оркестрація ───────────────────────────────────────────────────────────────
def label_transcript(
    sidecar: Path,
    audio: dict[str, Path],
    out_path: Path | None = None,
    labels: dict[str, str] | None = None,
    progress=None,
    speakers: int = 0,
    gate=None,
    originals: Path | None = None,
) -> Path:
    """Прохід над готовим записом → мовці вписуються в сам транскрипт.

    `audio` — {джерело: wav}; для зустрічі це {"mic": …, "sys": …}, для файлу
    {None: …}. Текст береться із сайдкара, не з `.md`, тож основний файл
    підміняється лише ПІСЛЯ успіху і атомарно (див. `_adopt`). Явний `out_path`
    — старий режим: окремий файл, основний не чіпається."""
    if not _JOB_LOCK.acquire(blocking=False):
        raise RuntimeError("розділення спікерів уже виконується — зачекай, поки завершиться")
    try:
        return _label_locked(sidecar, audio, out_path, labels, progress, speakers, gate, originals)
    finally:
        _JOB_LOCK.release()


def _label_locked(
    sidecar, audio, out_path, labels, progress, speakers=0, gate=None, originals=None
) -> Path:
    meta, rows = read_sidecar(sidecar)
    if not rows:
        raise ValueError("у сайдкарі немає сегментів")
    if not any(r.get("t0") is not None for r in rows):
        # Транскрипт без міток часу — це запис, зроблений до 06.09.2026. Накласти
        # мовців на нього неможливо без окремого проходу по аудіо (ретроспектива).
        raise ValueError("у транскрипті немає міток часу — потрібен ретроспективний прохід")
    live = [
        (src, Path(path))
        for src, path in audio.items()
        if path and Path(path).exists() and Path(path).stat().st_size > 44
    ]
    # Число голосів, назване людиною, — це число на ВЕСЬ запис. Коли каналів
    # два (мікрофон і система), поділити його між ними ми чесно не можемо:
    # накинути «трьох» на кожен канал означало б вигадати шістьох. Тому на
    # двоканальному записі підказка не діє, і кластери шукаються самі.
    per_source = speakers if len(live) == 1 else 0
    intervals = {}
    for src, path in live:
        intervals[src] = diarize_wav(path, progress=progress, speakers=per_source, gate=gate)
    if not intervals:
        raise ValueError("немає аудіо для розділення — запис не зберігся")
    rows = assign_speakers(rows, intervals)
    names = speaker_names(rows, labels)
    title = meta.get("transcript") or sidecar.stem
    text = render_markdown(rows, names, f"{title} — розділено на спікерів")
    stem = sidecar.name.split(".сегменти")[0]
    if out_path is not None:
        out_path.write_text(text, encoding="utf-8")
        return out_path
    md = sidecar.with_name(stem + ".md")
    if not md.exists():
        # Основного файлу вже нема (прибрали руками) — підміняти нічого, пишемо поруч.
        out = sidecar.with_name(stem + ".спікери.md")
        out.write_text(text, encoding="utf-8")
        return out
    return _adopt(md, text, originals if originals is not None else originals_dir())


ORIGINALS_DIRNAME = "whisper-originals"


def originals_dir() -> Path:
    """Тимчасовий буфер сирих розшифровок віспера — страховка, поки розділення
    вписується прямо в транскрипт. 🔴 11.09.2026, рішення Льоші: дубль
    `.спікери.md` прибрано, але оригінал не губимо, доки ревізія не підтвердить,
    що підміна нічого не з'їдає. Лежить поза текою транскриптів, щоб не
    плутатись поруч із ними."""
    from .paths import data_dir

    return data_dir() / ORIGINALS_DIRNAME


def _adopt(md: Path, text: str, originals: Path) -> Path:
    """Вписати розділений текст у сам `.md`, зберігши сиру версію в буфері.

    Порядок має значення: спершу копія оригіналу, потім тимчасовий файл, потім
    атомарна підміна. Збій на будь-якому кроці лишає на місці звичайний
    транскрипт. Копія не перезаписується: повторний прохід не має права
    замінити сирий віспер уже розділеною версією."""
    originals.mkdir(parents=True, exist_ok=True)
    keep = originals / md.name
    if not keep.exists():
        shutil.copy2(md, keep)
    tmp = md.with_name(md.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, md)
    return md
