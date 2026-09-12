"""Смок-тести розділення спікерів (фіча 06.09.2026).

Моделі й `sherpa_onnx` тут НЕ потрібні: перевіряється все, що може відвалитись
мовчки — вибір інтерпретатора для докачки, цілісність завантаження, накладання
мовців на текст і те, як воно виглядає у файлі.
"""

import io
import json
import sys
from typing import ClassVar

import pytest
from src import diarize


# ── Докачка: те, що ламається на ЧУЖОМУ маку ─────────────────────────────────
def test_pip_target_is_the_venv_the_app_actually_reads(tmp_path, monkeypatch):
    """🔴 Головний запобіжник установки в інших (Катя, Аня).

    Встановлена `/Applications/Pysar.app` запускає копію фреймворкового Python зі
    свого бандла, а venv підмішує через PYSAR_SITE. Якби докачка йшла в
    sys.executable, пакет ліг би повз той site-packages, який апка читає:
    кнопка звітує «готово», а розділення не працює."""
    venv = tmp_path / "venv"
    site = venv / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("#!/bin/sh\n")
    monkeypatch.setenv("PYSAR_SITE", str(site))
    assert diarize.venv_python() == str(venv / "bin" / "python")


def test_pip_target_falls_back_to_the_running_interpreter(tmp_path, monkeypatch):
    """Без PYSAR_SITE і без venv поруч — краще поставити хоч кудись, ніж упасти."""
    monkeypatch.setenv("PYSAR_SITE", str(tmp_path / "nope" / "lib" / "py" / "site-packages"))
    monkeypatch.setattr(diarize.site, "getsitepackages", lambda: [])
    monkeypatch.setattr(diarize.sys, "prefix", str(tmp_path / "empty"))
    assert diarize.venv_python() == sys.executable


def test_truncated_download_is_deleted_not_kept(tmp_path, monkeypatch):
    """Недокачаний .onnx — найгірший сценарій: файл на місці, «модель є», а
    падає воно значно пізніше й незрозуміло. Розмір звіряється з Content-Length."""

    class _Resp(io.BytesIO):
        headers: ClassVar[dict] = {"Content-Length": "1000"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(diarize.urllib.request, "urlopen", lambda *a, **k: _Resp(b"x" * 10))
    dest = tmp_path / "model.onnx"
    with pytest.raises(OSError, match="не повністю"):
        diarize._download("https://example/model.onnx", dest)
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part"))


def test_archive_cannot_write_outside_the_models_folder():
    """Архів із мережі не має права розкластись куди захоче."""
    assert diarize._safe_member("sherpa-onnx-pyannote/model.onnx")
    assert not diarize._safe_member("/etc/passwd")
    assert not diarize._safe_member("../../../.zshrc")


def test_install_refuses_when_the_disk_is_full(monkeypatch):
    monkeypatch.setattr(diarize, "have_engine", lambda: False)
    monkeypatch.setattr(diarize, "have_models", lambda: False)
    monkeypatch.setattr(diarize, "_free_mb", lambda _p: 12.0)
    ok, msg = diarize.ensure_ready()
    assert not ok
    assert "місця" in msg


def test_status_shape():
    st = diarize.status()
    assert set(st) >= {"engine", "models", "ready", "download_mb"}
    assert st["ready"] == (st["engine"] and st["models"])


# ── Сайдкар і накладання ─────────────────────────────────────────────────────
def _sidecar(tmp_path, rows, meta=None):
    p = tmp_path / "t.сегменти.jsonl"
    lines = [json.dumps({"_meta": meta or {"transcript": "t.md"}}, ensure_ascii=False)]
    lines += [json.dumps(r, ensure_ascii=False) for r in rows]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_broken_sidecar_line_does_not_lose_the_rest(tmp_path):
    """Сайдкар пишеться на живому записі й може обірватись на будь-якому байті —
    один битий рядок не має права з'їсти весь файл."""
    p = _sidecar(tmp_path, [{"i": 0, "t0": 0.0, "t1": 1.0, "text": "перший"}])
    with open(p, "a", encoding="utf-8") as f:
        f.write('{"i": 1, "t0": 1.0, "t1": 2\n')  # обірваний
        f.write(json.dumps({"i": 2, "t0": 2.0, "t1": 3.0, "text": "третій"}) + "\n")
    meta, rows = diarize.read_sidecar(p)
    assert meta["transcript"] == "t.md"
    assert [r["text"] for r in rows] == ["перший", "третій"]


def test_speaker_is_the_voice_with_the_biggest_overlap():
    rows = [
        {"t0": 0.0, "t1": 2.0, "src": "sys", "text": "а"},
        {"t0": 5.0, "t1": 7.0, "src": "sys", "text": "б"},
    ]
    ivs = {"sys": [(0.0, 3.0, 0), (4.0, 8.0, 1)]}
    out = diarize.assign_speakers(rows, ivs)
    assert [r["speaker"] for r in out] == ["sys#0", "sys#1"]


def test_row_without_timestamps_stays_without_a_speaker():
    """Старий транскрипт без міток часу — чесніше лишити без мовця, ніж вгадати."""
    out = diarize.assign_speakers([{"t0": None, "t1": None, "src": None, "text": "х"}], {"sys": []})
    assert out[0]["speaker"] is None


def test_unknown_source_looks_at_every_track():
    """Режим без розділення каналів пише src=None — тоді дивимось усі доріжки."""
    rows = [{"t0": 1.0, "t1": 2.0, "src": None, "text": "х"}]
    out = diarize.assign_speakers(rows, {"mic": [(0.5, 3.0, 4)]})
    assert out[0]["speaker"] == "mic#4"


def test_speakers_are_numbered_by_who_spoke_first():
    """«Спікер 1» має бути тим, хто заговорив першим, а не тим, кому кластер
    дав менший внутрішній номер."""
    rows = [{"speaker": "sys#7"}, {"speaker": "sys#2"}, {"speaker": "sys#7"}]
    names = diarize.speaker_names(rows)
    assert names["sys#7"].endswith("Спікер 1")
    assert names["sys#2"].endswith("Спікер 2")


def test_unrecognized_cluster_is_marked_not_merged():
    """Мікро-кластер іде у «❓», а не тоне в сусідньому голосі: злити двох
    людина може одним рухом, розчепити злиплих — ніяк."""
    names = diarize.speaker_names([{"speaker": "mic#-1"}])
    assert names["mic#-1"].startswith("❓")


def test_channel_label_survives_into_the_name():
    """Два голоси в доріжці — імʼя доріжки плюс номер.

    🔴 12.09.2026: раніше номер стояв і на одному голосі («Ти · Спікер 1»).
    Тепер одинак називається самою доріжкою — див.
    TestMicTrackIsOneVoice.test_single_voice_track_keeps_its_own_name."""
    names = diarize.speaker_names([{"speaker": "mic#0"}, {"speaker": "mic#1"}], {"mic": "Ти"})
    assert names["mic#0"] == "Ти · Спікер 1"
    assert names["mic#1"] == "Ти · Спікер 2"


def test_consecutive_turns_of_one_voice_merge_into_one_block():
    """Інакше заголовок з'являється кожні 5 секунд і читати неможливо."""
    rows = [
        {"t0": 0.0, "speaker": "s#0", "text": "раз"},
        {"t0": 3.0, "speaker": "s#0", "text": "два"},
        {"t0": 9.0, "speaker": "s#1", "text": "три"},
    ]
    md = diarize.render_markdown(rows, diarize.speaker_names(rows), "тест")
    assert md.count("**") == 4  # два заголовки
    assert "раз два" in md
    assert "0:00:00" in md and "0:00:09" in md


def test_audio_map_reads_the_source_from_the_dump_name():
    m = diarize.audio_map(["/x/2026-09-06-sys.wav", "/x/2026-09-06-mic.wav", "/x/random.wav"])
    assert set(m) == {"sys", "mic"}
    assert m["mic"].name.endswith("-mic.wav")


def test_transcript_without_timestamps_is_refused_with_a_reason(tmp_path, monkeypatch):
    """Запис, зроблений до появи міток часу, не можна розділити на місці — і про
    це треба сказати словами, а не мовчки видати порожній файл."""
    p = _sidecar(tmp_path, [{"i": 0, "t0": None, "t1": None, "text": "старе"}])
    wav = tmp_path / "a-sys.wav"
    wav.write_bytes(b"\0" * 100)
    with pytest.raises(ValueError, match="міток часу"):
        diarize.label_transcript(p, {"sys": wav})


def test_second_job_is_refused_while_one_is_running(tmp_path):
    """8 ГБ: дві діаризації одночасно кладуть машину у своп."""
    p = _sidecar(tmp_path, [{"i": 0, "t0": 0.0, "t1": 1.0, "text": "x"}])
    diarize._JOB_LOCK.acquire()
    try:
        with pytest.raises(RuntimeError, match="уже виконується"):
            diarize.label_transcript(p, {"sys": tmp_path / "a-sys.wav"})
    finally:
        diarize._JOB_LOCK.release()


def test_a_single_network_hiccup_does_not_cost_the_whole_download(monkeypatch):
    """Установка Pysar на Intel Air 2015 вже падала на разовому DNS-збої
    (13.07.2026) — повтор має вижити."""
    monkeypatch.setattr(diarize.time, "sleep", lambda _s: None)
    tries = []

    def flaky():
        tries.append(1)
        if len(tries) < 3:
            raise OSError("Could not resolve host")
        return "ok"

    assert diarize._retry(flaky) == "ok"
    assert len(tries) == 3


def test_retry_gives_up_and_reports_the_real_error(monkeypatch):
    monkeypatch.setattr(diarize.time, "sleep", lambda _s: None)

    def dead():
        raise OSError("мережі немає")

    with pytest.raises(OSError, match="мережі немає"):
        diarize._retry(dead)


# ── Ручний вибір кількості голосів (06.09.2026) ───────────────────────────────
class TestManualSpeakerCount:
    """Людина, яка була в кімнаті, знає число голосів краще за рушій.

    Число застосовується на ГЛОБАЛЬНИХ центроїдах — після зшивання вікон, а не
    в конфізі кластеризатора: там воно діяло б на кожні 30 хв окремо й розпилило
    б одного мовця на трьох у тихому вікні."""

    @staticmethod
    def _vecs(np):
        # Дві пари майже однакових голосів: чесне злиття має дати рівно 2.
        raw = [[1.0, 0.0], [0.99, 0.14], [0.0, 1.0], [0.14, 0.99]]
        out = []
        for v in raw:
            a = np.array(v, dtype=np.float32)
            out.append(a / np.linalg.norm(a))
        return out

    def test_merges_nearest_voices_down_to_the_requested_count(self):
        np = pytest.importorskip("numpy")
        gcent = self._vecs(np)
        glob = {f"w0_{i}": i for i in range(4)}
        new_glob, new_cent = diarize._merge_to(glob, gcent, 2, np)
        assert len(new_cent) == 2
        assert sorted(set(new_glob.values())) == [0, 1]
        # Пари не мають розʼїхатись по різних мовцях.
        assert new_glob["w0_0"] == new_glob["w0_1"]
        assert new_glob["w0_2"] == new_glob["w0_3"]

    def test_fewer_voices_than_asked_are_left_alone(self):
        # Рушій знайшов двох, людина сказала «четверо» — вигадувати нікого.
        np = pytest.importorskip("numpy")
        gcent = self._vecs(np)[:2]
        glob = {"w0_0": 0, "w0_1": 1}
        new_glob, new_cent = diarize._merge_to(glob, gcent, 4, np)
        assert len(new_cent) == 2
        assert new_glob == {"w0_0": 0, "w0_1": 1}


# ── 11.09.2026: злиття до заданої кількості голосів ───────────────────────────
# 🔴 Поле: запис двох стрімерів. Людина сказала «2 голоси», і саме через це
# розділення зламалося — обидва живі голоси злилися в одного, а окремим
# «спікером» лишився секундний шматок музики. Причина: зливалася найсхожіша
# ПАРА, а два голоси з однієї трансляції схожі між собою більше, ніж будь-хто
# з них — на музику. Тепер поглинається НАЙКОРОТШИЙ голос.


def _unit(np, *xs):
    v = np.array(xs, dtype="float32")
    return v / (np.linalg.norm(v) + 1e-9)


def test_merge_keeps_the_two_talkers_and_absorbs_the_short_noise():
    np = pytest.importorskip("numpy")
    # 0 і 1 — люди (схожі між собою), 2 — коротке сміття збоку.
    cents = [_unit(np, 1.0, 0.1, 0.0), _unit(np, 0.9, 0.2, 0.0), _unit(np, 0.0, 0.0, 1.0)]
    glob = {"a": 0, "b": 1, "c": 2}
    dur = {0: 60.0, 1: 45.0, 2: 1.0}
    out, cents2 = diarize._merge_to(glob, cents, 2, np, dur)
    assert len(cents2) == 2
    assert out["a"] != out["b"], "двох мовців злили в одного — та сама бага 11.09"
    assert out["c"] in (out["a"], out["b"]), "сміття мусить кудись вкластися"


def test_merge_without_durations_still_returns_exactly_k():
    """Старі виклики без тривалостей мають працювати, а не падати."""
    np = pytest.importorskip("numpy")
    cents = [_unit(np, 1.0, 0.0), _unit(np, 0.9, 0.1), _unit(np, 0.0, 1.0)]
    out, cents2 = diarize._merge_to({"a": 0, "b": 1, "c": 2}, cents, 2, np)
    assert len(cents2) == 2
    assert len(set(out.values())) == 2


def test_merge_never_invents_more_voices_than_found():
    np = pytest.importorskip("numpy")
    cents = [_unit(np, 1.0, 0.0)]
    out, cents2 = diarize._merge_to({"a": 0}, cents, 3, np, {0: 10.0})
    assert len(cents2) == 1 and out == {"a": 0}


# ── 11.09.2026: різання рядка по словах там, де змінився голос ────────────────
# Льоша: «сам віспер в цьому не надійний і далеко не завжди сам розділяє».
# Саме так: whisper ставить межі за паузами, а не за мовцями.


def _row_with_words(words):
    return {
        "i": 0,
        "t0": words[0][0],
        "t1": words[-1][1],
        "src": "sys",
        "clock": "21:05:20",
        "text": " ".join(w[2].strip() for w in words),
        "w": [list(w) for w in words],
    }


def test_one_row_with_two_voices_is_cut_at_the_word_where_the_voice_changes():
    row = _row_with_words(
        [
            (0.0, 0.2, " Як"),
            (0.2, 0.5, " вас"),
            (0.5, 0.9, " звати"),
            (0.9, 1.0, "?"),
            (1.0, 1.5, " Марина"),
            (1.5, 2.0, "."),
        ]
    )
    intervals = {"sys": [(0.0, 0.99, 0), (1.0, 2.0, 1)]}
    out = diarize.assign_speakers([row], intervals)
    assert [r["text"] for r in out] == ["Як вас звати?", "Марина."]
    assert [r["speaker"] for r in out] == ["sys#0", "sys#1"]


def test_a_row_in_one_voice_stays_one_row():
    row = _row_with_words([(0.0, 0.4, " Друзі"), (0.4, 0.9, " привіт"), (0.9, 1.2, ".")])
    out = diarize.assign_speakers([row], {"sys": [(0.0, 5.0, 0)]})
    assert len(out) == 1
    assert out[0]["speaker"] == "sys#0"
    assert "w" not in out[0]  # службові слова не тягнемо в результат


def test_a_single_stray_word_does_not_cut_the_line():
    # Одне слово чужим кластером на 0,2 с — це майже завжди похибка межі,
    # а не перебивання; різати на цьому не можна, бо репліки розсиплються.
    row = _row_with_words(
        [(0.0, 0.4, " Я"), (0.4, 0.6, " мала"), (0.6, 0.8, " йти"), (0.8, 1.4, " далі")]
    )
    intervals = {"sys": [(0.0, 0.45, 0), (0.45, 0.62, 1), (0.62, 2.0, 0)]}
    out = diarize.assign_speakers([row], intervals)
    assert len(out) == 1
    assert out[0]["text"] == "Я мала йти далі"


def test_rows_without_words_keep_the_old_behaviour():
    row = {"i": 0, "t0": 0.0, "t1": 2.0, "src": "sys", "clock": "21:05:20", "text": "текст"}
    out = diarize.assign_speakers([row], {"sys": [(0.0, 1.9, 3)]})
    assert out == [{**row, "speaker": "sys#3"}]


def _adoptable(tmp_path, monkeypatch):
    side = tmp_path / "transcript_x.сегменти.jsonl"
    rows = [
        {"_meta": {"transcript": "transcript_x.md"}},
        {"i": 0, "t0": 0.0, "t1": 1.0, "src": "sys", "text": "привіт"},
        {"i": 1, "t0": 1.0, "t1": 2.0, "src": "sys", "text": "бувай"},
    ]
    side.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )
    md = tmp_path / "transcript_x.md"
    md.write_text("# сирий віспер\n", encoding="utf-8")
    wav = tmp_path / "a-sys.wav"
    wav.write_bytes(b"\0" * 100)
    monkeypatch.setattr(diarize, "diarize_wav", lambda *a, **k: [(0.0, 1.0, 0), (1.0, 2.0, 1)])
    return side, md, wav


def test_speakers_go_into_the_transcript_itself_and_raw_whisper_is_buffered(tmp_path, monkeypatch):
    """11.09.2026: дубль `.спікери.md` прибрано — але сирий віспер лежить у буфері."""
    side, md, wav = _adoptable(tmp_path, monkeypatch)
    buf = tmp_path / "originals"
    out = diarize.label_transcript(side, {"sys": wav}, originals=buf)
    assert out == md
    assert "розділено на спікерів" in md.read_text(encoding="utf-8")
    assert (buf / md.name).read_text(encoding="utf-8") == "# сирий віспер\n"
    assert not list(tmp_path.glob("*.спікери.md"))
    assert not list(tmp_path.glob("*.tmp"))


def test_second_pass_never_overwrites_the_buffered_raw_whisper(tmp_path, monkeypatch):
    side, md, wav = _adoptable(tmp_path, monkeypatch)
    buf = tmp_path / "originals"
    diarize.label_transcript(side, {"sys": wav}, originals=buf)
    diarize.label_transcript(side, {"sys": wav}, originals=buf)
    assert (buf / md.name).read_text(encoding="utf-8") == "# сирий віспер\n"


def test_failed_split_leaves_the_transcript_untouched(tmp_path, monkeypatch):
    side, md, wav = _adoptable(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise RuntimeError("рушій упав")

    monkeypatch.setattr(diarize, "diarize_wav", boom)
    with pytest.raises(RuntimeError):
        diarize.label_transcript(side, {"sys": wav}, originals=tmp_path / "originals")
    assert md.read_text(encoding="utf-8") == "# сирий віспер\n"
    assert not (tmp_path / "originals").exists()


def test_whisper_tokens_are_glued_back_into_words_before_cutting():
    # Справжній рядок сайдкара 11.09.2026: whisper віддає токени, не слова.
    from src.diarize import _tokens_to_words

    w = [
        [0.02, 0.08, " Д"],
        [0.08, 0.16, "е"],
        [0.16, 0.24, ","],
        [0.95, 1.3, " брат"],
        [1.3, 1.47, "ик"],
        [1.47, 1.53, ","],
        [1.58, 1.71, " зд"],
        [1.72, 1.89, "ор"],
        [1.89, 2.13, "ова"],
        [2.23, 2.23, "."],
    ]
    got = _tokens_to_words(w)
    assert [t.strip() for _, _, t in got] == ["Де,", "братик,", "здорова."]
    assert got[1][:2] == (0.95, 1.53)


# ── мікрофон — один голос за конструкцією (12.09.2026) ────────────────────────
class TestMicTrackIsOneVoice:
    """🔴 З тесту Льоші 12.09.2026: «діаризатор нахуячив невпізнаваних спікерів».

    Доріжка мікрофона фізично містить одного мовця — власника мака. Шукати в
    ній голоси кластеризацією означає знаходити дихання, ехо й 0,3-секундні
    уривки, які потім стають «❓ Невпізнаний» просто тому, що короткі."""

    def _sidecar(self, tmp_path, rows):
        p = tmp_path / "зустріч.сегменти.jsonl"
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": {"transcript": "зустріч"}}, ensure_ascii=False) + "\n")
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return p

    def _wav(self, path, seconds=30.0):
        path.write_bytes(b"\0" * 44 + b"\1\0" * int(16000 * seconds))
        return path

    def test_mic_track_is_never_clustered(self, tmp_path, monkeypatch):
        """Рушій не має права навіть запуститись на мікрофоні: це і хибні
        спікери, і дарма витрачений прохід по аудіо на 8 ГБ."""
        called: list = []
        monkeypatch.setattr(
            diarize, "diarize_wav", lambda path, **kw: called.append(path) or [(0.0, 30.0, 0)]
        )
        sidecar = self._sidecar(
            tmp_path,
            [
                {"t0": 1.0, "t1": 3.0, "src": "mic", "text": "це я кажу"},
                {"t0": 4.0, "t1": 6.0, "src": "mic", "text": "і це теж я"},
                {"t0": 7.0, "t1": 9.0, "src": "sys", "text": "а це співрозмовник"},
            ],
        )
        out = diarize.label_transcript(
            sidecar,
            {
                "mic": self._wav(tmp_path / "з-mic.wav"),
                "sys": self._wav(tmp_path / "з-sys.wav"),
            },
            out_path=tmp_path / "готово.md",
            labels={"mic": "Ви", "sys": "Система"},
        )
        assert [p.name for p in called] == ["з-sys.wav"], "мікрофон пішов у кластеризацію"
        text = out.read_text(encoding="utf-8")
        assert "❓" not in text, "на мікрофоні з'явився невпізнаний спікер"
        assert "це я кажу" in text and "і це теж я" in text

    def test_single_voice_track_keeps_its_own_name(self):
        """Один голос у доріжці ⇒ без номера: «Ви», а не «Ви · Спікер 2»."""
        rows = [
            {"speaker": "sys#0"},
            {"speaker": "sys#1"},
            {"speaker": "mic#0"},
        ]
        names = diarize.speaker_names(rows, {"sys": "Система", "mic": "Ви"})
        assert names["mic#0"] == "Ви"
        assert names["sys#0"] == "Система · Спікер 1"
        assert names["sys#1"] == "Система · Спікер 2"

    def test_track_seconds_from_file_size(self, tmp_path):
        assert diarize._track_seconds(self._wav(tmp_path / "a.wav", 12.5)) == 12.5
        assert diarize._track_seconds(tmp_path / "нема.wav") == 0.0
