"""Transcript file autosave for the "transcribe everything" mode.

A meeting/call transcript is written to a timestamped Markdown file under the
app's Application Support folder as it streams, so a long session is never held
only in memory (8 GB) and survives a crash. Pure file I/O — no audio, no AppKit —
so it's import-cheap and unit-testable.
"""

import contextlib
import json
from datetime import datetime
from pathlib import Path

from .paths import data_dir

_TRANSCRIPTS = data_dir() / "transcripts"
_override: Path | None = None  # user-chosen output folder, set from settings


def default_transcripts_dir() -> Path:
    """The built-in location — used when the user has not chosen one."""
    return _TRANSCRIPTS


def set_transcripts_dir(path: str | Path | None) -> None:
    """Point every transcript write at ``path`` (both the meeting recorder and
    file transcription). ``None``/empty restores the built-in folder. The path
    is remembered even if it is currently unwritable — ``transcripts_dir()``
    falls back at write time instead, so a temporarily missing external disk
    doesn't silently reset the user's choice."""
    global _override
    _override = Path(path).expanduser() if path else None


def transcripts_dir() -> Path:
    """The live output folder, created on demand. Falls back to the built-in
    location if the chosen one cannot be created (disk unplugged, permissions)."""
    if _override is not None:
        try:
            _override.mkdir(parents=True, exist_ok=True)
            return _override
        except OSError:
            pass
    _TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    return _TRANSCRIPTS


class SegmentSidecar:
    """Межі сегментів у секундах поруч із транскриптом — `<імʼя>.сегменти.jsonl`.

    Спільний для запису зустрічей і транскрибації файлів, щоб схема не розʼїхалась
    у двох місцях. Кожен рядок лягає на диск ОДРАЗУ (append+flush): 12.07.2026
    діаризацію відхилили саме за накопичення тексту до кінця прогону — годинний
    ефір ризикував згинути весь при збої. Помилки сайдкара ніколи не валять
    транскрипт: мітки часу — фундамент розділення спікерів, але не умова запису.
    """

    VERSION = 1

    def __init__(self, md_path: Path, meta: dict | None = None):
        self.path = md_path.with_suffix(".сегменти.jsonl")
        self._fh = None
        self._i = 0
        with contextlib.suppress(Exception):
            self._fh = open(self.path, "w", encoding="utf-8")  # noqa: SIM115 — довгий хендл
            head = {
                "pysar_segments": self.VERSION,
                "transcript": md_path.name,
                "note": "межі сегментів у секундах від початку запису — "
                "потрібні, щоб розділити спікерів без перерозшифровки",
            }
            head.update(meta or {})
            self._fh.write(json.dumps({"_meta": head}, ensure_ascii=False) + "\n")
            self._fh.flush()

    def write(self, text: str, src: str | None, clock: str, span, words=None) -> None:
        if self._fh is None:
            return
        row = {
            "i": self._i,
            "t0": span[0] if span else None,
            "t1": span[1] if span else None,
            "src": src,
            "clock": clock,
            "text": text,
        }
        if words:
            # [[початок, кінець, слово], …] в АБСОЛЮТНИХ секундах запису.
            row["w"] = words
        self._i += 1
        with contextlib.suppress(Exception):
            self._fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._fh.flush()

    def write_parts(self, text: str, src: str | None, clock: str, span, parts) -> None:
        """Рядок на КОЖЕН сегмент whisper усередині шматка (якщо вони є).

        `parts` — [{"t0","t1","text","w"}], час у секундах ВІД ПОЧАТКУ шматка.
        Один рядок на 12-18 секунд аудіо (як було до 11.09.2026) не дає
        розділенню голосів жодного шансу на діалог: «— Як вас звати? — Марина»
        це один рядок і один мовець."""
        if self._fh is None:
            return
        if not parts or not span:
            self.write(text, src, clock, span)
            return
        base = span[0]
        wrote = False
        for part in parts:
            try:
                t0 = base + float(part["t0"])
                t1 = min(base + float(part["t1"]), span[1])
                body = str(part["text"]).strip()
            except (KeyError, TypeError, ValueError):
                continue
            if not body:
                continue
            words = [
                [round(base + float(w[0]), 2), round(base + float(w[1]), 2), str(w[2])]
                for w in (part.get("w") or [])
                if len(w) >= 3
            ]
            self.write(body, src, clock, (round(t0, 2), round(t1, 2)), words)
            wrote = True
        if not wrote:
            self.write(text, src, clock, span)

    def close(self) -> None:
        if self._fh is not None:
            with contextlib.suppress(Exception):
                self._fh.close()
            self._fh = None

    def __enter__(self) -> "SegmentSidecar":
        return self

    def __exit__(self, *exc) -> None:
        # Хендл мусить закритись і на скасуванні, і на аварії прогону: файлова
        # транскрипція має гілки cancelled/aborted, які не доходять до кінця.
        self.close()


class TranscriptFile:
    """An append-as-you-go Markdown transcript. ``open()`` creates the file with a
    dated header; ``append(text)`` adds one segment line and flushes immediately so
    nothing is lost if the app dies mid-meeting; ``close()`` stamps the end."""

    def __init__(self, started: datetime | None = None):
        self._started = started or datetime.now()
        self._fh = None
        self.path: Path | None = None
        # Сайдкар меж часу (фіча «мітки секунд», 06.09.2026). Живе ПОРУЧ із .md і
        # пишеться тим самим append+flush: 12.07.2026 діаризацію відхилили саме
        # за накопичення тексту до кінця прогону — годинний ефір ризикував
        # згинути весь при збої. Тут кожен рядок на диску одразу.
        self._side: SegmentSidecar | None = None
        self.segments_path: Path | None = None
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
        self._side = SegmentSidecar(
            self.path, {"started": self._started.isoformat(timespec="seconds")}
        )
        self.segments_path = self._side.path
        return self.path

    def append(
        self,
        text: str,
        source: str | None = None,
        ts: datetime | None = None,
        span: tuple[float, float] | None = None,
        parts: list[dict] | None = None,
    ) -> None:
        text = (text or "").strip()
        if not text or self._fh is None:
            return
        # A small header before every block: "Source · HH:MM" (or just the time
        # when the source is unknown — e.g. the mixed "off" mode). The user wants
        # each block stamped, not consecutive lines grouped under one label.
        # Секунди в годиннику з 06.09.2026: без них два сусідні блоки в одну
        # хвилину неможливо розрізнити, а правки в редакторі чіпляються за час.
        clock = (ts or datetime.now()).strftime("%H:%M:%S")
        head = f"{self._labels.get(source, source)} · {clock}" if source is not None else clock
        self._fh.write(f"**{head}**\n\n")
        self._last_source = source
        self._fh.write(text + "\n\n")
        self._fh.flush()
        self._write_segment(text, source, ts, span, parts)

    def _write_segment(
        self,
        text: str,
        source: str | None,
        ts: datetime | None,
        span: tuple[float, float] | None,
        parts: list[dict] | None = None,
    ) -> None:
        """Рядки в сайдкар, одразу на диск. Ніколи не валить транскрипт.

        Дрібні межі (`parts`) кладуться окремими рядками — див.
        `SegmentSidecar.write_parts`. У самому транскрипті (.md) блок лишається
        один: людині кришиво по дві секунди читати незручно."""
        if self._side is None:
            return
        clock = (ts or datetime.now()).strftime("%H:%M:%S")
        self._side.write_parts(text, source, clock, span, parts)

    def close(self) -> None:
        if self._fh is None:
            return
        with contextlib.suppress(Exception):
            ended = datetime.now().strftime("%H:%M")
            self._fh.write(f"_— ended {ended} —_\n")
            self._fh.flush()
            self._fh.close()
        self._fh = None
        if self._side is not None:
            self._side.close()
            self._side = None
