"""Rotation of the meeting/call recordings (meeting_keep_last).

Meetings rotate on their own count, not on keep_last: they are far heavier than
a dictation and far less repeatable, and the user picks how many to keep —
including "all", because the one-off phone call is the reason the buffer exists
at all.

The unit of rotation is the SESSION, not the file: one meeting on disk is a
`<stem>-mic.wav` + `<stem>-sys.wav` pair, plus `-2`/`-3` pairs added by a
recover-restart mid-call. Pruning file-by-file would make "last 5" mean two and a
half meetings and leave halves of conversations behind.
"""

import json
import os

import pytest

from pysar import recordings
from pysar.recordings import (
    MEETING_KEEP_ALL,
    MEETING_KEEP_OPTIONS,
    load_settings,
    meeting_sessions,
    prune_meetings,
)


@pytest.fixture
def meetings(tmp_path, monkeypatch):
    d = tmp_path / "meetings"
    d.mkdir()
    monkeypatch.setattr(recordings, "_MEETINGS", d)
    return d


def _session(d, stem: str, age: float, parts=("mic", "sys"), restarts=0):
    """Write one meeting's files. *age* orders sessions (bigger = older)."""
    names = [f"{stem}-{p}.wav" for p in parts]
    for n in range(2, restarts + 2):
        names += [f"{stem}-{n}-{p}.wav" for p in parts]
    when = 1_700_000_000 - age
    for name in names:
        f = d / name
        f.write_bytes(b"RIFF-fake")
        os.utime(f, (when, when))
    return names


def _left(d) -> set[str]:
    return {p.name for p in d.iterdir()}


# ── grouping ──────────────────────────────────────────────────────────────────
def test_a_pair_is_one_session(meetings):
    _session(meetings, "2026-08-24_10-00-00", age=0)
    assert list(meeting_sessions()) == ["2026-08-24_10-00-00"]


def test_recover_restarts_belong_to_the_same_session(meetings):
    # Three streams, one conversation — the count the user chose is meetings, not
    # capture attempts, and the pieces are only useful together.
    _session(meetings, "2026-08-24_10-00-00", age=0, restarts=2)
    sessions = meeting_sessions()
    assert list(sessions) == ["2026-08-24_10-00-00"]
    assert len(sessions["2026-08-24_10-00-00"]) == 6


def test_foreign_files_are_not_ours_to_group(meetings):
    (meetings / "notes.txt").write_text("hi")
    (meetings / "exported.wav").write_bytes(b"RIFF")
    _session(meetings, "2026-08-24_10-00-00", age=0)
    assert list(meeting_sessions()) == ["2026-08-24_10-00-00"]


# ── rotation ──────────────────────────────────────────────────────────────────
def test_rotation_drops_whole_sessions(meetings):
    _session(meetings, "2026-08-24_12-00-00", age=0)
    _session(meetings, "2026-08-23_12-00-00", age=86400)
    _session(meetings, "2026-08-22_12-00-00", age=172800)

    prune_meetings(2)

    assert _left(meetings) == {
        "2026-08-24_12-00-00-mic.wav",
        "2026-08-24_12-00-00-sys.wav",
        "2026-08-23_12-00-00-mic.wav",
        "2026-08-23_12-00-00-sys.wav",
    }


def test_a_dropped_session_takes_its_restart_pairs_with_it(meetings):
    _session(meetings, "2026-08-24_12-00-00", age=0)
    _session(meetings, "2026-08-23_12-00-00", age=86400, restarts=2)  # 6 files, one call

    prune_meetings(1)

    assert _left(meetings) == {
        "2026-08-24_12-00-00-mic.wav",
        "2026-08-24_12-00-00-sys.wav",
    }


def test_a_kept_session_keeps_all_of_its_pieces(meetings):
    _session(meetings, "2026-08-24_12-00-00", age=0, restarts=1)  # 4 files
    _session(meetings, "2026-08-23_12-00-00", age=86400)

    prune_meetings(1)

    # "Last 1 meeting" means the whole meeting — not the newest two files of it.
    assert _left(meetings) == {
        "2026-08-24_12-00-00-mic.wav",
        "2026-08-24_12-00-00-sys.wav",
        "2026-08-24_12-00-00-2-mic.wav",
        "2026-08-24_12-00-00-2-sys.wav",
    }


def test_the_count_is_sessions_not_files(meetings):
    for i in range(5):
        _session(meetings, f"2026-08-2{i}_12-00-00", age=i * 86400, restarts=i)
    prune_meetings(3)
    assert len(meeting_sessions()) == 3


def test_keep_all_deletes_nothing(meetings):
    for i in range(4):
        _session(meetings, f"2026-08-2{i}_12-00-00", age=i * 86400)
    before = _left(meetings)

    prune_meetings(MEETING_KEEP_ALL)

    # The one-off phone call: with rotation off, nothing on disk may be touched.
    assert _left(meetings) == before


def test_a_negative_count_is_treated_as_keep_all(meetings):
    _session(meetings, "2026-08-24_12-00-00", age=0)
    prune_meetings(-5)  # corrupted setting must never mean "delete everything"
    assert _left(meetings)


def test_foreign_files_are_never_deleted(meetings):
    (meetings / "important-notes.txt").write_text("hi")
    (meetings / "my-own-export.wav").write_bytes(b"RIFF")
    for i in range(3):
        _session(meetings, f"2026-08-2{i}_12-00-00", age=i * 86400)

    prune_meetings(1)

    assert "important-notes.txt" in _left(meetings)
    assert "my-own-export.wav" in _left(meetings)


def test_pruning_an_empty_folder_is_a_noop(meetings):
    prune_meetings(5)
    assert _left(meetings) == set()


def test_fewer_sessions_than_the_limit_are_all_kept(meetings):
    _session(meetings, "2026-08-24_12-00-00", age=0)
    _session(meetings, "2026-08-23_12-00-00", age=86400)
    prune_meetings(10)
    assert len(_left(meetings)) == 4


# ── the setting itself ────────────────────────────────────────────────────────
def test_keep_all_is_offered_as_an_option():
    assert MEETING_KEEP_ALL in MEETING_KEEP_OPTIONS


def test_default_is_generous_and_bounded():
    n = recordings.DEFAULTS["meeting_keep_last"]
    assert n in MEETING_KEEP_OPTIONS
    assert n > recordings.DEFAULTS["keep_last"]  # meetings outlive dictations


def _load(monkeypatch, tmp_path, payload):
    f = tmp_path / "settings.json"
    f.write_text(json.dumps(payload))
    monkeypatch.setattr(recordings, "_SETTINGS", f)
    return load_settings()


def test_a_stored_choice_survives_a_restart(monkeypatch, tmp_path):
    assert _load(monkeypatch, tmp_path, {"meeting_keep_last": 100})["meeting_keep_last"] == 100
    assert _load(monkeypatch, tmp_path, {"meeting_keep_last": 0})["meeting_keep_last"] == 0


def test_a_bogus_value_falls_back_to_the_default(monkeypatch, tmp_path):
    merged = _load(monkeypatch, tmp_path, {"meeting_keep_last": 7})
    assert merged["meeting_keep_last"] == recordings.DEFAULTS["meeting_keep_last"]


def test_meeting_rotation_is_independent_of_dictation_rotation(monkeypatch, tmp_path):
    merged = _load(monkeypatch, tmp_path, {"keep_last": 5, "meeting_keep_last": 500})
    assert (merged["keep_last"], merged["meeting_keep_last"]) == (5, 500)
