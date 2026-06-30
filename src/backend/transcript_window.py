"""Live transcript window — floating borderless "island" panel.

A lightweight NSPanel hosting a read-only NSTextView over a blur-vibrancy
background with a subtle border and a corner grip. Meeting/call text is appended
sentence-by-sentence; all UI work is marshalled to the main thread (segments
arrive on a worker thread). The panel floats above everything (including
full-screen video) but stays below the menu bar, never activates the app or
shows a Dock tile, and remembers its frame across sessions via the
*on_frame_change* callback.
"""

import contextlib

_WIDTH = 560
_HEIGHT = 640
_MIN_W = 360
_MIN_H = 280
_RADIUS = 16.0
_STRIP_H = 30  # draggable top strip height


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
    """NSPanel + NSTextView. ``show()`` / ``hide()`` / ``append()`` / ``clear()`` —
    all are safe to call from any thread (UI work is marshalled to the main queue)."""

    def __init__(self, title: str = "Pysar — Transcript", on_frame_change=None):
        self._title = title
        self._window = None
        self._textview = None
        self._delegate = None
        self._on_top = False  # kept for API compat (island always floats high)
        self._labels: dict[str, str] = {"sys": "System", "mic": "You"}
        self._last_source: str | None = None
        self._saved_frame: dict | None = None
        self._on_frame_change = on_frame_change  # callable(dict) or None
        self._opacity = 1.0  # whole-panel translucency (liquid-glass slider, 0.4–1.0)

    # ── public API ────────────────────────────────────────────────────────────
    def show(self, title: str | None = None) -> None:
        if title:
            self._title = title

        def _go():
            if self._window is None:
                self._build()
            # Position: saved frame if we have one, else the default top-right.
            frame = None
            if self._saved_frame:
                from AppKit import NSMakeRect

                f = self._saved_frame
                frame = NSMakeRect(
                    f.get("x", 0), f.get("y", 0), f.get("w", _WIDTH), f.get("h", _HEIGHT)
                )
            if frame is None:
                frame = self._default_frame()
            frame = self._clamp_to_visible(frame)
            with contextlib.suppress(Exception):
                self._window.setFrame_display_(frame, True)
            self._apply_level()
            with contextlib.suppress(Exception):
                from AppKit import (
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary,
                )

                self._window.setCollectionBehavior_(
                    NSWindowCollectionBehaviorCanJoinAllSpaces
                    | NSWindowCollectionBehaviorFullScreenAuxiliary
                    | NSWindowCollectionBehaviorStationary
                )
            # Bring the panel to front WITHOUT activating the app or stealing focus.
            with contextlib.suppress(Exception):
                self._window.orderFrontRegardless()

        _main_async(_go)

    def hide(self) -> None:
        def _go():
            if self._window:
                with contextlib.suppress(Exception):
                    self._window.orderOut_(None)

        _main_async(_go)

    def set_on_top(self, on: bool) -> None:
        """Kept for caller compatibility; the island always floats above everything."""
        self._on_top = bool(on)
        _main_async(self._apply_level)

    def _apply_level(self) -> None:
        if self._window is None:
            return
        with contextlib.suppress(Exception):
            # The island floats above EVERYTHING, including other apps' full-screen
            # video — that's the whole point, so it needs the very high screen-saver
            # level. The menu-bar overlap (a separate bug) is solved by clamping the
            # frame into visibleFrame (see _clamp_to_visible): a high level can't draw
            # over the menu bar if the window is never positioned in that region.
            try:
                from AppKit import NSScreenSaverWindowLevel

                level = NSScreenSaverWindowLevel
            except ImportError:
                from AppKit import NSStatusWindowLevel

                level = NSStatusWindowLevel
            self._window.setLevel_(level)

    def set_opacity(self, value) -> None:
        """Set the island's overall translucency (liquid-glass control).

        *value* is clamped to [0.4, 1.0] so the panel can get glassier without ever
        vanishing. Applies live if the panel already exists."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        v = max(0.4, min(1.0, v))
        self._opacity = v

        def _go():
            if self._window is not None:
                with contextlib.suppress(Exception):
                    self._window.setAlphaValue_(v)

        _main_async(_go)

    def set_frame(self, frame: dict | None) -> None:
        """Store a frame; apply immediately if the panel already exists."""
        self._saved_frame = frame
        if self._window is None or not frame:
            return

        def _go():
            with contextlib.suppress(Exception):
                from AppKit import NSMakeRect

                rect = NSMakeRect(
                    frame.get("x", 0),
                    frame.get("y", 0),
                    frame.get("w", _WIDTH),
                    frame.get("h", _HEIGHT),
                )
                rect = self._clamp_to_visible(rect)
                self._window.setFrame_display_(rect, True)

        _main_async(_go)

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

    # ── frame persistence helpers ─────────────────────────────────────────────
    def _emit_frame(self, frame_dict: dict) -> None:
        if self._on_frame_change is not None:
            with contextlib.suppress(Exception):
                self._on_frame_change(frame_dict)

    def _default_frame(self):
        """Top-right of the main screen with a 24 px inset, clamped to the visible area."""
        from AppKit import NSMakeRect, NSScreen

        screen = NSScreen.mainScreen()
        if screen is None:
            return NSMakeRect(120, 120, _WIDTH, _HEIGHT)
        visible = screen.visibleFrame()
        x = visible.origin.x + visible.size.width - _WIDTH - 24
        y = visible.origin.y + visible.size.height - _HEIGHT - 24
        if x < visible.origin.x:
            x = visible.origin.x
        if y < visible.origin.y:
            y = visible.origin.y
        return NSMakeRect(x, y, _WIDTH, _HEIGHT)

    def _clamp_to_visible(self, rect):
        """Keep *rect* inside the main screen's visible area (below menu bar, beside Dock)."""
        from AppKit import NSMakeRect, NSScreen

        screen = NSScreen.mainScreen()
        if screen is None:
            return rect
        vis = screen.visibleFrame()
        w = min(max(rect.size.width, _MIN_W), vis.size.width)
        h = min(max(rect.size.height, _MIN_H), vis.size.height)
        x = max(vis.origin.x, min(rect.origin.x, vis.origin.x + vis.size.width - w))
        y = max(vis.origin.y, min(rect.origin.y, vis.origin.y + vis.size.height - h))
        return NSMakeRect(x, y, w, h)

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

            # Header before every block. ONE accent: the coloured dot carries the
            # speaker identity; the "name · time" text stays a quiet secondary grey
            # so the transcript body reads first. Generous space before each block
            # separates speakers; the header sits tight against its own body.
            para = NSMutableParagraphStyle.alloc().init()
            para.setParagraphSpacingBefore_(16.0)
            color_map = {"sys": NSColor.systemBlueColor(), "mic": NSColor.systemOrangeColor()}
            dot_color = color_map.get(source, NSColor.systemGrayColor())
            dot_attrs = {
                NSFontAttributeName: NSFont.boldSystemFontOfSize_(12.5),
                NSForegroundColorAttributeName: dot_color,
                NSParagraphStyleAttributeName: para,
            }
            meta_attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(12.0),
                NSForegroundColorAttributeName: NSColor.secondaryLabelColor(),
                NSParagraphStyleAttributeName: para,
            }
            if source is not None:
                meta_text = " " + self._labels.get(source, source) + " · " + clock + "\n"
            else:
                meta_text = " " + clock + "\n"
            storage.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_("●", dot_attrs)
            )
            storage.appendAttributedString_(
                NSAttributedString.alloc().initWithString_attributes_(meta_text, meta_attrs)
            )
            self._last_source = source

            # Append the body with explicit neutral attributes — only the dot is
            # coloured; the spoken text stays in the primary label colour. A single
            # trailing newline keeps the header close to its body.
            body_attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_(14.0),
                NSForegroundColorAttributeName: NSColor.labelColor(),
            }
            body_start = storage.length()
            body_str = NSAttributedString.alloc().initWithString_attributes_(
                text + "\n", body_attrs
            )
            storage.appendAttributedString_(body_str)
            body_len = storage.length() - body_start
            self._textview.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

            # Fade-in the body of the new block (the header stays instant).
            self._fade_range(body_start, body_len, body_attrs.get(NSForegroundColorAttributeName))

    def _fade_range(self, start: int, length: int, base_color=None) -> None:
        """Ramp the appended body from near-transparent to full opacity over ~150 ms.

        Honours the system "Reduce Motion" setting (text appears instantly then).
        Animates a single colour over one fixed range — no storage scanning, so it
        can never stall the main thread."""
        if length <= 0 or self._textview is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSColor, NSForegroundColorAttributeName, NSWorkspace
            from Foundation import NSMakeRange

            if NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion():
                return  # honour reduced motion — no fade

            if base_color is None:
                base_color = self._textview.textColor() or NSColor.labelColor()
            storage = self._textview.textStorage()
            rng = NSMakeRange(start, length)
            steps = 6
            step_delay = 0.025  # ~25 ms × 6 ≈ 150 ms

            def apply_step(step):
                with contextlib.suppress(Exception):
                    alpha = min(1.0, 0.15 + 0.85 * step / (steps - 1))
                    color = base_color.colorWithAlphaComponent_(alpha)
                    storage.addAttribute_value_range_(NSForegroundColorAttributeName, color, rng)

            for step in range(steps):
                try:
                    import libdispatch

                    when = libdispatch.dispatch_time(
                        libdispatch.DISPATCH_TIME_NOW, int(step * step_delay * 1e9)
                    )
                    libdispatch.dispatch_after(
                        when, libdispatch.dispatch_get_main_queue(), lambda s=step: apply_step(s)
                    )
                except Exception:
                    apply_step(step)

    def _clear_main(self) -> None:
        if self._textview is None:
            return
        with contextlib.suppress(Exception):
            self._textview.setString_("")
            self._last_source = None

    # ── build the floating-island panel ─────────────────────────────────────────
    def _build(self) -> None:
        from AppKit import (
            NSBackingStoreBuffered,
            NSBezierPath,
            NSColor,
            NSFont,
            NSImage,
            NSMakeRect,
            NSMakeSize,
            NSPanel,
            NSScrollView,
            NSTextView,
            NSViewHeightSizable,
            NSViewMaxYMargin,
            NSViewMinXMargin,
            NSViewMinYMargin,
            NSViewWidthSizable,
            NSVisualEffectBlendingModeBehindWindow,
            NSVisualEffectMaterialHUDWindow,
            NSVisualEffectStateActive,
            NSVisualEffectView,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
            NSWindowStyleMaskResizable,
        )

        frame = NSMakeRect(0, 0, _WIDTH, _HEIGHT)

        # ── panel (borderless, non-activating, resizable) ──
        style = (
            NSWindowStyleMaskBorderless
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskNonactivatingPanel
        )
        win = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        win.setTitle_(self._title)
        win.setOpaque_(False)
        win.setBackgroundColor_(NSColor.clearColor())
        win.setHasShadow_(True)
        win.setMovableByWindowBackground_(True)  # padding/empty areas drag too
        win.setReleasedWhenClosed_(False)  # reused across opens
        win.setMinSize_(NSMakeSize(_MIN_W, _MIN_H))
        with contextlib.suppress(Exception):
            win.setAlphaValue_(self._opacity)  # liquid-glass translucency
        with contextlib.suppress(Exception):
            win.setBecomesKeyOnlyIfNeeded_(True)
            win.setFloatingPanel_(True)
            win.setHidesOnDeactivate_(False)

        # ── vibrancy island (content view) ──
        fx = NSVisualEffectView.alloc().initWithFrame_(frame)
        fx.setMaterial_(NSVisualEffectMaterialHUDWindow)
        fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        fx.setState_(NSVisualEffectStateActive)
        fx.setWantsLayer_(True)
        with contextlib.suppress(Exception):
            fx.layer().setCornerRadius_(_RADIUS)
            fx.layer().setMasksToBounds_(True)
            fx.layer().setBorderWidth_(1.0)
            fx.layer().setBorderColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.14).CGColor())
        fx.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        # ── rounded mask image so the vibrancy AND the window shadow follow the
        # rounded shape (fixes the square white corner artifact). Cap insets keep
        # the corners crisp while the centre stretches on resize. ──
        with contextlib.suppress(Exception):
            size = NSMakeSize(_WIDTH, _HEIGHT)

            def _draw_mask(dst_rect):
                NSColor.blackColor().set()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(0, 0, _WIDTH, _HEIGHT), _RADIUS, _RADIUS
                ).fill()
                return True

            mask = NSImage.imageWithSize_flipped_drawingHandler_(size, False, _draw_mask)
            with contextlib.suppress(Exception):
                from AppKit import NSImageResizingModeStretch
                from Foundation import NSEdgeInsetsMake

                mask.setCapInsets_(NSEdgeInsetsMake(_RADIUS, _RADIUS, _RADIUS, _RADIUS))
                mask.setResizingMode_(NSImageResizingModeStretch)
            fx.setMaskImage_(mask)

        win.setContentView_(fx)

        # ── scroll view + text view (transparent so the blur shows through) ──
        scroll = NSScrollView.alloc().initWithFrame_(fx.bounds())
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)  # NSNoBorder
        scroll.setDrawsBackground_(False)
        with contextlib.suppress(Exception):
            scroll.contentView().setDrawsBackground_(False)  # clip view must be clear too
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)

        tv = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, _WIDTH, _HEIGHT))
        tv.setEditable_(False)
        tv.setSelectable_(True)  # let the user copy from the transcript
        tv.setRichText_(True)  # support attributed speaker labels
        tv.setDrawsBackground_(False)
        tv.setBackgroundColor_(NSColor.clearColor())
        tv.setAutoresizingMask_(NSViewWidthSizable)
        # ── live text reflow during window resize ──
        tv.setHorizontallyResizable_(False)
        tv.setVerticallyResizable_(True)
        tv.setMinSize_(NSMakeSize(0.0, 0.0))
        tv.setMaxSize_(NSMakeSize(1.0e7, 1.0e7))
        with contextlib.suppress(Exception):
            tc = tv.textContainer()
            tc.setWidthTracksTextView_(True)
            tc.setContainerSize_(NSMakeSize(tv.bounds().size.width, 1.0e7))
        with contextlib.suppress(Exception):
            tv.setFont_(NSFont.systemFontOfSize_(14.0))
            tv.setTextColor_(NSColor.labelColor())
            # top inset clears the drag strip so the first line isn't hidden under it
            tv.setTextContainerInset_(NSMakeSize(20.0, float(_STRIP_H)))
        scroll.setDocumentView_(tv)
        fx.addSubview_(scroll)
        self._textview = tv

        # ── top drag strip (drag-anywhere handle; the text view eats mouseDown over
        # its own area, so this transparent strip guarantees a reliable grab zone) ──
        with contextlib.suppress(Exception):
            ds = _DragStrip.alloc().initWithFrame_(
                NSMakeRect(0, fx.bounds().size.height - _STRIP_H, fx.bounds().size.width, _STRIP_H)
            )
            ds.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)  # pinned to top
            fx.addSubview_(ds)

        # ── corner resize affordance (subtle native grow-box hint) ──
        # Inset well inside the 16px corner radius so masksToBounds doesn't clip the
        # lines along the curve (the previous grip sat ON the arc → looked broken).
        # Three thin, low-opacity diagonal lines decreasing toward the corner.
        with contextlib.suppress(Exception):
            from AppKit import NSView
            from Quartz import (
                CAShapeLayer,
                CGPathAddLineToPoint,
                CGPathCreateMutable,
                CGPathMoveToPoint,
            )

            grip = NSView.alloc().initWithFrame_(
                NSMakeRect(fx.bounds().size.width - 22, 7, 14, 14)
            )
            grip.setAutoresizingMask_(NSViewMinXMargin | NSViewMaxYMargin)
            grip.setWantsLayer_(True)
            for sx, sy, ex, ey in ((3, 12, 12, 3), (7, 12, 12, 7), (11, 12, 12, 11)):
                path = CGPathCreateMutable()
                CGPathMoveToPoint(path, None, sx, sy)
                CGPathAddLineToPoint(path, None, ex, ey)
                line = CAShapeLayer.alloc().init()
                line.setStrokeColor_(NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.18).CGColor())
                line.setFillColor_(NSColor.clearColor().CGColor())
                line.setLineWidth_(1.0)
                line.setLineCap_("round")
                line.setPath_(path)
                grip.layer().addSublayer_(line)
            fx.addSubview_(grip)

        # ── delegate (move + resize → frame persistence) ──
        self._delegate = _Delegate.alloc().init()
        self._delegate._owner = self
        win.setDelegate_(self._delegate)
        self._window = win


# ── Drag-strip view class (lazy, same pattern as the delegate) ─────────────────
def _make_dragstrip_class():
    from AppKit import NSView

    class _DragStripImpl(NSView):
        # NOTE: the selector is ``mouseDownCanMoveWindow`` (no trailing underscore —
        # it's a zero-arg property getter). A trailing underscore would register the
        # wrong selector ``mouseDownCanMoveWindow:`` that AppKit never calls.
        def mouseDownCanMoveWindow(self):
            return True

        def mouseDown_(self, event):
            with contextlib.suppress(Exception):
                window = self.window()
                if window is not None and hasattr(window, "performWindowDragWithEvent_"):
                    window.performWindowDragWithEvent_(event)

    return _DragStripImpl


class _DragStripMeta:
    _cls = None

    def alloc(self):
        if _DragStripMeta._cls is None:
            _DragStripMeta._cls = _make_dragstrip_class()
        return _DragStripMeta._cls.alloc()


_DragStrip = _DragStripMeta()


# ── NSWindowDelegate (frame-persistence only) ──────────────────────────────────
def _make_delegate_class():
    from AppKit import NSObject

    class _DelegateImpl(NSObject):
        def windowDidMove_(self, notification):
            with contextlib.suppress(Exception):
                rect = notification.object().frame()
                owner = getattr(self, "_owner", None)
                if owner is not None:
                    owner._emit_frame(
                        {
                            "x": rect.origin.x,
                            "y": rect.origin.y,
                            "w": rect.size.width,
                            "h": rect.size.height,
                        }
                    )

        def windowDidResize_(self, notification):
            self.windowDidMove_(notification)  # same payload: report the new frame

    return _DelegateImpl


class _DelegateMeta:
    _cls = None

    def alloc(self):
        if _DelegateMeta._cls is None:
            _DelegateMeta._cls = _make_delegate_class()
        return _DelegateMeta._cls.alloc()


_Delegate = _DelegateMeta()
