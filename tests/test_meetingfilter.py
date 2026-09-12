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


class TestEchoDirection:
    """🔴 12.09.2026, лог Каті. Ехо однонапрямлене: динаміки → мікрофон.
    Раніше фільтр був симетричний і викидав те, що прийшло другим у чергу,
    тому в неї полетіла системна доріжка, а ехо в мікрофоні лишилось жити."""

    def test_system_channel_is_never_dropped_as_echo(self):
        f = MeetingFilter()
        assert _keep(f, "надроченный скилл в плане чего находить выход", "mic", now=0)
        # The same words arriving on the system channel are the ORIGINAL.
        assert _keep(f, "надроченный скилл в плане чего находить выход", "sys", now=2)

    def test_mic_echo_of_system_still_dropped(self):
        f = MeetingFilter()
        assert _keep(f, "надроченный скилл в плане чего находить выход", "sys", now=0)
        assert not _keep(f, "надроченный скилл в плане чего находить выход", "mic", now=2)


class TestEchoCutWithinBlock:
    """Ехо буває лише ЧАСТИНОЮ блоку — тоді ріжемо сегменти, а не блок."""

    def _seg(self, text, t0=0.0):
        return {"t0": t0, "t1": t0 + 1.0, "text": text}

    def _block(self, f, segs, source="mic", now=0.0):
        """whisper віддає блок як склейку своїх сегментів — так і подаємо."""
        text = " ".join(s["text"] for s in segs)
        return f.review(text, source, {"segments": segs}, now=now)

    def test_echo_segment_cut_and_own_speech_kept(self):
        f = MeetingFilter()
        f.verdict("невозможно взять и применить один и тот же подход ко всем", "sys", {}, now=0)
        text, parts, reason = self._block(
            f,
            [
                self._seg("Типа применить один и тот же подход ко всем.", 12.2),
                self._seg("Надроченный скилл в плане чего?", 16.9),
                self._seg("Типа находить выход из любой ситуации?", 19.4),
            ],
            now=3,
        )
        assert reason is None
        assert len(parts) == 2
        assert "подход ко всем" not in text
        assert "Надроченный скилл" in text

    def test_block_that_is_all_echo_still_dropped_whole(self):
        f = MeetingFilter()
        f.verdict("обходить все возможные и невозможные ограничения", "sys", {}, now=0)
        segs = [self._seg("Обходить все возможные и невозможные ограничения?")]
        assert self._block(f, segs, now=2)[2] == "cross-channel echo"

    def test_short_tail_segment_follows_its_neighbour(self):
        # "проекту." alone is too short to judge; it must follow the echo
        # segment it was cut from instead of surviving on its own.
        f = MeetingFilter()
        f.verdict(
            "не с первого раза но когда я нахожу подход к определенному проекту",
            "sys",
            now=0,
            meta={},
        )
        segs = [
            self._seg("Не с первого раза, но когда я нахожу подход к определенному"),
            self._seg("проекту."),
        ]
        assert self._block(f, segs, now=2)[2] == "cross-channel echo"

    def test_clean_mic_block_passes_untouched(self):
        f = MeetingFilter()
        f.verdict("покажи мне пожалуйста свой экран сейчас", "sys", {}, now=0)
        meta = {"segments": [self._seg("У меня два монитора и я не понимаю какой из них.")]}
        text, parts, reason = f.review(
            "У меня два монитора и я не понимаю какой из них.", "mic", meta, now=2
        )
        assert reason is None and len(parts) == 1
        assert text == "У меня два монитора и я не понимаю какой из них."
