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
_STOP_H = 20  # stop-button height (its corner radius is half of this — a capsule)
_STOP_INSET = 16  # gap to the island's right edge — must clear the 16pt corner arc


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

    def __init__(self, title: str = "Pysar — Transcript", on_frame_change=None, on_stop=None):
        self._title = title
        self._window = None
        self._textview = None
        self._delegate = None
        # Stop control. The island used to have no way out at all: the only stop
        # lived in the menu-bar item, which on a crowded menu bar can sit hidden
        # behind the notch — a tester could start a capture and then not reach the
        # control that ends it (17.08.2026). The button is that missing exit.
        self._on_stop = on_stop  # callable() or None
        self._stop_button = None
        self._stop_target = None
        self._stopping = False
        self._stop_labels = {"stop": "Stop", "stopping": "Stopping…"}
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
        self._appearance_obs = None  # system dark/light change observer token
        self._theme = "auto"  # "auto" | "light" | "dark" — applied on every (re)build too
        self._grip_lines = []  # CAShapeLayers of the corner grip (re-stroked per theme)
        self._content = None  # the view we add subviews to (inside the glass, not the window)

    # ── public API ────────────────────────────────────────────────────────────
    def show(self, title: str | None = None) -> None:
        if title:
            self._title = title

        def _go():
            if self._window is None:
                self._build()
            # A fresh capture always opens on the live (not draining) caption —
            # the window object is reused across sessions.
            self._stopping = False
            self._apply_stop_state()
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

    # ── stop control ──────────────────────────────────────────────────────────
    def set_stop_labels(self, stop: str, stopping: str) -> None:
        """Localized button captions (the window outlives a language switch)."""
        self._stop_labels = {"stop": stop, "stopping": stopping}
        _main_async(self._apply_stop_state)

    def set_stopping(self, on: bool) -> None:
        """Switch the button into its draining state. The stop itself can take up
        to a minute (the queue has to finish transcribing what was already said);
        until this existed, the island just sat there unchanged and the click read
        as ignored."""
        self._stopping = bool(on)
        _main_async(self._apply_stop_state)

    def _apply_stop_state(self) -> None:
        btn = self._stop_button
        if btn is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import (
                NSCenterTextAlignment,
                NSColor,
                NSFont,
                NSFontAttributeName,
                NSForegroundColorAttributeName,
                NSMutableParagraphStyle,
                NSParagraphStyleAttributeName,
            )
            from Foundation import NSAttributedString

            stopping = self._stopping
            text = self._stop_labels.get("stopping" if stopping else "stop", "")
            para = NSMutableParagraphStyle.alloc().init()
            para.setAlignment_(NSCenterTextAlignment)
            # An ATTRIBUTED title, not setTitle_: a borderless NSButton draws its
            # plain title in a fixed control colour that disappears against the
            # light theme. labelColor / secondaryLabelColor are dynamic — they
            # re-resolve per appearance, so the caption survives a theme flip.
            colour = NSColor.secondaryLabelColor() if stopping else NSColor.labelColor()
            attrs = {
                NSFontAttributeName: NSFont.systemFontOfSize_weight_(11.0, 0.23),  # medium
                NSForegroundColorAttributeName: colour,
                NSParagraphStyleAttributeName: para,
            }
            btn.setAttributedTitle_(
                NSAttributedString.alloc().initWithString_attributes_(text, attrs)
            )
            btn.setEnabled_(not stopping)
            btn.setToolTip_(text)
            # The glyph is a template image, so it takes the tint directly — it has
            # to fade with the caption, otherwise the draining state looks half-lit.
            with contextlib.suppress(Exception):
                btn.setContentTintColor_(colour)
            self._layout_stop_button()
            # After the frame: the corner radius is derived from the button height.
            with contextlib.suppress(Exception):
                btn._paint()  # enabled/disabled changes the capsule fill too

    def _layout_stop_button(self) -> None:
        """Pin the button to the top-right corner of the strip (it is re-laid out
        after every title change, since the two captions differ in width)."""
        btn = self._stop_button
        if btn is None or self._content is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSMakeRect

            # self._content, NOT window.contentView(): under Liquid Glass the
            # window's content view is the NSGlassEffectView, and the views we
            # actually add live one level deeper, inside its own content holder.
            content = self._content
            title = btn.attributedTitle()
            # 11pt of padding on each side, plus room for the glyph and its gap.
            w = max(float(title.size().width) + 36.0 if title else 0.0, 76.0)
            h = float(_STOP_H)
            # Sits low in the strip, not centred in it: the island's corners are
            # rounded by 16pt, and a button pinned any higher had its right end
            # clipped by the arc (seen in an offscreen render, 18.08.2026).
            btn.setFrame_(
                NSMakeRect(
                    content.bounds().size.width - w - _STOP_INSET,
                    content.bounds().size.height - _STRIP_H + 3.0,
                    w,
                    h,
                )
            )

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

    def _install_appearance_observer(self) -> None:
        """Re-resolve text/glass colours when the *system* flips light/dark.

        This borderless, non-activating NSPanel doesn't reliably repaint dynamic
        NSColors (labelColor, windowBackgroundColor) on its own when macOS toggles
        appearance live — same class of gap as the sleep/wake bug above, just for
        theme instead of level/collection-behaviour. Without this, text built (or
        last repainted) under one appearance stays that colour even after the
        system — and the island's own glass tint — has visibly moved to the other,
        e.g. dark labelColor text sitting on a now-dark glass background."""
        if self._appearance_obs is not None:
            return
        with contextlib.suppress(Exception):
            from Foundation import NSDistributedNotificationCenter

            def _on_change(_note) -> None:
                _main_async(self._apply_theme_now)

            dnc = NSDistributedNotificationCenter.defaultCenter()
            self._appearance_obs = dnc.addObserverForName_object_queue_usingBlock_(
                "AppleInterfaceThemeChangedNotification", None, None, _on_change
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
            from AppKit import NSApp, NSAppearance, NSAppearanceNameAqua, NSAppearanceNameDarkAqua

            name = {"light": NSAppearanceNameAqua, "dark": NSAppearanceNameDarkAqua}.get(
                self._theme
            )
            if name is None:
                # "auto" — don't just clear the override and hope AppKit inherits
                # it live; explicitly mirror the *current* system appearance, since
                # this panel doesn't reliably re-resolve dynamic colours on its own
                # (see _install_appearance_observer for the live-toggle half of it).
                sys_ap = NSApp.effectiveAppearance()
                name = (
                    sys_ap.bestMatchFromAppearancesWithNames_(
                        [NSAppearanceNameAqua, NSAppearanceNameDarkAqua]
                    )
                    or NSAppearanceNameAqua
                )
            self._window.setAppearance_(NSAppearance.appearanceNamed_(name))
        # Re-paint the tint/glass and the grip under the new appearance right away.
        self._apply_transp()
        self._apply_grip_color()

    def _apply_grip_color(self) -> None:
        """Re-stroke the corner-grip lines under the panel's current appearance.

        CAShapeLayer strokes are static CGColors, so they must be re-resolved on
        every theme change — labelColor at low alpha reads on both glass sides,
        where the old frozen white was invisible on the light theme."""
        if not self._grip_lines or self._window is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSColor

            out = {}

            def make():
                out["c"] = NSColor.labelColor().colorWithAlphaComponent_(0.28).CGColor()

            ap = self._window.effectiveAppearance()
            if hasattr(ap, "performAsCurrentDrawingAppearance_"):
                ap.performAsCurrentDrawingAppearance_(make)
            else:
                make()
            for line in self._grip_lines:
                line.setStrokeColor_(out["c"])

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

            # colorWithAlphaComponent_ FREEZES a dynamic colour (labelColor) to
            # whatever appearance is current at CALL time — verified: the result
            # resolves identically under light and dark. So the intermediate fade
            # colours must be resolved under the PANEL's own appearance, and the
            # final step must restore the ORIGINAL dynamic colour — otherwise every
            # revealed word keeps that frozen colour forever and ignores any later
            # theme change (this, not window appearance, was the black-text-on-dark
            # / white-text-on-light bug: the reveal repainted correct dynamic text
            # with a frozen snapshot one frame later).
            ap = self._window.effectiveAppearance() if self._window is not None else None

            def frozen_faded(alpha):
                out = {}

                def make():
                    out["c"] = base_color.colorWithAlphaComponent_(alpha)

                if ap is not None and hasattr(ap, "performAsCurrentDrawingAppearance_"):
                    ap.performAsCurrentDrawingAppearance_(make)
                else:
                    make()
                return out["c"]

            # Hide the whole body up-front, then fade each word in on a stagger.
            full_rng = NSMakeRange(base_start, len(text))
            storage.addAttribute_value_range_(
                NSForegroundColorAttributeName, frozen_faded(0.0), full_rng
            )

            n = len(words)
            stagger = min(0.022, 0.5 / n)  # cap total reveal duration
            micro = (0.40, 0.72, 1.0)  # quick per-word ramp (no hard pop)
            micro_delay = 0.03
            micro_colors = [base_color if a >= 1.0 else frozen_faded(a) for a in micro]

            def ramp(rng, color):
                with contextlib.suppress(Exception):
                    storage.addAttribute_value_range_(NSForegroundColorAttributeName, color, rng)

            try:
                import libdispatch

                for i, (off, ln) in enumerate(words):
                    rng = NSMakeRange(base_start + off, ln)
                    for j, c in enumerate(micro_colors):
                        delay = i * stagger + j * micro_delay
                        when = libdispatch.dispatch_time(
                            libdispatch.DISPATCH_TIME_NOW, int(delay * 1e9)
                        )
                        libdispatch.dispatch_after(
                            when,
                            libdispatch.dispatch_get_main_queue(),
                            lambda r=rng, cc=c: ramp(r, cc),
                        )
            except Exception:
                ramp(full_rng, base_color)  # no libdispatch → just show it

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
        # _IslandPanel, not a bare NSPanel: a borderless window answers NO to
        # canBecomeKeyWindow, which silently made the transcript unselectable
        # (see the class comment below).
        win = _IslandPanel.alloc().initWithContentRect_styleMask_backing_defer_(
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

        # ── stop button (top-right of the strip) ──
        # Added after the strip, so it sits on top of it and gets the clicks.
        if self._on_stop is not None:
            with contextlib.suppress(Exception):
                from AppKit import NSCenterTextAlignment, NSImageLeft

                self._stop_target = _StopTarget.alloc().init()
                self._stop_target._owner = self
                btn = _StopButton.alloc().initWithFrame_(NSMakeRect(0, 0, 84, _STOP_H))
                btn.setBordered_(False)
                btn.setAlignment_(NSCenterTextAlignment)
                btn.setTarget_(self._stop_target)
                btn.setAction_("stopClicked:")
                # Flexible left + bottom margins = pinned to the top-right corner
                # while the island is resized.
                btn.setAutoresizingMask_(NSViewMinXMargin | NSViewMinYMargin)
                btn.setWantsLayer_(True)
                # A small filled square left of the caption. The glyph is what the
                # eye lands on first — "this is a control", before any word is read
                # — and it survives a language switch unchanged.
                with contextlib.suppress(Exception):
                    from AppKit import NSImage, NSImageSymbolConfiguration

                    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                        "stop.fill", None
                    )
                    if img is not None:
                        cfg = NSImageSymbolConfiguration.configurationWithPointSize_weight_scale_(
                            9.0, 6, 1
                        )
                        with contextlib.suppress(Exception):
                            img = img.imageWithSymbolConfiguration_(cfg)
                        btn.setImage_(img)
                        btn.setImagePosition_(NSImageLeft)
                        btn.setImageScaling_(0)  # NSImageScaleProportionallyDown
                        # Keep the glyph next to the caption instead of parked at
                        # the far edge of the button (which read as two loose bits).
                        with contextlib.suppress(Exception):
                            btn.setImageHugsTitle_(True)
                self._stop_button = btn
                content.addSubview_(btn)
                self._apply_stop_state()

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
            self._grip_lines = []
            for sx, sy, ex, ey in ((3, 12, 12, 3), (7, 12, 12, 7), (11, 12, 12, 11)):
                path = CGPathCreateMutable()
                CGPathMoveToPoint(path, None, sx, sy)
                CGPathAddLineToPoint(path, None, ex, ey)
                line = CAShapeLayer.alloc().init()
                line.setFillColor_(NSColor.clearColor().CGColor())
                line.setLineWidth_(1.0)
                line.setLineCap_("round")
                line.setPath_(path)
                grip.layer().addSublayer_(line)
                self._grip_lines.append(line)
            content.addSubview_(grip)
            # stroke colour is theme-dependent (a frozen white was invisible on the
            # light theme) — painted by _apply_grip_color / _apply_theme_now below

        # paint the tint underlay at the current slider value
        self._apply_transp()

        # ── delegate (move + resize → frame persistence) ──
        self._delegate = _Delegate.alloc().init()
        self._delegate._owner = self
        win.setDelegate_(self._delegate)
        self._window = win
        self._content = content
        self._install_wake_observer()
        self._install_appearance_observer()
        with contextlib.suppress(Exception):
            win.invalidateShadow()  # match the shadow to the masked rounded shape from the start
        self._apply_theme_now()  # start on the stored theme, not whatever AppKit inherits by default


# ── Island panel class (lazy, same pattern as the drag strip / delegate) ───────
def _make_island_class():
    import objc
    from AppKit import NSPanel

    class _IslandPanelImpl(NSPanel):
        # A BORDERLESS window answers NO to canBecomeKeyWindow — AppKit grants key
        # status only to windows with a title/resize bar. That single default made
        # the whole island read-only-to-the-eye: no key window → the NSTextView
        # never becomes first responder → a mouse drag never starts a selection and
        # ⌘C is delivered to whatever app is frontmost. `setSelectable_(True)` in
        # _build was therefore dead code — permission granted, access denied.
        #
        # Overriding it here (rather than switching to a titled window) keeps the
        # island's whole look. Focus stays polite because of the two flags already
        # set in _build: NSWindowStyleMaskNonactivatingPanel means taking key never
        # activates the app (no Dock switch, no menu-bar flip), and
        # becomesKeyOnlyIfNeeded=YES means the panel only takes key when the click
        # lands on a view that says it needs it — the text view (its
        # needsPanelToBecomeKey is YES) — while clicks on the drag strip, the
        # padding or the grip move/resize the island without stealing focus at all.
        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            # Key (to select/copy) but never main: this is an accessory island, and
            # main-window status is what makes AppKit treat it as the app's document
            # window (menu-bar ownership, window-menu entry).
            return False

        def performKeyEquivalent_(self, event):
            """Serve ⌘C / ⌘A ourselves instead of relying on the menu bar.

            Pysar runs as a menu-bar agent, and its Edit menu (with the standard
            copy:/selectAll: key equivalents) is installed only while the Settings
            window is open — see settings_window._install_main_menu. With just the
            island on screen there is no Edit menu, so a key window alone would give
            mouse selection but STILL no ⌘C. Routing the action through the responder
            chain (target=None) is exactly what a menu item does, so the text view
            handles it with its native implementation — including the case where the
            user never clicked into the text and nothing is selected (the action
            simply finds no responder and we fall through)."""
            handled = False
            with contextlib.suppress(Exception):
                from AppKit import NSApp, NSEventModifierFlagCommand

                mods = event.modifierFlags()
                if mods & NSEventModifierFlagCommand:
                    key = (event.charactersIgnoringModifiers() or "").lower()
                    sel = {"c": "copy:", "a": "selectAll:"}.get(key)
                    if sel is not None:
                        handled = bool(NSApp().sendAction_to_from_(sel, None, self))
            if handled:
                return True
            return objc.super(_IslandPanelImpl, self).performKeyEquivalent_(event)

    return _IslandPanelImpl


class _IslandPanelMeta:
    _cls = None

    def alloc(self):
        if _IslandPanelMeta._cls is None:
            _IslandPanelMeta._cls = _make_island_class()
        return _IslandPanelMeta._cls.alloc()


_IslandPanel = _IslandPanelMeta()


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


# ── Stop button view class (lazy, same pattern as the drag strip) ─────────────
# A plain borderless NSButton on glass reads as a flat grey slab: no edge, no
# reaction to the cursor, square-ish corners at 20px with a 10px radius. This
# subclass paints a proper capsule — a hairline refraction edge and a fill that
# answers hover and press — and repaints itself when the system flips theme,
# which a CGColor baked once at build time cannot do.
def _make_stop_button_class():
    import objc
    from AppKit import (
        NSButton,
        NSColor,
        NSTrackingActiveAlways,
        NSTrackingArea,
        NSTrackingInVisibleRect,
        NSTrackingMouseEnteredAndExited,
    )

    class _StopButtonImpl(NSButton):
        def _paint(self):
            """Repaint the capsule for the current appearance + mouse state."""
            with contextlib.suppress(Exception):
                layer = self.layer()
                if layer is None:
                    return
                dark = "Dark" in str(self.effectiveAppearance().name())
                # Milk on dark glass, ink on light glass — a single neutral, never
                # an accent colour: the island sits over someone else's window and
                # a coloured chip would fight whatever is underneath.
                base = 1.0 if dark else 0.0
                alpha = 0.10 if dark else 0.055
                if not self.isEnabled():
                    alpha *= 0.55  # draining: present, but clearly not clickable
                elif getattr(self, "_pressed", False):
                    alpha += 0.11
                elif getattr(self, "_hover", False):
                    alpha += 0.06
                layer.setBackgroundColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(base, alpha).CGColor()
                )
                layer.setBorderWidth_(1.0)
                layer.setBorderColor_(
                    NSColor.colorWithCalibratedWhite_alpha_(
                        1.0 if dark else 0.0, 0.16 if dark else 0.09
                    ).CGColor()
                )
                # Radius follows the height, so it stays a capsule if the size changes.
                layer.setCornerRadius_(self.bounds().size.height / 2.0)
                with contextlib.suppress(Exception):
                    layer.setCornerCurve_("continuous")

        def viewDidChangeEffectiveAppearance(self):
            self._paint()

        def updateTrackingAreas(self):
            objc.super(_StopButtonImpl, self).updateTrackingAreas()
            with contextlib.suppress(Exception):
                for area in list(self.trackingAreas()):
                    self.removeTrackingArea_(area)
                # ActiveAlways, not ActiveInKeyWindow: the island is a
                # non-activating panel and never becomes key, so the key-window
                # variant would never fire.
                opts = (
                    NSTrackingMouseEnteredAndExited
                    | NSTrackingActiveAlways
                    | NSTrackingInVisibleRect
                )
                self.addTrackingArea_(
                    NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
                        self.bounds(), opts, self, None
                    )
                )

        def mouseEntered_(self, _event):
            self._hover = True
            self._paint()

        def mouseExited_(self, _event):
            self._hover = False
            self._pressed = False
            self._paint()

        def mouseDown_(self, event):
            self._pressed = True
            self._paint()
            # super's mouseDown_ runs the whole tracking loop and fires the action
            # on mouse-up, so the release repaint below lands after the click.
            objc.super(_StopButtonImpl, self).mouseDown_(event)
            self._pressed = False
            self._paint()

    return _StopButtonImpl


class _StopButtonMeta:
    _cls = None

    def alloc(self):
        if _StopButtonMeta._cls is None:
            _StopButtonMeta._cls = _make_stop_button_class()
        return _StopButtonMeta._cls.alloc()


_StopButton = _StopButtonMeta()


# ── Stop-button target (lazy, same pattern as the drag strip) ─────────────────
def _make_stop_target_class():
    from AppKit import NSObject

    class _StopTargetImpl(NSObject):
        def stopClicked_(self, _sender):
            owner = getattr(self, "_owner", None)
            if owner is None or owner._on_stop is None or owner._stopping:
                return
            # Paint the draining state before the callback: stopping drains the
            # transcription queue and can take a while, and the click must be
            # acknowledged in the same frame it happened. Set + repaint directly
            # rather than via set_stopping(), which defers to the main queue —
            # a button action already runs there, so deferring would only push
            # the acknowledgement into the next loop turn.
            owner._stopping = True
            owner._apply_stop_state()
            with contextlib.suppress(Exception):
                owner._on_stop()

    return _StopTargetImpl


class _StopTargetMeta:
    _cls = None

    def alloc(self):
        if _StopTargetMeta._cls is None:
            _StopTargetMeta._cls = _make_stop_target_class()
        return _StopTargetMeta._cls.alloc()


_StopTarget = _StopTargetMeta()


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
