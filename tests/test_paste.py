"""Paste focus-safety: never Cmd+V into the wrong window.

The dictation target is snapshotted at record start; at paste time macOS may
silently refuse to refocus it (14+ blocks programmatic activation of a
non-frontmost app). When focus can't be verified back on the target, the text
must be LEFT on the clipboard for a manual ⌘V rather than pasted blindly.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only backend")


class _FakeApp:
    """Stand-in for NSRunningApplication: a PID + a no-op activate."""

    def __init__(self, pid):
        self._pid = pid
        self.activated = False

    def processIdentifier(self):
        return self._pid

    def activateWithOptions_(self, _opts):
        self.activated = True


def _wire(frontmost_pid):
    """Patch a Paster so it never touches the real clipboard/keyboard.
    `frontmost_pid` is what _frontmost_pid() reports (the focus after activate).

    Patches go on the *instance*, not the class: a paste schedules a background
    clipboard-restore thread, and a class-level patch would let that stale thread
    from one test write into the next test's store (the methods are looked up on
    the class at call time). Instance attributes keep each test isolated."""
    from pysar.backend import Paster

    p = Paster()
    store = {"clip": b""}
    pressed = []
    p._read_clipboard = lambda: store["clip"]
    p._write_clipboard = lambda data: store.__setitem__("clip", data)
    p._frontmost_pid = lambda: frontmost_pid
    p._press_cmd_v = lambda: pressed.append(True)
    return p, store, pressed


def test_pastes_when_no_target():
    p, _store, pressed = _wire(frontmost_pid=999)
    assert p.paste_text("hello", target=None) is True
    assert pressed == [True]


def test_pastes_when_target_regains_focus():
    p, _store, pressed = _wire(frontmost_pid=42)
    target = _FakeApp(42)
    assert p.paste_text("hello", target=target) is True
    assert target.activated
    assert pressed == [True]


def test_holds_in_clipboard_when_focus_not_returned():
    # Focus is on some other app (pid 7), target is pid 42 → never verified.
    p, store, pressed = _wire(frontmost_pid=7)
    target = _FakeApp(42)
    assert p.paste_text("hello world", target=target) is False
    assert pressed == []  # never pasted blindly
    assert store["clip"] == b"hello world"  # text left for a manual ⌘V
