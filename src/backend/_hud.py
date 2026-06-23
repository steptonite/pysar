"""A small status pill that drops out of our menu-bar icon during streaming.

Streaming dictation has no obvious "is it working?" signal once the hotkey is a
silent key (a remapped Caps Lock with no LED, a bare modifier): the menu-bar
glyph is easy to miss and the status line stays hidden until the menu is opened.
This floating pill shows the live state — listening / recognizing / buffering —
anchored directly under the app's own status-bar button, so it reads as dropping
out of the icon rather than floating in a random corner.

Design notes:
- Background is a native NSVisualEffectView (a real system material) that the OS
  tints for the active appearance — no flat black box. The panel's appearance is
  forced to the app theme (auto / light / dark) so it never clashes with a light
  menu bar.
- A single coloured dot carries the state (red listening / amber recognizing /
  blue buffering) instead of an emoji, and the capsule hugs its text instead of
  being a fixed-width bar.
- On first appearance it springs in (scale + fade) so it feels like it pops out
  of the icon; later text/state updates within the same take don't re-animate.

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

# NSVisualEffectView material/blending/state — a native HUD material the system
# tints for the active appearance (light vs dark) on its own.
_MATERIAL_HUD = 13  # NSVisualEffectMaterialHUDWindow
_BLEND_BEHIND_WINDOW = 0  # NSVisualEffectBlendingModeBehindWindow
_STATE_ACTIVE = 1  # NSVisualEffectStateActive

_APPEARANCE_NAMES = {"light": "NSAppearanceNameAqua", "dark": "NSAppearanceNameDarkAqua"}

# Layout (points). The pill hugs its text between these paddings.
_H = 30.0
_PAD_L, _DOT, _GAP, _PAD_R = 12.0, 8.0, 8.0, 14.0
_FONT_SZ = 13.0
_MIN_W, _MAX_W = 96.0, 340.0
_RADIUS = _H / 2.0  # full capsule
_GAP_BELOW_BAR = 4.0  # space between the menu bar and the pill


def _dot_color(state: str):
    from AppKit import NSColor

    return {
        "listening": NSColor.systemRedColor,
        "recognizing": NSColor.systemOrangeColor,
        "buffering": NSColor.systemBlueColor,
        "error": NSColor.systemRedColor,
    }.get(state, NSColor.systemGrayColor)()


class StatusHUD:
    def __init__(self):
        self._panel = None
        self._label = None
        self._dot = None
        self._effect = None
        self._visible = False

    def _build(self) -> None:
        from AppKit import NSColor, NSFont, NSPanel, NSTextField, NSView, NSVisualEffectView

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (_MIN_W, _H)), _NONACTIVATING_PANEL, _BACKING_BUFFERED, False
        )
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setLevel_(_STATUS_WINDOW_LEVEL)
        panel.setIgnoresMouseEvents_(True)
        panel.setHidesOnDeactivate_(False)
        with contextlib.suppress(Exception):
            panel.setCollectionBehavior_(_COLLECTION_ALL_SPACES)

        # Native translucent material — tints itself for the active appearance.
        effect = NSVisualEffectView.alloc().initWithFrame_(((0.0, 0.0), (_MIN_W, _H)))
        effect.setMaterial_(_MATERIAL_HUD)
        effect.setBlendingMode_(_BLEND_BEHIND_WINDOW)
        effect.setState_(_STATE_ACTIVE)
        effect.setWantsLayer_(True)
        if effect.layer() is not None:
            effect.layer().setCornerRadius_(_RADIUS)
            effect.layer().setMasksToBounds_(True)

        # State dot — a small filled circle, colour set per state in show().
        dot = NSView.alloc().initWithFrame_(((_PAD_L, (_H - _DOT) / 2.0), (_DOT, _DOT)))
        dot.setWantsLayer_(True)
        if dot.layer() is not None:
            dot.layer().setCornerRadius_(_DOT / 2.0)
        effect.addSubview_(dot)

        label = NSTextField.alloc().initWithFrame_(
            ((_PAD_L + _DOT + _GAP, (_H - 18.0) / 2.0), (_MIN_W, 18.0))
        )
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        label.setTextColor_(NSColor.labelColor())  # adapts to the appearance
        label.setFont_(NSFont.systemFontOfSize_(_FONT_SZ))
        with contextlib.suppress(Exception):
            label.cell().setLineBreakMode_(5)  # NSLineBreakByTruncatingTail
        label.setStringValue_("")
        effect.addSubview_(label)

        panel.setContentView_(effect)
        self._panel, self._effect, self._label, self._dot = panel, effect, label, dot

    def _apply_theme(self, theme: str) -> None:
        if self._panel is None:
            return
        with contextlib.suppress(Exception):
            from AppKit import NSAppearance

            name = _APPEARANCE_NAMES.get(theme)
            self._panel.setAppearance_(NSAppearance.appearanceNamed_(name) if name else None)

    def _layout(self, text: str) -> float:
        """Resize the label to its text, return the pill width (clamped)."""
        self._label.setStringValue_(text)
        self._label.sizeToFit()
        tw = self._label.frame().size.width
        width = _PAD_L + _DOT + _GAP + tw + _PAD_R
        width = max(_MIN_W, min(width, _MAX_W))
        avail = width - (_PAD_L + _DOT + _GAP) - _PAD_R
        self._label.setFrame_(((_PAD_L + _DOT + _GAP, (_H - 18.0) / 2.0), (avail, 18.0)))
        return width

    def _reposition(self, width: float, anchor) -> None:
        from AppKit import NSScreen

        screen = NSScreen.mainScreen()
        if screen is None or self._panel is None:
            return
        sf = screen.frame()
        if anchor:  # (x, y, w, h) of the status-bar button, screen coords
            ax, ay, aw, _ah = anchor
            x = ax + aw / 2.0 - width / 2.0  # centred under the icon
            y = ay - _GAP_BELOW_BAR - _H  # just below the menu bar
        else:  # fall back to the top-right corner
            x = sf.origin.x + sf.size.width - width - 12.0
            y = sf.origin.y + sf.size.height - _H - 28.0
        # Keep it fully on screen.
        x = max(sf.origin.x + 6.0, min(x, sf.origin.x + sf.size.width - width - 6.0))
        self._panel.setFrame_display_(((x, y), (width, _H)), True)

    def _spring_in(self) -> None:
        """Pop the pill out: a quick scale-up spring plus a fade."""
        from AppKit import NSAnimationContext

        self._panel.setAlphaValue_(0.0)
        self._panel.orderFrontRegardless()
        with contextlib.suppress(Exception):
            from Quartz import CASpringAnimation

            layer = self._effect.layer()
            if layer is not None:
                spring = CASpringAnimation.animationWithKeyPath_("transform.scale")
                spring.setFromValue_(0.85)
                spring.setToValue_(1.0)
                spring.setStiffness_(320.0)
                spring.setDamping_(22.0)
                spring.setMass_(1.0)
                spring.setDuration_(spring.settlingDuration())
                layer.addAnimation_forKey_(spring, "emerge")
        NSAnimationContext.beginGrouping()
        NSAnimationContext.currentContext().setDuration_(0.18)
        self._panel.animator().setAlphaValue_(1.0)
        NSAnimationContext.endGrouping()

    def show(self, text: str, theme: str = "auto", state: str = "listening", anchor=None) -> None:
        with contextlib.suppress(Exception):
            if self._panel is None:
                self._build()
            self._apply_theme(theme)
            with contextlib.suppress(Exception):
                self._dot.layer().setBackgroundColor_(_dot_color(state).CGColor())
            width = self._layout(text)
            self._reposition(width, anchor)
            if not self._visible:
                self._spring_in()
                self._visible = True
            else:
                self._panel.orderFrontRegardless()

    def hide(self) -> None:
        with contextlib.suppress(Exception):
            if self._panel is not None:
                self._panel.orderOut_(None)
            self._visible = False
