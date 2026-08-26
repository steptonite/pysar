"""The menu item must be written from the main thread, not from whoever calls.

Regression guard for 26.08.2026: `_stop_meeting` runs on a background thread, so
`set_meeting_active(False)` mutated an NSMenuItem off-main. AppKit dropped the
write and `contextlib.suppress` ate the evidence — the menu kept reading
"⏳ Stopping…" for a capture that had already stopped and saved, while the title,
the status line and the HUD (all main-thread-marshalled) were correctly idle.

The assertion is deliberately about the HOP, not about the resulting label: a test
that only checked the label would keep passing on the broken version whenever it
happened to run on the main thread.
"""

import pytest

_macos = pytest.importorskip("src.backend._macos")


class _Item:
    def __init__(self) -> None:
        self.state = 0
        self.title = "start"


class _Deferred:
    """Stands in for AppHelper: records the calls instead of running them."""

    def __init__(self) -> None:
        self.queue = []

    def callAfter(self, fn, *args) -> None:  # AppHelper's own spelling
        self.queue.append((fn, args))

    def run_all(self) -> None:
        for fn, args in self.queue:
            fn(*args)
        self.queue.clear()


@pytest.fixture
def tray(monkeypatch):
    t = object.__new__(_macos.Tray)
    t._meeting_item = _Item()
    t._meeting_capture_mic = True
    t._ui_lang = "en"
    deferred = _Deferred()
    monkeypatch.setattr(_macos, "AppHelper", deferred)
    return t, deferred


def test_stopping_label_is_not_written_on_the_calling_thread(tray):
    t, deferred = tray
    t.set_meeting_stopping()
    assert t._meeting_item.title == "start"  # nothing yet — it was only queued
    assert len(deferred.queue) == 1
    deferred.run_all()
    assert t._meeting_item.state == -1
    assert t._meeting_item.title != "start"


def test_idle_reset_is_not_written_on_the_calling_thread(tray):
    t, deferred = tray
    t.set_meeting_active(False)
    assert len(deferred.queue) == 1
    deferred.run_all()
    assert t._meeting_item.state == 0


def test_stop_then_reset_lands_in_that_order(tray):
    """The bug's shape: stopping label applied, idle reset lost. Both are queued,
    so the last one enqueued is the last one applied — the item ends up idle."""
    t, deferred = tray
    t.set_meeting_stopping()
    t.set_meeting_active(False)
    deferred.run_all()
    assert t._meeting_item.state == 0
    assert t._meeting_item.title == t._meeting_start_title()


def test_mic_label_switch_also_hops(tray):
    t, deferred = tray
    t.set_meeting_capture_mic(False)
    assert t._meeting_capture_mic is False  # plain state, set immediately
    assert len(deferred.queue) == 1
    deferred.run_all()
    assert t._meeting_item.title == t._meeting_start_title()
