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
_MIN_H = 140
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
        self._opacity = 1.0  # backing solidity (liquid-glass slider, 0.0–1.0; the
        # Settings UI shows/sends the inverse — "transparency" — see settings_window.py)
        self._glass = None  # NSGlassEffectView (macOS 26) or NSVisualEffectView fallback
        self._fill = None  # tint underlay below the text; its alpha = the slider value
        self._wake_obs = None  # NSWorkspace notification observer token
        self._theme = "auto"  # "auto" | "light" | "dark" — applied on every (re)build too

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

    def _install_wake_observer(self) -> None:
        """Re-apply the panel's level/collection-behaviour after the Mac wakes.

        The island persists across a whole recording session (unlike the HUD pill,
        which is rebuilt each take) — WindowServer silently stops honouring a
        high-level panel's orderFront after sleep/wake, so without this the island
        shows then immediately vanishes until an app restart. Registers once; the
        handler only acts if the panel is currently visible."""
        if self._wake_obs is not None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSWorkspace

            def _on_wake(_note) -> None:
                def _go() -> None:
                    with contextlib.suppress(Exception):
                        if self._window is not None and self._window.isVisible():
                            self._apply_level()
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
                            self._window.orderFrontRegardless()

                _main_async(_go)

            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            self._wake_obs = nc.addObserverForName_object_queue_usingBlock_(
                "NSWorkspaceDidWakeNotification", None, None, _on_wake
            )

    def set_opacity(self, value) -> None:
        """Liquid-glass control: how *solid* the island's backing is.

        Unlike a window-wide ``alphaValue`` (which would also fade the text), this
        drives only a tint underlay that sits *below* the text. At 1.0 the backing is
        a solid themed panel; toward 0.0 it thins out to full glass (the desktop
        refracting through it) — while the text stays fully crisp; the native
        `NSGlassEffectView` itself keeps the island visible even with no tint at all.
        Clamped to [0.0, 1.0]. Applies live."""
        try:
            v = float(value)
        except (TypeError, ValueError):
            return
        v = max(0.0, min(1.0, v))
        self._opacity = v
        _main_async(self._apply_transp)

    def _apply_transp(self) -> None:
        """Paint the tint underlay at ``self._opacity`` and, on real glass, add a
        faint milk tint so the body stays legible at high transparency. The window
        background colour is resolved under the panel's *current* appearance so a
        light/dark switch repaints with the right colour (CALayer freezes it
        otherwise)."""
        if self._fill is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSColor, NSColorSpace

            v = self._opacity

            def paint():
                with contextlib.suppress(Exception):
                    c = NSColor.windowBackgroundColor().colorUsingColorSpace_(
                        NSColorSpace.sRGBColorSpace()
                    )
                    self._fill.layer().setBackgroundColor_(
                        NSColor.colorWithRed_green_blue_alpha_(
                            c.redComponent(), c.greenComponent(), c.blueComponent(), v
                        ).CGColor()
                    )
                    glass = self._glass
                    if glass is not None and hasattr(glass, "setTintColor_"):
                        is_dark = "Dark" in str(glass.effectiveAppearance().name())
                        base = 0.0 if is_dark else 1.0
                        a = 0.05 + 0.06 * (1.0 - v)  # glassier → a touch more milk
                        glass.setTintColor_(NSColor.colorWithWhite_alpha_(base, a))

            ap = self._glass.effectiveAppearance() if self._glass is not None else None
            if ap is not None and hasattr(ap, "performAsCurrentDrawingAppearance_"):
                ap.performAsCurrentDrawingAppearance_(paint)
            else:
                paint()

    def apply_theme(self, theme: str) -> None:
        """Force the island to light/dark, or follow macOS when 'auto' — mirrors
        SettingsWindow.apply_theme. Without this the panel only ever tracked the
        real system appearance, ignoring the app's manual theme override, so text
        and glass tint stayed on the wrong side of a forced dark/light setting.
        Stores the choice even if the panel isn't built yet, so a later `show()`
        (which calls `_build()`) starts on the right appearance from frame one —
        not just "whatever the window happened to inherit that first paint"."""
        self._theme = theme if theme in ("auto", "light", "dark") else "auto"
        _main_async(self._apply_theme_now)

    def _apply_theme_now(self) -> None:
        """Must run on the main thread (via `_main_async` or from `_build`, which
        is itself always called on the main thread)."""
        if self._window is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSAppearance

            name = {"light": "NSAppearanceNameAqua", "dark": "NSAppearanceNameDarkAqua"}.get(
                self._theme
            )
            self._window.setAppearance_(NSAppearance.appearanceNamed_(name) if name else None)
        # Re-paint the tint/glass under the new appearance right away.
        self._apply_transp()

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
            self._textview.scrollRangeToVisible_(NSMakeRange(storage.length(), 0))

            # Reveal the body word-by-word (live-transcription feel); header instant.
            self._reveal_words(body_start, text, body_attrs.get(NSForegroundColorAttributeName))

    def _reveal_words(self, base_start: int, text: str, base_color=None) -> None:
        """Reveal the appended body word-by-word with a short staggered micro-fade,
        for a live-transcription feel (à la Otter / Granola). The header stays
        instant. Honours the system "Reduce Motion" setting (text appears at once).
        Pure dispatch_after scheduling over fixed ranges — it never scans storage or
        blocks the main thread, and the total reveal is capped (~0.5 s + tail) so a
        long segment can't crawl."""
        if self._textview is None or not text:
            return
        with contextlib.suppress(Exception):
            import re

            from AppKit import NSColor, NSForegroundColorAttributeName, NSWorkspace
            from Foundation import NSMakeRange

            storage = self._textview.textStorage()
            if base_color is None:
                base_color = self._textview.textColor() or NSColor.labelColor()

            if NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion():
                return  # reduced motion → leave the text at full opacity

            words = [(m.start(), m.end() - m.start()) for m in re.finditer(r"\S+", text)]
            if not words:
                return

            # Hide the whole body up-front, then fade each word in on a stagger.
            full_rng = NSMakeRange(base_start, len(text))
            storage.addAttribute_value_range_(
                NSForegroundColorAttributeName, base_color.colorWithAlphaComponent_(0.0), full_rng
            )

            n = len(words)
            stagger = min(0.022, 0.5 / n)  # cap total reveal duration
            micro = (0.40, 0.72, 1.0)  # quick per-word ramp (no hard pop)
            micro_delay = 0.03

            def ramp(rng, alpha):
                with contextlib.suppress(Exception):
                    storage.addAttribute_value_range_(
                        NSForegroundColorAttributeName,
                        base_color.colorWithAlphaComponent_(alpha),
                        rng,
                    )

            try:
                import libdispatch

                for i, (off, ln) in enumerate(words):
                    rng = NSMakeRange(base_start + off, ln)
                    for j, a in enumerate(micro):
                        delay = i * stagger + j * micro_delay
                        when = libdispatch.dispatch_time(
                            libdispatch.DISPATCH_TIME_NOW, int(delay * 1e9)
                        )
                        libdispatch.dispatch_after(
                            when,
                            libdispatch.dispatch_get_main_queue(),
                            lambda r=rng, al=a: ramp(r, al),
                        )
            except Exception:
                ramp(full_rng, 1.0)  # no libdispatch → just show it

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
            NSEdgeInsetsMake,
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
            win.setBecomesKeyOnlyIfNeeded_(True)
            win.setFloatingPanel_(True)
            win.setHidesOnDeactivate_(False)

        from AppKit import NSView

        # ── island backing: REAL Liquid Glass (NSGlassEffectView, macOS 26 Tahoe) ──
        # The desktop refracts through it like a lens and the text laid on top stays
        # perfectly crisp — the transparency slider thins a separate tint underlay
        # (self._fill) rather than the whole window, so lowering it no longer fades
        # the text. Pre-Tahoe falls back to the masked NSVisualEffectView blur.
        glass_cls = None
        with contextlib.suppress(Exception):
            import objc

            glass_cls = objc.lookUpClass("NSGlassEffectView")

        if glass_cls is not None:
            glass = glass_cls.alloc().initWithFrame_(frame)
            with contextlib.suppress(Exception):
                glass.setStyle_(0)  # Regular: frosted glass with a gentle blur
            with contextlib.suppress(Exception):
                # contentLensing OFF: continuous refraction of the moving desktop is
                # the big GPU cost (WindowServer balloons on 8 GB); blur+translucency
                # stay, only the edge shimmer goes.
                glass.set_contentLensing_(False)
            with contextlib.suppress(Exception):
                glass.setCornerRadius_(_RADIUS)
            with contextlib.suppress(Exception):
                if glass.respondsToSelector_("setCornerCurve:"):
                    glass.setCornerCurve_("continuous")
            glass.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            content = NSView.alloc().initWithFrame_(frame)
            content.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            content.setWantsLayer_(True)
            with contextlib.suppress(Exception):
                content.layer().setCornerRadius_(_RADIUS)
                content.layer().setMasksToBounds_(True)
                # circular (default) reads sharper/rounder than macOS's own panels,
                # which all use the "squircle" continuous curve (same as SF Symbols /
                # app icons) — without this the island looks subtly "off-brand".
                content.layer().setCornerCurve_("continuous")
            glass.setContentView_(content)
            win.setContentView_(glass)
            self._glass = glass
        else:
            fx = NSVisualEffectView.alloc().initWithFrame_(frame)
            fx.setMaterial_(NSVisualEffectMaterialHUDWindow)
            fx.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
            fx.setState_(NSVisualEffectStateActive)
            fx.setWantsLayer_(True)
            with contextlib.suppress(Exception):
                fx.layer().setCornerRadius_(_RADIUS)
                fx.layer().setMasksToBounds_(True)
                fx.layer().setCornerCurve_("continuous")
                fx.layer().setBorderWidth_(1.0)
                fx.layer().setBorderColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, 0.14).CGColor()
                )
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
            content = fx
            self._glass = fx

        # ── tint underlay (the slider thins THIS, never the text) ──
        with contextlib.suppress(Exception):
            fill = NSView.alloc().initWithFrame_(content.bounds())
            fill.setWantsLayer_(True)
            fill.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
            with contextlib.suppress(Exception):
                fill.layer().setCornerRadius_(_RADIUS)
                fill.layer().setMasksToBounds_(True)
                fill.layer().setCornerCurve_("continuous")
            content.addSubview_positioned_relativeTo_(fill, -1, None)  # below all content
            self._fill = fill

        # ── scroll view + text view (transparent so the blur shows through) ──
        scroll = NSScrollView.alloc().initWithFrame_(content.bounds())
        scroll.setHasVerticalScroller_(True)
        scroll.setAutohidesScrollers_(True)
        scroll.setBorderType_(0)  # NSNoBorder
        scroll.setDrawsBackground_(False)
        with contextlib.suppress(Exception):
            scroll.contentView().setDrawsBackground_(False)  # clip view must be clear too
        scroll.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        with contextlib.suppress(Exception):
            # Keep the overlay scroller clear of the corner resize-grip decoration
            # (bottom-right) — without this the scroller track paints right through it.
            scroll.setAutomaticallyAdjustsContentInsets_(False)
            scroll.setContentInsets_(NSEdgeInsetsMake(0.0, 0.0, 20.0, 0.0))

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
        content.addSubview_(scroll)
        self._textview = tv

        # ── top drag strip (drag-anywhere handle; the text view eats mouseDown over
        # its own area, so this transparent strip guarantees a reliable grab zone) ──
        with contextlib.suppress(Exception):
            ds = _DragStrip.alloc().initWithFrame_(
                NSMakeRect(
                    0,
                    content.bounds().size.height - _STRIP_H,
                    content.bounds().size.width,
                    _STRIP_H,
                )
            )
            ds.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)  # pinned to top
            content.addSubview_(ds)

        # ── corner resize affordance (subtle native grow-box hint) ──
        # Inset well inside the 16px corner radius so masksToBounds doesn't clip the
        # lines along the curve (the previous grip sat ON the arc → looked broken).
        # Three thin, low-opacity diagonal lines decreasing toward the corner.
        with contextlib.suppress(Exception):
            from Quartz import (
                CAShapeLayer,
                CGPathAddLineToPoint,
                CGPathCreateMutable,
                CGPathMoveToPoint,
            )

            grip = NSView.alloc().initWithFrame_(
                NSMakeRect(content.bounds().size.width - 22, 7, 14, 14)
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
            content.addSubview_(grip)

        # paint the tint underlay at the current slider value
        self._apply_transp()

        # ── delegate (move + resize → frame persistence) ──
        self._delegate = _Delegate.alloc().init()
        self._delegate._owner = self
        win.setDelegate_(self._delegate)
        self._window = win
        self._install_wake_observer()
        with contextlib.suppress(Exception):
            win.invalidateShadow()  # match the shadow to the masked rounded shape from the start
        self._apply_theme_now()  # start on the stored theme, not whatever AppKit inherits by default


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
            with contextlib.suppress(Exception):
                # A borderless, non-opaque window derives its drop shadow from the
                # rendered layer shape, but AppKit doesn't always recompute that
                # automatically mid-drag — without this the shadow can lag behind
                # into a stale (rectangular-looking) shape while resizing.
                notification.object().invalidateShadow()

    return _DelegateImpl


class _DelegateMeta:
    _cls = None

    def alloc(self):
        if _DelegateMeta._cls is None:
            _DelegateMeta._cls = _make_delegate_class()
        return _DelegateMeta._cls.alloc()


_Delegate = _DelegateMeta()
