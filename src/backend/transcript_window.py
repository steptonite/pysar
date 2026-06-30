"""Live transcript window for the "transcribe everything" mode.

A lightweight native NSWindow hosting a read-only NSTextView — meeting/call text
is appended sentence-by-sentence as it streams. Deliberately *not* the WebKit
settings panel: a scrolling read-only log needs no bridge, just an append that
hops to the main thread (segments arrive on a worker thread).

Built lazily and reused across opens, mirroring SettingsWindow's lifecycle so the
app drops back to a menu-bar-only accessory when the window closes.
"""

import contextlib

_WIDTH = 560
_HEIGHT = 640
_MIN_W = 360
_MIN_H = 280


def _main_async(fn) -> None:
    """Run ``fn`` on the main thread (AppKit is not thread-safe; segments arrive on
    the transcription worker)."""
    try:
        import libdispatch

        libdispatch.dispatch_async(libdispatch.dispatch_get_main_queue(), fn)
    except Exception:
        with contextlib.suppress(Exception):
            fn()


class TranscriptWindow:
    """NSWindow + NSTextView. ``show()`` / ``append()`` / ``clear()`` — all of which
    are safe to call from any thread (UI work is marshalled to the main queue)."""

    def __init__(self, title: str = "Pysar — Transcript"):
        self._title = title
        self._window = None
        self._textview = None
        self._delegate = None
        self._on_top = False  # float above other windows (meeting_on_top setting)
        self._labels: dict[str, str] = {"sys": "System", "mic": "You"}
        self._last_source: str | None = None

    # ── public API ────────────────────────────────────────────────────────────
    def show(self, title: str | None = None) -> None:
        if title:
            self._title = title

        def _go():
            if self._window is None:
                self._build()
            from AppKit import NSApp

            NSApp().setActivationPolicy_(0)  # Regular while visible (Dock + Cmd-Tab)
            with contextlib.suppress(Exception):
                from .settings_window import _apply_dock_icon, _install_main_menu

                _install_main_menu()
                _apply_dock_icon()
            self._window.setTitle_(self._title)
            NSApp().activateIgnoringOtherApps_(True)
            self._window.makeKeyAndOrderFront_(None)
            self._apply_level()

        _main_async(_go)

    def set_on_top(self, on: bool) -> None:
        """Float the transcript above other windows (or drop back to normal)."""
        self._on_top = bool(on)
        _main_async(self._apply_level)

    def _apply_level(self) -> None:
        if self._window is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSFloatingWindowLevel, NSNormalWindowLevel

            self._window.setLevel_(NSFloatingWindowLevel if self._on_top else NSNormalWindowLevel)

    def append(self, text: str, source: str | None = None, ts=None) -> None:
        text = (text or "").strip()
        if not text:
            return
        from datetime import datetime

        clock = (ts or datetime.now()).strftime("%H:%M")
        _main_async(lambda: self._append_main(text, source, clock))

    def clear(self) -> None:
        _main_async(self._clear_main)

    def set_source_labels(self, labels: dict[str, str]) -> None:
        """Update the display labels for each source (e.g. ``{"sys": "System", "mic": "You"}``)."""
        if labels:
            self._labels.update({k: v for k, v in labels.items() if v})

    # ── main-thread bodies ──────────────────────────────────────────────────────
    def _append_main(self, text: str, source: str | None, clock: str = "") -> None:
        if self._textview is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import (
                NSAttributedString,
                NSColor,
                NSFont,
                NSFontAttributeName,
                NSForegroundColorAttributeName,
                NSMutableParagraphStyle,
                NSParagraphStyleAttributeName,
            )
            from Foundation import NSMakeRange

            storage = self._textview.textStorage()

            # A small header before every block: "● Source · HH:MM" (or just the
            # time when the source is unknown). Each block is stamped — consecutive
            # lines are no longer grouped silently under one label.
            label_attrs = {}
            label_attrs[NSFontAttributeName] = NSFont.boldSystemFontOfSize_(12.5)
            color_map = {"sys": NSColor.systemBlueColor(), "mic": NSColor.systemOrangeColor()}
            label_attrs[NSForegroundColorAttributeName] = (
                color_map.get(source, NSColor.systemGrayColor())
                if source is not None
                else NSColor.systemGrayColor()
            )
            para = NSMutableParagraphStyle.alloc().init()
            para.setParagraphSpacingBefore_(8.0)
            label_attrs[NSParagraphStyleAttributeName] = para
            if source is not None:
                head = "● " + self._labels.get(source, source) + " · " + clock + "\n"
            else:
                head = "● " + clock + "\n"
            label_str = NSAttributedString.alloc().initWithString_attributes_(head, label_attrs)
            storage.appendAttributedString_(label_str)
            self._last_source = source

            # Append the body text using the established typing attributes
            body_attrs = self._textview.typingAttributes()
            body_str = NSAttributedString.alloc().initWithString_attributes_(
                text + "\n\n", body_attrs
            )
            storage.appendAttributedString_(body_str)
            self._textview.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

    def _clear_main(self) -> None:
        if self._textview is None:
            return
        with contextlib.suppress(Exception):
            self._textview.setString_("")
            self._last_source = None

    # ── build ──────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSColor,
            NSFont,
            NSMakeRect,
            NSMakeSize,
            NSScrollView,
            NSTextView,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )

        frame = NSMakeRect(0, 0, _WIDTH, _HEIGHT)
        scroll = NSScrollView.alloc().initWithFrame_(frame)
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)  # NSNoBorder
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        tv = NSTextView.alloc().initWithFrame_(frame)
        tv.setEditable_(False)
        tv.setSelectable_(True)  # let the user copy from the transcript
        tv.setRichText_(True)   # support attributed speaker labels
        tv.setAutoresizingMask_(NSViewWidthSizable)
        with contextlib.suppress(Exception):
            tv.setFont_(NSFont.systemFontOfSize_(14.0))
            tv.setTextColor_(NSColor.labelColor())
            pad = NSMakeSize(16.0, 14.0)
            tv.setTextContainerInset_(pad)
        scroll.setDocumentView_(tv)
        self._textview = tv

        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskMiniaturizable
            | NSWindowStyleMaskResizable
        )
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        win.setTitle_(self._title)
        win.setContentView_(scroll)
        win.setReleasedWhenClosed_(False)  # reused across opens
        with contextlib.suppress(Exception):
            win.setMinSize_(NSMakeSize(_MIN_W, _MIN_H))
        self._delegate = _Delegate.alloc().init()
        win.setDelegate_(self._delegate)
        win.center()
        self._window = win


def _make_delegate_class():
    """NSWindowDelegate that restores the menu-bar-only footprint on close."""
    from AppKit import NSObject

    class _DelegateImpl(NSObject):
        def windowWillClose_(self, notification):
            with contextlib.suppress(Exception):
                from AppKit import NSApp

                NSApp().setActivationPolicy_(1)  # Accessory (no Dock tile)

    return _DelegateImpl


class _DelegateMeta:
    _cls = None

    def alloc(self):
        if _DelegateMeta._cls is None:
            _DelegateMeta._cls = _make_delegate_class()
        return _DelegateMeta._cls.alloc()


_Delegate = _DelegateMeta()

