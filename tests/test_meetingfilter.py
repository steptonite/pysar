"""meetingfilter.MeetingFilter — echo dedup, hum filter, language outliers.

All cases are driven with explicit `now` timestamps so nothing sleeps."""

from src.meetingfilter import MeetingFilter


def _keep(f, text, source="mic", meta=None, now=0.0):
    return f.verdict(text, source, meta or {}, now=now) is None


class TestEchoDedup:
    def test_cross_channel_duplicate_dropped(self):
        f = MeetingFilter()
        assert _keep(f, "Слушай, ну я начала разбираться с сайтами и так далее", "sys", now=0)
        # The mic-bleed copy: same speech, slightly different ASR wording.
        assert not _keep(f, "Слушай ну я да начала разбираться там с сайтами", "mic", now=3)

    def test_time_shifted_partial_segment_dropped(self):
        # The two segmenters cut at different pauses: the mic copy holds a
        # fragment of a longer sys utterance.
        f = MeetingFilter()
        assert _keep(
            f,
            "Изначально у меня появилось желание потому что ко мне пришел мой отец "
            "и попросил меня найти кого-то кто ему сделает сайт",
            "sys",
            now=0,
        )
        assert not _keep(f, "ко мне пришел мой отец и попросил найти кого-то", "mic", now=4)

    def test_same_channel_repeat_kept(self):
        # A speaker literally repeating themselves is content, not echo.
        f = MeetingFilter()
        assert _keep(f, "давай попробуем еще раз сначала", "mic", now=0)
        assert _keep(f, "давай попробуем еще раз сначала", "mic", now=3)

    def test_short_backchannel_never_deduped(self):
        f = MeetingFilter()
        assert _keep(f, "да, угу", "sys", now=0)
        assert _keep(f, "да, угу", "mic", now=1)

    def test_echo_window_expires(self):
        f = MeetingFilter()
        assert _keep(f, "эта фраза была сказана давным давно в начале", "sys", now=0)
        assert _keep(f, "эта фраза была сказана давным давно в начале", "mic", now=60)

    def test_unrelated_text_kept(self):
        f = MeetingFilter()
        assert _keep(f, "покажи мне пожалуйста свой экран сейчас", "sys", now=0)
        assert _keep(f, "у меня два монитора и я не понимаю какой", "mic", now=2)


class TestHumFilter:
    def test_no_speech_segment_dropped(self):
        f = MeetingFilter()
        assert not _keep(f, "Mm-hmm", meta={"no_speech": 0.9})

    def test_low_confidence_blurb_dropped(self):
        f = MeetingFilter()
        assert not _keep(f, "Obrigada", meta={"logprob": -1.5})

    def test_low_confidence_long_text_kept(self):
        # Long low-logprob text is real speech in bad audio, not a hum.
        f = MeetingFilter()
        assert _keep(
            f,
            "довге речення яке точно не є мугиканням бо в ньому багато слів",
            meta={"logprob": -1.5},
        )

    def test_confident_speech_kept(self):
        f = MeetingFilter()
        assert _keep(f, "нормальна впевнена фраза", meta={"no_speech": 0.01, "logprob": -0.2})

    def test_missing_meta_keeps(self):
        f = MeetingFilter()
        assert _keep(f, "фраза без метаданих взагалі", meta={})


class TestLangOutlier:
    def _seed_russian(self, f, now=0.0):
        for i, text in enumerate(
            [
                "мы сейчас обсуждаем как сделать презентацию в клоде",
                "давай посмотрим на твой экран и разберемся вместе",
                "ну вот смотри тут у тебя открывается новая сессия",
            ]
        ):
            assert _keep(f, text, "mic", {"lang": "ru", "lang_prob": 0.95}, now=now + i)

    def test_short_foreign_outlier_dropped(self):
        f = MeetingFilter(auto_lang=True)
        self._seed_russian(f)
        assert not _keep(
            f,
            "Normalmente fai un simbiettello",
            "sys",
            {"lang": "it", "lang_prob": 0.6},
            now=10,
        )

    def test_long_foreign_passage_kept(self):
        # A real switch to another language: long enough to be trusted.
        f = MeetingFilter(auto_lang=True)
        self._seed_russian(f)
        assert _keep(
            f,
            "let us get right into it whether you want a rooftop dinner "
            "or just keep the vibes completely casual let me know",
            "sys",
            {"lang": "en", "lang_prob": 0.8},
            now=10,
        )

    def test_confident_short_foreign_kept(self):
        f = MeetingFilter(auto_lang=True)
        self._seed_russian(f)
        assert _keep(f, "just do it", "sys", {"lang": "en", "lang_prob": 0.99}, now=10)

    def test_disabled_outside_auto_mode(self):
        f = MeetingFilter(auto_lang=False)
        self._seed_russian(f)
        assert _keep(f, "Obrigada muito", "sys", {"lang": "pt", "lang_prob": 0.6}, now=10)

    def test_no_dominant_before_votes(self):
        f = MeetingFilter(auto_lang=True)
        assert _keep(f, "Obrigada muito boa", "sys", {"lang": "pt", "lang_prob": 0.6}, now=0)
