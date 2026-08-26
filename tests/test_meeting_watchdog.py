"""Capture-liveness watchdog + the stop/recover race.

All three cases here come from live reports, not theory:
  * 23.07.2026 — a mute/unmute cycle stalled SCK silently (no didStop).
  * 07.08.2026 — a recovered stream that never delivered its first buffer left
    the watchdog blind and the menu stuck on "Stop transcription" forever.
  * 07.08.2026 — clicking Stop during the drain fell through to the start branch
    and did nothing visible.

VoiceTyper is built without __init__ (no tray, no AppKit), as in test_streaming.
"""

import threading

import pytest

from pysar import app as app_mod
from pysar.app import VoiceTyper

# The watchdog asks CoreAudio about the real machine (output device, whether its
# IO is open). Tests must never do that: the answer differs per Mac and changes
# if anything happens to be playing while the suite runs. Everything below runs
# against this stub instead.
_probe = {"device": 1, "running": False}


@pytest.fixture(autouse=True)
def _stub_audio_probes(monkeypatch):
    _probe["device"] = 1
    _probe["running"] = False
    monkeypatch.setattr(app_mod, "default_output_device", lambda: _probe["device"])
    monkeypatch.setattr(app_mod, "output_is_running", lambda dev=None: _probe["running"])


class _FakeTray:
    def __init__(self):
        self.statuses: list[str] = []
        self.huds: list[str] = []
        self.meeting_states: list[str] = []
        # Notifications are the only channel that outlives the moment (they stay
        # in Notification Centre), so the failure tests below read them.
        self.notes: list[tuple] = []

    def set_status(self, text: str) -> None:
        self.statuses.append(text)

    def show_hud(self, text: str, state: str = "listening") -> None:
        self.huds.append(text)

    def hide_hud(self) -> None:
        pass

    def notify(self, *a) -> None:
        self.notes.append(a)

    def set_title(self, *a) -> None:
        pass

    def set_meeting_active(self, active: bool) -> None:
        self.meeting_states.append("on" if active else "off")

    def set_meeting_stopping(self) -> None:
        self.meeting_states.append("stopping")


class _FakeRecorder:
    """Stands in for SystemAudioRecorder: `since` is what the heartbeat reports,
    `secs`/`paths` what its raw recovery buffer ended up holding."""

    def __init__(
        self,
        since: float | None,
        secs: float = 0.0,
        paths: list | None = None,
        mute: float | None = 0.0,
        sound_seen: bool = True,
    ):
        self.since = since
        # 26.08.2026: `mute` is how long the SYSTEM tap has carried nothing but
        # exact zeros. Healthy by default so the older cases keep their meaning.
        self.mute = mute
        self.sound_seen = sound_seen
        self.stopped = False
        self.started = False
        self.secs = secs
        self.paths = paths or []

    def seconds_since_audio(self) -> float | None:
        return self.since

    def seconds_since_sound(self) -> float | None:
        return self.mute

    def heard_sound(self) -> bool:
        return self.sound_seen

    def start(self, on_segment=None, on_error=None) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def dump_seconds(self) -> float:
        return self.secs

    def dump_paths(self) -> list:
        return self.paths


def _vt(
    since: float | None,
    started_ago: float,
    mute: float | None = 0.0,
    sound_seen: bool = True,
) -> VoiceTyper:
    import time

    vt = object.__new__(VoiceTyper)
    vt._tray = _FakeTray()
    vt._t = lambda key, **kw: key
    vt._mode = "uk"
    vt._meeting = True
    vt._meeting_stopping = False
    vt._meeting_mic = False
    vt._sysrec = _FakeRecorder(since, mute=mute, sound_seen=sound_seen)
    vt._capture_started_at = time.monotonic() - started_ago
    vt._recover_lock = threading.RLock()
    vt._watchdog_stop = threading.Event()
    vt._watchdog_thread = None
    vt._meeting_recover_count = 0
    vt._meeting_recover_window_start = 0.0
    vt._mute_recover_streak = 0
    vt._capture_output_device = _probe["device"]
    vt._meeting_queue = None
    vt._meeting_worker = None
    vt._transcript_file = None
    vt._transcript_window = None
    vt._settings = {"meeting_capture_mic": False, "meeting_source_mode": "off"}
    vt.recovered: list[str] = []
    return vt


def _no_real_recover(vt) -> None:
    """Record the reason instead of touching ScreenCaptureKit."""
    vt._recover_meeting_capture = lambda reason: vt.recovered.append(reason)


# ── first-buffer deadline (the 07.08.2026 zombie) ─────────────────────────────
def test_no_first_buffer_past_deadline_triggers_recover():
    vt = _vt(since=None, started_ago=VoiceTyper._MEETING_FIRST_BUFFER_SEC + 5)
    _no_real_recover(vt)
    reason = vt._watchdog_tick()
    assert reason is not None and "first buffer" in reason
    assert len(vt.recovered) == 1


def test_no_first_buffer_within_warmup_is_left_alone():
    vt = _vt(since=None, started_ago=2.0)
    _no_real_recover(vt)
    assert vt._watchdog_tick() is None
    assert vt.recovered == []


def test_stalled_stream_triggers_recover():
    vt = _vt(since=VoiceTyper._MEETING_STALL_SEC + 1, started_ago=60.0)
    _no_real_recover(vt)
    reason = vt._watchdog_tick()
    assert reason is not None and "no audio" in reason


def test_live_stream_is_left_alone():
    vt = _vt(since=1.0, started_ago=60.0)
    _no_real_recover(vt)
    assert vt._watchdog_tick() is None


def test_tick_is_a_noop_while_stopping():
    vt = _vt(since=None, started_ago=999.0)
    vt._meeting_stopping = True
    _no_real_recover(vt)
    assert vt._watchdog_tick() is None
    assert vt.recovered == []


# ── stop vs recover ───────────────────────────────────────────────────────────
def test_recover_cannot_resurrect_capture_behind_a_stop():
    """A recover already in flight must not start a fresh stream after the user
    stopped: _stop_meeting flips the flag and tears the capture down under the
    same (reentrant) lock, so the waiting recover wakes up to a dead meeting."""
    vt = _vt(since=None, started_ago=999.0)
    started_fresh: list[str] = []

    entered = threading.Event()
    release = threading.Event()

    def slow_recover(reason: str) -> None:
        entered.set()
        release.wait(2)
        with vt._recover_lock:
            if not vt._meeting or vt._meeting_stopping:
                return
            started_fresh.append(reason)

    vt._recover_meeting_capture = slow_recover
    t = threading.Thread(target=slow_recover, args=("watchdog: stall",))
    t.start()
    entered.wait(2)

    vt._stop_meeting()
    release.set()
    t.join(3)

    assert started_fresh == []  # no orphan capture started behind the stop
    assert vt._sysrec.stopped is True
    assert vt._meeting is False


def test_stop_says_stopping_in_the_menu_before_draining():
    vt = _vt(since=1.0, started_ago=10.0)
    vt._stop_meeting()
    # "stopping" must be shown first — the drain can take up to a minute, and the
    # old code only relabelled the item after it, so the button looked frozen.
    assert vt._tray.meeting_states[0] == "stopping"
    assert vt._tray.meeting_states[-1] == "off"


def test_toggle_during_stop_does_not_start_a_new_capture():
    vt = _vt(since=1.0, started_ago=10.0)
    vt._meeting = False
    vt._meeting_stopping = True
    vt._start_meeting = lambda: (_ for _ in ()).throw(AssertionError("must not start"))
    vt._on_toggle_meeting()
    # The user gets a visible HUD, not just a menu-only status line.
    assert vt._tray.huds and "meetingStopping" in vt._tray.huds[-1]


# ── the stop that looked like a hang (18.08.2026) ─────────────────────────────
class _ThrowingIsland:
    """An island whose teardown blows up — a window torn down mid-stop, a dead
    dispatch queue. The UI reset behind it must still land."""

    def set_stopping(self, on: bool) -> None:
        raise RuntimeError("island is gone")

    def hide(self) -> None:
        raise RuntimeError("island is gone")


def test_menu_resets_even_if_the_island_throws():
    """Reported 18.08.2026: the menu item sometimes stayed on "⏳ Stopping…" with
    nothing running. Every reset step is isolated now, so one throw cannot eat
    the ones after it."""
    vt = _vt(since=1.0, started_ago=10.0)
    vt._transcript_window = _ThrowingIsland()
    vt._stop_meeting()
    assert vt._tray.meeting_states[-1] == "off"
    assert vt._meeting_stopping is False


def test_drain_counts_down_out_loud():
    """A stop is not instant — the audio already captured still has to go through
    whisper. Silence during that read as a freeze, so the remaining count is
    named in the status line."""
    import queue
    import threading as th

    vt = _vt(since=1.0, started_ago=10.0)
    vt._meeting_queue = queue.Queue()
    for _ in range(3):
        vt._meeting_queue.put((b"", None))
    done = th.Event()

    def worker():
        while not done.is_set():
            done.wait(0.05)

    vt._meeting_worker = th.Thread(target=worker, daemon=True)
    vt._meeting_worker.start()
    th.Timer(0.6, done.set).start()
    vt._await_drain()
    assert any("meetingDraining" in s for s in vt._tray.statuses)


def test_drain_gives_up_at_the_ceiling_instead_of_blocking_forever():
    import threading as th
    import time

    vt = _vt(since=1.0, started_ago=10.0)
    vt._MEETING_DRAIN_TIMEOUT = 0.5
    vt._meeting_worker = th.Thread(target=lambda: time.sleep(30), daemon=True)
    vt._meeting_worker.start()
    t0 = time.monotonic()
    vt._await_drain()
    assert time.monotonic() - t0 < 3.0  # returned; the worker finishes on its own


# ── a capture that dies MID-call has to leave a trace (24.08.2026) ────────────
class _NoTimer:
    """threading.Timer stand-in — the HUD auto-hide is irrelevant here, and a real
    two-second timer would only make the suite wait for it at exit."""

    def __init__(self, *a, **kw):
        pass

    def start(self) -> None:
        pass


class _FakeTranscript:
    """TranscriptFile as far as the recover path is concerned."""

    def __init__(self):
        self.lines: list[str] = []
        self.path = "/tmp/transcript.md"

    def append(self, text: str, source: str | None = None, ts=None) -> None:
        self.lines.append(text)

    def close(self) -> None:
        pass


def _recoverable(monkeypatch, tmp_path):
    """A VoiceTyper whose REAL _recover_meeting_capture can be run: no SCK, no
    real meetings folder, no live HUD timer."""
    vt = _vt(since=None, started_ago=999.0)
    vt._transcript_file = _FakeTranscript()
    monkeypatch.setattr(app_mod, "SystemAudioRecorder", lambda **kw: _FakeRecorder(since=None))
    monkeypatch.setattr(app_mod, "meetings_dir", lambda: tmp_path)
    monkeypatch.setattr(app_mod.threading, "Timer", _NoTimer)
    return vt


def _restart_notes(vt) -> list[tuple]:
    return [n for n in vt._tray.notes if "notif.captureRestart" in str(n)]


def test_a_mid_session_drop_is_announced_out_loud(monkeypatch, tmp_path):
    """The HUD lives for two seconds. Someone who is on the phone right then sees
    nothing, so the event has to survive the moment: a notification plus a mark in
    the transcript that says where the recording has a hole."""
    vt = _recoverable(monkeypatch, tmp_path)
    vt._recover_meeting_capture("watchdog: no audio 12s")

    assert len(_restart_notes(vt)) == 1
    assert vt._transcript_file.lines == ["transcript.captureRestart"]


def test_a_flapping_stream_notifies_once_not_per_restart(monkeypatch, tmp_path):
    # Restart storms are exactly when the capture is worst — and exactly when a
    # notification per attempt would bury the screen instead of informing.
    vt = _recoverable(monkeypatch, tmp_path)
    for _ in range(VoiceTyper._MEETING_RECOVER_MAX):
        vt._recover_meeting_capture("watchdog: no audio 12s")

    assert len(_restart_notes(vt)) == 1
    assert vt._transcript_file.lines == ["transcript.captureRestart"]


def test_a_new_incident_later_speaks_up_again(monkeypatch, tmp_path):
    """One message per recover WINDOW, not per meeting: a drop half an hour after
    the first one is news again, not the same event still repeating."""
    import time

    vt = _recoverable(monkeypatch, tmp_path)
    vt._recover_meeting_capture("watchdog: no audio 12s")
    # Age the window out — the counter resets and the next drop is a fresh event.
    vt._meeting_recover_window_start = time.monotonic() - VoiceTyper._MEETING_RECOVER_WINDOW - 1
    vt._recover_meeting_capture("watchdog: no audio 12s")

    assert len(_restart_notes(vt)) == 2
    assert len(vt._transcript_file.lines) == 2


def test_the_restart_notice_never_blocks_the_restart(monkeypatch, tmp_path):
    # Reporting is a courtesy; restarting the capture is the job. A transcript
    # file that throws must not cost the rest of the meeting.
    vt = _recoverable(monkeypatch, tmp_path)

    class _Throwing:
        def append(self, *a, **kw):
            raise RuntimeError("file is gone")

    vt._transcript_file = _Throwing()
    vt._recover_meeting_capture("watchdog: no audio 12s")
    assert vt._sysrec.started is True


def test_no_transcript_file_still_notifies(monkeypatch, tmp_path):
    # "Record without the window" + no file: the notification is then the ONLY
    # trace the user will ever get.
    vt = _recoverable(monkeypatch, tmp_path)
    vt._transcript_file = None
    vt._recover_meeting_capture("watchdog: no audio 12s")
    assert len(_restart_notes(vt)) == 1


# ── what the user is told when the meeting ends ───────────────────────────────
def test_stop_shouts_when_no_audio_ever_reached_the_disk():
    """The 24.08.2026 failure: a ten-minute call ended with "transcript saved"
    and an empty file. A mute capture must be named as such, before anything
    reassuring is shown."""
    vt = _vt(since=1.0, started_ago=600.0)
    vt._sysrec = _FakeRecorder(since=1.0, secs=0.0)
    vt._stop_meeting()
    assert any("notif.noAudioTitle" in str(n) for n in vt._tray.notes)


def test_stop_points_at_the_saved_raw_audio():
    vt = _vt(since=1.0, started_ago=600.0)
    vt._sysrec = _FakeRecorder(since=1.0, secs=600.0, paths=["/tmp/meet-mic.wav"])
    vt._stop_meeting()
    assert any("notif.rawAudioTitle" in str(n) for n in vt._tray.notes)
    assert not any("notif.noAudioTitle" in str(n) for n in vt._tray.notes)


def test_stop_reports_the_outcome_after_the_capture_is_torn_down():
    # The tally is read from a recorder that is already stopped — the reason
    # SystemAudioRecorder keeps its summary past stop().
    vt = _vt(since=1.0, started_ago=600.0)
    vt._sysrec = _FakeRecorder(since=1.0, secs=120.0, paths=["/tmp/meet-sys.wav"])
    vt._stop_meeting()
    assert vt._sysrec.stopped is True
    assert any("notif.rawAudioTitle" in str(n) for n in vt._tray.notes)


# ── the mic the user picked must be the mic that records (24.08.2026) ─────────
def _mic_vt(mic_name, *, pinning: bool, monkeypatch):
    vt = _vt(since=1.0, started_ago=10.0)
    vt._settings["mic"] = mic_name
    monkeypatch.setattr(app_mod, "mic_pinning_supported", lambda: pinning)
    return vt


def test_an_unresolvable_mic_is_announced(monkeypatch):
    """Device renamed or unplugged: the capture falls back to the system default,
    which on this Mac is the AirPods link rather than the built-in mic. The user
    has to hear that BEFORE the call, not from the finished recording."""
    vt = _mic_vt("MacBook Air Microphone", pinning=True, monkeypatch=monkeypatch)
    assert vt._warn_if_mic_not_pinned(capture_mic=True, mic_uid=None) is True
    assert any("notif.micPinFailedTitle" in str(n) for n in vt._tray.notes)


def test_an_old_macos_that_cannot_pin_is_announced(monkeypatch):
    # macOS 14 and older have no setMicrophoneCaptureDeviceID_ at all: the menu
    # choice is silently ignored by SCK.
    vt = _mic_vt("MacBook Air Microphone", pinning=False, monkeypatch=monkeypatch)
    assert vt._warn_if_mic_not_pinned(capture_mic=True, mic_uid="uid-built-in") is True


def test_a_pinned_mic_says_nothing(monkeypatch):
    vt = _mic_vt("MacBook Air Microphone", pinning=True, monkeypatch=monkeypatch)
    assert vt._warn_if_mic_not_pinned(capture_mic=True, mic_uid="uid-built-in") is False
    assert vt._tray.notes == []


def test_the_system_default_choice_is_not_a_failure(monkeypatch):
    # No device chosen = "whatever the system uses" — there is nothing to warn about.
    vt = _mic_vt(None, pinning=False, monkeypatch=monkeypatch)
    assert vt._warn_if_mic_not_pinned(capture_mic=True, mic_uid=None) is False
    assert vt._tray.notes == []


def test_a_system_only_capture_is_not_warned_about(monkeypatch):
    vt = _mic_vt("MacBook Air Microphone", pinning=False, monkeypatch=monkeypatch)
    assert vt._warn_if_mic_not_pinned(capture_mic=False, mic_uid=None) is False


# ── the deaf tap (GoIT webinar, 26.08.2026) ───────────────────────────────────
# Buffers arrived on schedule for 50 minutes while every one of them was exact
# digital zeros. Arrival-only liveness saw a healthy stream, so nothing fired and
# the raw dump saved the silence. These cases pin the sound heartbeat.
def test_silent_tap_with_live_buffers_triggers_recover():
    vt = _vt(since=0.5, started_ago=600.0, mute=VoiceTyper._MEETING_MUTE_SEC + 30)
    _no_real_recover(vt)
    reason = vt._watchdog_tick()
    assert reason is not None and "silent" in reason
    assert len(vt.recovered) == 1


def test_short_pause_in_speech_is_not_a_stall():
    """A pause between sentences must never restart the stream."""
    vt = _vt(since=0.2, started_ago=600.0, mute=4.0)
    _no_real_recover(vt)
    assert vt._watchdog_tick() is None
    assert vt.recovered == []


def test_quiet_meeting_backs_off_instead_of_restarting_every_90s():
    """Real silence looks the same from here, so the threshold has to grow."""
    vt = _vt(since=0.5, started_ago=600.0, mute=VoiceTyper._MEETING_MUTE_SEC + 5)
    _no_real_recover(vt)
    assert vt._watchdog_tick() is not None
    assert vt._mute_recover_streak == 1
    # Same silence, one tick later: the doubled threshold is not met yet.
    assert vt._watchdog_tick() is None
    assert len(vt.recovered) == 1
    # Twice the wait does clear it.
    vt._sysrec.mute = VoiceTyper._MEETING_MUTE_SEC * 2 + 5
    assert vt._watchdog_tick() is not None
    assert len(vt.recovered) == 2


def test_sound_returning_clears_the_backoff():
    vt = _vt(since=0.5, started_ago=600.0, mute=VoiceTyper._MEETING_MUTE_SEC + 5)
    _no_real_recover(vt)
    vt._watchdog_tick()
    assert vt._mute_recover_streak == 1
    vt._sysrec.mute = 1.0
    assert vt._watchdog_tick() is None
    assert vt._mute_recover_streak == 0


def test_fresh_stream_without_sound_yet_is_left_alone():
    """A recovered stream that has not heard anything must not reset the backoff
    just because its baseline is young — otherwise the cap never engages."""
    vt = _vt(since=0.5, started_ago=600.0, mute=1.0, sound_seen=False)
    _no_real_recover(vt)
    vt._mute_recover_streak = 2
    assert vt._watchdog_tick() is None
    assert vt._mute_recover_streak == 2


# ── output switched mid-meeting (AirPods → speakers) ──────────────────────────
# Льоша, 26.08.2026: "система має відпрацьовувати, навіть якщо я вимикаю
# еірподси і переходжу на динаміки — це інструмент, який не має бекапу".
def test_output_device_change_recovers_immediately():
    """Not after 45 s of proving it by silence — at the next tick, ~3 s."""
    vt = _vt(since=0.2, started_ago=600.0, mute=0.5)
    _no_real_recover(vt)
    _probe["device"] = 2  # AirPods out, sound back on the speakers
    reason = vt._watchdog_tick()
    assert reason is not None and "output device" in reason
    assert len(vt.recovered) == 1


def test_output_device_change_rebases_and_does_not_repeat():
    vt = _vt(since=0.2, started_ago=600.0, mute=0.5)
    _no_real_recover(vt)
    _probe["device"] = 2
    assert vt._watchdog_tick() is not None
    assert vt._watchdog_tick() is None  # same new device — nothing left to react to
    assert len(vt.recovered) == 1


def test_unreadable_device_probe_is_not_evidence_of_anything():
    """A probe that cannot answer must never be read as 'the device changed'."""
    vt = _vt(since=0.2, started_ago=600.0, mute=0.5)
    _no_real_recover(vt)
    _probe["device"] = None
    assert vt._watchdog_tick() is None
    assert vt.recovered == []


def test_deaf_tap_while_output_is_open_recovers_sooner():
    """Speakers held open + nothing but zeros reaching us ⇒ 45 s, not 90 s."""
    vt = _vt(since=0.2, started_ago=600.0, mute=VoiceTyper._MEETING_DEAF_SEC + 2)
    _no_real_recover(vt)
    _probe["running"] = True
    reason = vt._watchdog_tick()
    assert reason is not None and "silent" in reason


def test_same_silence_with_idle_output_waits_for_the_long_threshold():
    """Nothing holding the speakers: this looks exactly like a quiet meeting, so
    the short rung must not apply."""
    vt = _vt(since=0.2, started_ago=600.0, mute=VoiceTyper._MEETING_DEAF_SEC + 2)
    _no_real_recover(vt)
    _probe["running"] = False
    assert vt._watchdog_tick() is None
    assert vt.recovered == []
