"""Transcript file autosave for the "transcribe everything" mode.

A meeting/call transcript is written to a timestamped Markdown file under the
app's Application Support folder as it streams, so a long session is never held
only in memory (8 GB) and survives a crash. Pure file I/O — no audio, no AppKit —
so it's import-cheap and unit-testable.
"""

import contextlib
from datetime import datetime
from pathlib import Path

from .paths import data_dir

_TRANSCRIPTS = data_dir() / "transcripts"


def transcripts_dir() -> Path:
    _TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    return _TRANSCRIPTS


class TranscriptFile:
    """An append-as-you-go Markdown transcript. ``open()`` creates the file with a
    dated header; ``append(text)`` adds one segment line and flushes immediately so
    nothing is lost if the app dies mid-meeting; ``close()`` stamps the end."""

    def __init__(self, started: datetime | None = None):
        self._started = started or datetime.now()
        self._fh = None
        self.path: Path | None = None
        # Speaker-source labels (source-separation modes). Keyed "sys"/"mic"; a
        # heading is written only when the source changes, so consecutive segments
        # from one speaker group under one label.
        self._labels: dict[str, str] = {"sys": "System", "mic": "You"}
        self._last_source: str | None = None

    def set_source_labels(self, labels: dict[str, str]) -> None:
        if labels:
            self._labels.update(labels)

    def open(self) -> Path:
        stamp = self._started.strftime("%Y-%m-%d_%H-%M-%S")
        self.path = transcripts_dir() / f"transcript_{stamp}.md"
        self._fh = open(self.path, "w", encoding="utf-8")  # noqa: SIM115 — long-lived handle
        human = self._started.strftime("%Y-%m-%d %H:%M")
        self._fh.write(f"# Pysar transcript — {human}\n\n")
        self._fh.flush()
        self._last_source = None
        return self.path

    def append(
        self, text: str, source: str | None = None, ts: datetime | None = None
    ) -> None:
        text = (text or "").strip()
        if not text or self._fh is None:
            return
        # A small header before every block: "Source · HH:MM" (or just the time
        # when the source is unknown — e.g. the mixed "off" mode). The user wants
        # each block stamped, not consecutive lines grouped under one label.
        clock = (ts or datetime.now()).strftime("%H:%M")
        head = f"{self._labels.get(source, source)} · {clock}" if source is not None else clock
        self._fh.write(f"**{head}**\n\n")
        self._last_source = source
        self._fh.write(text + "\n\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh is None:
            return
        with contextlib.suppress(Exception):
            ended = datetime.now().strftime("%H:%M")
            self._fh.write(f"_— ended {ended} —_\n")
            self._fh.flush()
            self._fh.close()
        self._fh = None
