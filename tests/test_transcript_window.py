"""Transcript island — the flags that make its text selectable and copyable.

Regression cover for 14.08.2026: the island was built as a bare borderless
NSPanel, which answers NO to canBecomeKeyWindow. Without key status the text view
never becomes first responder, so the transcript could be neither selected with
the mouse nor copied — while `setSelectable_(True)` in _build made it *look*
configured correctly. These tests pin the window-level contract; the AppKit
drawing glue around it is not exercised.
"""

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS-only backend")


@pytest.fixture
def island():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    from pysar.backend.transcript_window import TranscriptWindow

    w = TranscriptWindow()
    w._build()
    yield w
    w._window.orderOut_(None)


def test_panel_can_become_key_but_not_main(island):
    """Key → the text view can take first responder. Not main → the island never
    poses as the app's document window."""
    assert island._window.canBecomeKeyWindow() is True
    assert island._window.canBecomeMainWindow() is False


def test_focus_stays_polite(island):
    """Taking key must not activate the app or grab focus from empty areas: the
    non-activating style mask plus becomesKeyOnlyIfNeeded are what keep the click
    on the drag strip / padding from stealing focus."""
    from AppKit import NSWindowStyleMaskNonactivatingPanel

    assert island._window.styleMask() & NSWindowStyleMaskNonactivatingPanel
    assert island._window.becomesKeyOnlyIfNeeded() is True


def test_textview_is_selectable_and_asks_for_key(island):
    tv = island._textview
    assert tv.isSelectable() is True
    assert tv.isEditable() is False
    # becomesKeyOnlyIfNeeded consults this on click — it is what hands the panel
    # key status when (and only when) the click lands on the transcript text.
    assert tv.needsPanelToBecomeKey() is True


def test_text_drag_does_not_move_the_window(island):
    """The window is movableByWindowBackground, but a drag over the TEXT must
    select, not move — moving is delegated to the top strip."""
    assert island._window.isMovableByWindowBackground() is True
    assert island._textview.mouseDownCanMoveWindow() is False


def _cmd_event(win, ch):
    from AppKit import NSEvent, NSEventModifierFlagCommand, NSKeyDown

    return NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        NSKeyDown, (0, 0), NSEventModifierFlagCommand, 0, win.windowNumber(), None, ch, ch, False, 0
    )


def test_select_all_is_served_without_a_menu_bar(island):
    """Pysar is a menu-bar agent: the Edit menu exists only while Settings is open,
    so the panel serves ⌘A/⌘C itself through the responder chain. Asserted on ⌘A
    because it is observable without touching the user's clipboard."""
    island._append_main("рядок транскрипту", "sys", "12:03")
    win, tv = island._window, island._textview
    win.orderFrontRegardless()
    win.makeKeyWindow()
    if not win.isKeyWindow():  # no window server (headless CI)
        pytest.skip("window server refused key status")
    assert win.makeFirstResponder_(tv) is True

    assert win.performKeyEquivalent_(_cmd_event(win, "a")) is True
    assert tv.selectedRange().length > 0


def test_plain_key_falls_through(island):
    """Only ⌘-equivalents are intercepted; everything else keeps AppKit's path."""
    from AppKit import NSEvent, NSKeyDown

    win = island._window
    ev = NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
        NSKeyDown, (0, 0), 0, 0, win.windowNumber(), None, "c", "c", False, 0
    )
    assert win.performKeyEquivalent_(ev) is False


# ── Stop button ───────────────────────────────────────────────────────────────
# Regression cover for 17.08.2026: a tester started a capture and could not stop
# it. The only stop lived in the menu-bar item, which her crowded menu bar had
# pushed under the notch, and the island itself offered no control at all; the
# long queue drain then made the eventual click look ignored.


@pytest.fixture
def island_with_stop():
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory

    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    from pysar.backend.transcript_window import TranscriptWindow

    calls = []
    w = TranscriptWindow(on_stop=lambda: calls.append(1))
    w.set_stop_labels("Зупинити", "Зупиняю…")
    w._build()
    w._apply_stop_state()
    w._calls = calls
    yield w
    w._window.orderOut_(None)


def test_stop_button_sits_in_the_top_strip(island_with_stop):
    """Inside the island, fully on-screen, and within the draggable strip — not
    over the transcript text."""
    from pysar.backend.transcript_window import _STRIP_H

    win = island_with_stop
    frame, bounds = win._stop_button.frame(), win._content.bounds()
    assert frame.origin.x + frame.size.width <= bounds.size.width
    assert frame.origin.y + frame.size.height <= bounds.size.height
    assert frame.origin.y >= bounds.size.height - _STRIP_H - 2


def test_stop_button_stays_pinned_on_resize(island_with_stop):
    """The island is resizable; the button must follow the top-right corner."""
    from AppKit import NSMakeRect

    win = island_with_stop
    before = win._stop_button.frame().origin.x
    win._window.setFrame_display_(NSMakeRect(100, 100, 900, 500), True)
    after = win._stop_button.frame()
    bounds = win._content.bounds()
    assert after.origin.x > before  # moved right with the wider window
    assert after.origin.x + after.size.width <= bounds.size.width
    assert after.origin.y + after.size.height <= bounds.size.height


def test_click_fires_once_and_shows_the_drain(island_with_stop):
    """The click paints "stopping" immediately (the drain can take a minute) and
    a second click during the drain must not start a second stop."""
    win = island_with_stop
    win._stop_target.stopClicked_(win._stop_button)
    assert win._calls == [1]
    assert win._stopping is True
    assert win._stop_button.attributedTitle().string() == "Зупиняю…"
    assert win._stop_button.isEnabled() is False

    win._stop_target.stopClicked_(win._stop_button)
    assert win._calls == [1]


def test_reopening_resets_the_caption(island_with_stop):
    """The window object is reused across captures — a new one must not open on
    the previous session's "stopping" caption."""
    win = island_with_stop
    win.set_stopping(True)
    win._apply_stop_state()
    win._stopping = False  # what show() does before ordering the panel front
    win._apply_stop_state()
    assert win._stop_button.attributedTitle().string() == "Зупинити"
    assert win._stop_button.isEnabled() is True


def test_no_button_without_a_callback():
    """A window built without on_stop (any non-meeting use) keeps the bare island."""
    from pysar.backend.transcript_window import TranscriptWindow

    w = TranscriptWindow()
    w._build()
    try:
        assert w._stop_button is None
    finally:
        w._window.orderOut_(None)
