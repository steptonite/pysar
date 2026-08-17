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

from pysar.app import VoiceTyper


class _FakeTray:
    def __init__(self):
        self.statuses: list[str] = []
        self.huds: list[str] = []
        self.meeting_states: list[str] = []

    def set_status(self, text: str) -> None:
        self.statuses.append(text)

    def show_hud(self, text: str, state: str = "listening") -> None:
        self.huds.append(text)

    def hide_hud(self) -> None:
        pass

    def notify(self, *a) -> None:
        pass

    def set_title(self, *a) -> None:
        pass

    def set_meeting_active(self, active: bool) -> None:
        self.meeting_states.append("on" if active else "off")

    def set_meeting_stopping(self) -> None:
        self.meeting_states.append("stopping")


class _FakeRecorder:
    """Stands in for SystemAudioRecorder: `since` is what the heartbeat reports."""

    def __init__(self, since: float | None):
        self.since = since
        self.stopped = False

    def seconds_since_audio(self) -> float | None:
        return self.since

    def stop(self) -> None:
        self.stopped = True


def _vt(since: float | None, started_ago: float) -> VoiceTyper:
    import time

    vt = object.__new__(VoiceTyper)
    vt._tray = _FakeTray()
    vt._t = lambda key, **kw: key
    vt._mode = "uk"
    vt._meeting = True
    vt._meeting_stopping = False
    vt._meeting_mic = False
    vt._sysrec = _FakeRecorder(since)
    vt._capture_started_at = time.monotonic() - started_ago
    vt._recover_lock = threading.RLock()
    vt._watchdog_stop = threading.Event()
    vt._watchdog_thread = None
    vt._meeting_recover_count = 0
    vt._meeting_recover_window_start = 0.0
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
