"""Post-ASR filters for the meeting ("transcribe everything") pipeline.

Live call 16.07.2026 exposed three failure modes the raw pipeline can't see:

  * Echo duplicates ("smart" source mode): with the user on speakers, the
    remote voice reaches both the system-audio channel (clean) and the
    microphone (room bleed), so the same phrase is transcribed twice under two
    labels — and the two segmenters cut at different pauses, so the duplicates
    are time-shifted, not identical.
  * Backchannel hallucinations (auto language): hums and short "дякую/угу"
    decode as random-language filler ("Obrigada", Italian word salad).
  * Language-detection flicker (auto language): one misdetected short segment
    lands in a foreign language mid-conversation.

All three are text/metadata-level problems, so they are filtered here — pure
Python, no audio, no AppKit — after transcription and before the transcript
window/file. Dictation is untouched: these trade a small risk of dropping a
real line for a much cleaner meeting transcript, which is the right trade for
a transcript but not for typing.
"""

import re
import time
from difflib import SequenceMatcher

# ── tunables ──────────────────────────────────────────────────────────────────
# Echo dedup: how long a transcribed phrase can lag its cross-channel twin.
# Covers segmenter boundary skew plus one serialized whisper round-trip.
ECHO_WINDOW_SEC = 25.0
# Fraction of the new text's WORDS that must be covered by runs of ≥2
# consecutive shared words with the other channel's recent text. Word runs
# (not characters) — char-level subsequence over Cyrillic inflates on any
# same-language text; single shared words ("а", "ну", "типа") don't count.
# Real echo pairs from the 16.07.2026 call score 0.65–0.78 on this metric,
# unrelated same-topic replies score 0.0.
ECHO_MATCH_RATIO = 0.55
# Phrases shorter than this (words) are never deduped — "да"/"угу"
# legitimately occur on both sides of a call.
ECHO_MIN_WORDS = 4
# Per-source history kept for matching, words (concatenated tail).
ECHO_TAIL_WORDS = 120

# Hum filter: duration-weighted mean no-speech probability above which the
# whole segment is treated as non-speech the VAD let through.
HUM_NO_SPEECH = 0.55
# ...or a very unconfident decode of a very short blurb.
HUM_LOGPROB = -1.1
HUM_MAX_CHARS = 25

# Language-outlier filter (auto mode only): votes are lang_prob sums over
# confident-ish segments; a dominant language exists once this much weight
# accumulated. Short foreign segments below LANG_SURE_PROB are then dropped.
LANG_VOTE_MIN = 2.0
LANG_VOTE_CHARS = 20  # segments shorter than this don't vote
LANG_OUTLIER_MAX_CHARS = 60  # longer foreign text is a real language switch
LANG_SURE_PROB = 0.95

_NORM = re.compile(r"[^\w]+", re.UNICODE)


def _norm(text: str) -> str:
    return _NORM.sub(" ", text).lower().strip()


class MeetingFilter:
    """Per-meeting stateful filter. ``verdict(text, source, meta)`` returns
    ``None`` to keep the segment or a short reason string to drop it.

    ``meta`` comes from ``transcriber.transcribe_meeting`` and may be empty —
    every check degrades to "keep" when its inputs are missing."""

    def __init__(self, auto_lang: bool = False):
        self._auto_lang = auto_lang
        # (monotonic time, source, normalized text) of kept segments.
        self._recent: list[tuple[float, str, str]] = []
        self._lang_votes: dict[str, float] = {}

    # ── public ────────────────────────────────────────────────────────────────
    def verdict(
        self,
        text: str,
        source: str | None,
        meta: dict | None,
        now: float | None = None,
    ) -> str | None:
        meta = meta or {}
        now = time.monotonic() if now is None else now
        norm = _norm(text)
        if not norm:
            return "empty"

        reason = self._check_hum(norm, meta) or self._check_lang(norm, meta)
        if reason is None and source is not None:
            reason = self._check_echo(norm, source, now)
        if reason is not None:
            return reason

        self._remember(norm, source, now)
        self._vote(norm, meta)
        return None

    # ── checks ────────────────────────────────────────────────────────────────
    def _check_hum(self, norm: str, meta: dict) -> str | None:
        no_speech = meta.get("no_speech")
        if no_speech is not None and no_speech > HUM_NO_SPEECH:
            return "no-speech"
        logprob = meta.get("logprob")
        if logprob is not None and logprob < HUM_LOGPROB and len(norm) < HUM_MAX_CHARS:
            return "low-confidence blurb"
        return None

    def _check_lang(self, norm: str, meta: dict) -> str | None:
        if not self._auto_lang:
            return None
        lang, prob = meta.get("lang"), meta.get("lang_prob") or 0.0
        if lang is None:
            return None
        dominant = self._dominant_lang()
        if (
            dominant is not None
            and lang != dominant
            and len(norm) < LANG_OUTLIER_MAX_CHARS
            and prob < LANG_SURE_PROB
        ):
            return f"lang outlier {lang}"
        return None

    def _check_echo(self, norm: str, source: str, now: float) -> str | None:
        new_words = norm.split()
        if len(new_words) < ECHO_MIN_WORDS:
            return None
        self._recent = [(t, s, n) for (t, s, n) in self._recent if now - t <= ECHO_WINDOW_SEC]
        tail: list[str] = []
        for _, s, n in self._recent:
            if s != source:
                tail.extend(n.split())
        tail = tail[-ECHO_TAIL_WORDS:]
        if not tail:
            return None
        # Share of the new words covered by runs of ≥2 consecutive words shared
        # with the other channel's tail: containment-tolerant (the tail is
        # longer and the two segmenters cut at different points), yet robust to
        # per-channel ASR word differences.
        blocks = SequenceMatcher(None, new_words, tail, autojunk=False).get_matching_blocks()
        covered = sum(b.size for b in blocks if b.size >= 2) / len(new_words)
        if covered >= ECHO_MATCH_RATIO:
            return "cross-channel echo"
        return None

    # ── state ─────────────────────────────────────────────────────────────────
    def _remember(self, norm: str, source: str | None, now: float) -> None:
        if source is not None:
            self._recent.append((now, source, norm))

    def _vote(self, norm: str, meta: dict) -> None:
        lang, prob = meta.get("lang"), meta.get("lang_prob") or 0.0
        if self._auto_lang and lang and len(norm) >= LANG_VOTE_CHARS:
            self._lang_votes[lang] = self._lang_votes.get(lang, 0.0) + prob

    def _dominant_lang(self) -> str | None:
        if not self._lang_votes:
            return None
        lang, weight = max(self._lang_votes.items(), key=lambda kv: kv[1])
        return lang if weight >= LANG_VOTE_MIN else None
