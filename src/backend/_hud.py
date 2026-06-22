"""A tiny always-on-top status pill near the menu-bar icon.

Streaming dictation has no obvious "is it working?" signal once the hotkey is a
silent key (a remapped Caps Lock with no LED, a bare modifier): the menu-bar
glyph is easy to miss and the status line stays hidden until the menu is opened.
This floating pill shows the live state — listening / recognizing / buffering —
anchored under the top-right menu bar where the app's icon lives.

It is a *non-activating* borderless panel that ignores mouse events: it never
steals keyboard focus (which would break the very typing it reports on) and never
blocks clicks underneath it. Every AppKit object here must be touched on the main
thread — Tray.show_hud/hide_hud marshal calls in via AppHelper.callAfter.
"""

import contextlib

# NSWindowStyleMaskNonactivatingPanel — a panel that never becomes key/main, so
# showing it can't pull focus away from the field we're typing into.
_NONACTIVATING_PANEL = 1 << 7
_BACKING_BUFFERED = 2  # NSBackingStoreBuffered
_STATUS_WINDOW_LEVEL = 25  # floats above normal windows, near the menu bar
# CanJoinAllSpaces | Stationary — stay put and visible on every Space.
_COLLECTION_ALL_SPACES = (1 << 0) | (1 << 4)

_W, _H = 250.0, 36.0


class StatusHUD:
    def __init__(self):
        self._panel = None
        self._label = None

    def _build(self) -> None:
        from AppKit import NSColor, NSFont, NSPanel, NSTextField, NSView

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (_W, _H)), _NONACTIVATING_PANEL, _BACKING_BUFFERED, False
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(_STATUS_WINDOW_LEVEL)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        with contextlib.suppress(Exception):
            panel.setCollectionBehavior_(_COLLECTION_ALL_SPACES)

        view = NSView.alloc().initWithFrame_(((0.0, 0.0), (_W, _H)))
        view.setWantsLayer_(True)
        layer = view.layer()
        layer.setCornerRadius_(10.0)
        # Dark translucent slate — not pure black, readable over any background.
        layer.setBackgroundColor_(NSColor.colorWithCalibratedWhite_alpha_(0.13, 0.92).CGColor())

        label = NSTextField.alloc().initWithFrame_(((14.0, 8.0), (_W - 28.0, 20.0)))
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(NSColor.whiteColor())
        label.setFont_(NSFont.systemFontOfSize_(13.0))
        with contextlib.suppress(Exception):
            label.cell().setLineBreakMode_(5)  # NSLineBreakByTruncatingTail
        label.setStringValue_("")
        view.addSubview_(label)

        panel.setContentView_(view)
        self._panel, self._label = panel, label

    def _reposition(self) -> None:
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is None or self._panel is None:
            return
        frame = screen.frame()
        x = frame.origin.x + frame.size.width - _W - 12.0
        y = frame.origin.y + frame.size.height - _H - 28.0  # just below the menu bar
        self._panel.setFrameOrigin_((x, y))

    def show(self, text: str) -> None:
        with contextlib.suppress(Exception):
            if self._panel is None:
                self._build()
            self._label.setStringValue_(text)
            self._reposition()
            self._panel.orderFrontRegardless()

    def hide(self) -> None:
        with contextlib.suppress(Exception):
            if self._panel is not None:
                self._panel.orderOut_(None)
