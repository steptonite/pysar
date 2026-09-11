"""Межі сегментів у секундах (фіча «мітки секунд», 06.09.2026).

Час беремо з лічильника записаних семплів у сирому дампі, а НЕ з годинника:
сегмент розшифровується через секунди після того, як прозвучав, тому
datetime.now() у воркері показує час розшифровки, а не час репліки. Саме ці межі
потім дозволяють розділити спікерів без перерозшифровки.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only backend")


class _FakeDump:
    def __init__(self, frames):
        self.frames = frames


def _rec(sys_frames=None, mic_frames=None):
    from pysar.syscap import SystemAudioRecorder

    r = object.__new__(SystemAudioRecorder)
    r._dump_sys = _FakeDump(sys_frames) if sys_frames is not None else None
    r._dump_mic = _FakeDump(mic_frames) if mic_frames is not None else None
    return r


def test_span_counts_from_the_source_own_dump():
    from pysar.syscap import SAMPLE_RATE

    # 10 с системного звуку записано, останній сегмент — 2 с.
    r = _rec(sys_frames=10 * SAMPLE_RATE, mic_frames=3 * SAMPLE_RATE)
    assert r._span("sys", 2 * SAMPLE_RATE) == (8.0, 10.0)
    # Мік іде своїм лічильником, не системним.
    assert r._span("mic", SAMPLE_RATE) == (2.0, 3.0)


def test_span_never_goes_negative_at_the_very_start():
    from pysar.syscap import SAMPLE_RATE

    # Сегмент довший за все, що встигло записатись (перший буфер) — t0 не від'ємне.
    r = _rec(sys_frames=SAMPLE_RATE)
    assert r._span("sys", 5 * SAMPLE_RATE) == (0.0, 1.0)


def test_span_falls_back_to_the_longest_dump_when_source_is_unknown():
    from pysar.syscap import SAMPLE_RATE

    # Змішаний режим не тегує джерело: беремо найдовший дамп як шкалу часу.
    r = _rec(sys_frames=4 * SAMPLE_RATE, mic_frames=9 * SAMPLE_RATE)
    assert r._span(None, SAMPLE_RATE) == (8.0, 9.0)


def test_span_is_none_before_anything_is_recorded():
    # Дампів нема (запис без збереження аудіо) → меж нема, транскрипт пишеться як був.
    assert _rec()._span("sys", 16000) is None
    assert _rec(sys_frames=0)._span("sys", 16000) is None


class TestSegmentSurvivesSpanFailure:
    """🔴 Регресія 06.09.2026: мітка часу вбила ВЕСЬ звук.

    `_span` стоїть аргументом усередині `suppress(Exception)`, що обгортає
    `_on_segment`. Сегментер віддає `bytes`, а код кликав `.size` (numpy) —
    AttributeError на кожному сегменті, і 4 хвилини мовлення пішли в нікуди
    при повних 7 МБ сирого аудіо на диску. Тести тоді були зелені, бо
    підсовували numpy замість байтів.
    """

    @staticmethod
    def _rec():
        from pysar import syscap

        r = syscap.SystemAudioRecorder(capture_mic=True, source_mode="smart")
        got = []
        r._on_segment = lambda wav, src, span: got.append((src, span))
        return r, got

    def test_segments_reach_the_transcript_when_the_segmenter_returns_bytes(self):
        import numpy as np

        r, got = self._rec()

        class ByteSeg:
            def feed(self, block):
                return b"\x00\x01" * 800  # рівно те, що віддає справжній сегментер

        r._feed_source_locked(ByteSeg(), "_acc_sys", "sys", np.ones(4096, dtype=np.float32))
        assert got, "сегмент загубився — мітка часу не сміє коштувати звуку"

    def test_a_broken_span_costs_the_mark_not_the_segment(self):
        import numpy as np

        r, got = self._rec()

        class ByteSeg:
            def feed(self, block):
                return b"\x00\x01" * 800

        # Дамп, який кидає на .frames — найгірший випадок для обчислення мітки.
        class Exploding:
            @property
            def frames(self):
                raise RuntimeError("дамп зламався")

        r._dump_sys = Exploding()
        r._feed_source_locked(ByteSeg(), "_acc_sys", "sys", np.ones(4096, dtype=np.float32))
        assert got, "сегмент мусить пройти навіть коли мітку порахувати неможливо"
        assert got[0][1] is None, "зламана мітка = None, а не виняток"

    def test_span_counts_bytes_as_samples_not_as_bytes(self):

        r, _ = self._rec()

        class Dump:
            frames = 16000  # рівно секунда вже на диску

        r._dump_sys = Dump()
        span = r._span("sys", b"\x00\x01" * 8000)  # 8000 семплів = 0.5 с
        assert span == (0.5, 1.0), f"очікував (0.5, 1.0), отримав {span}"
        assert span == r._span("sys", __import__("numpy").zeros(8000, dtype="float32"))


# ── Межі бере САМ сегментер ───────────────────────────────────────────────────
# 🔴 Рахунок по дампу (усе вище) давав мітки, які НАКЛАДАЛИСЬ: між кінцем фрази
# і видачею сегмента у дамп натікав ще звук. У записі 06.09.2026 сусідні репліки
# перекривались на 10-20 с, і розділення голосів ставило одного «Спікера 1» на
# відрізок, де говорили троє. Тепер позицію рахує той, хто різав.


class _FakeSeg:
    def __init__(self, span):
        self.last_span_samples = span


def test_span_comes_from_the_segmenter_not_from_the_dump():
    from pysar.syscap import SAMPLE_RATE

    r = _rec(sys_frames=90 * SAMPLE_RATE)  # у дамп уже натекло 90 с
    r._source_mode = "smart"
    r._seg_sys = _FakeSeg((10 * SAMPLE_RATE, 14 * SAMPLE_RATE))
    r._seg_mic = None
    # Сегмент прозвучав на 10-14 с, а не «останні 4 с перед 90-ю».
    assert r._span("sys", 4 * SAMPLE_RATE) == (10.0, 14.0)


def test_neighbouring_segments_do_not_overlap():
    from pysar.segmenter import Segmenter

    import numpy as np

    rate, block = 16000, 1600
    seg = Segmenter(
        sample_rate=rate,
        block_size=block,
        pause_sec=0.3,
        min_seg_sec=0.2,
        max_seg_sec=30.0,
        silence_margin=3.0,
    )
    loud = np.full(block, 0.3, dtype=np.float32)
    quiet = np.zeros(block, dtype=np.float32)
    spans = []
    for _wave in range(3):
        for _ in range(8):
            if seg.feed(loud) is not None:
                spans.append(seg.last_span_samples)
        for _ in range(8):
            if seg.feed(quiet) is not None:
                spans.append(seg.last_span_samples)
    assert len(spans) >= 2, spans
    assert spans[1][0] >= spans[0][1], f"межі наклались: {spans}"


def test_span_skips_the_silence_the_segmenter_dropped():
    from pysar.segmenter import Segmenter

    import numpy as np

    rate, block = 16000, 1600
    seg = Segmenter(
        sample_rate=rate,
        block_size=block,
        pause_sec=0.3,
        min_seg_sec=0.2,
        max_seg_sec=30.0,
        silence_margin=3.0,
    )
    quiet = np.zeros(block, dtype=np.float32)
    loud = np.full(block, 0.3, dtype=np.float32)
    for _ in range(10):  # секунда тиші на початку — вона НЕ частина репліки
        seg.feed(quiet)
    for _ in range(5):
        seg.feed(loud)
    for _ in range(6):
        seg.feed(quiet)
    assert seg.last_span_samples is not None
    assert seg.last_span_samples[0] >= 10 * block
