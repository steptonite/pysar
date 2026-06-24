"""macOS backend implementation: Quartz + rumps.

- HotkeyListener: CGEventTap dispatching user-assignable hotkey bindings
- Paster:         pbcopy + Cmd+V simulation via CGEvent, restores previous clipboard
- Tray:           rumps.App with «🌍 Languages» submenu
"""

import contextlib
import subprocess
import threading
import time
from collections.abc import Callable

import Quartz
import rumps
from PyObjCTools import AppHelper

from ..config import (
    CLIPBOARD_RESTORE_DELAY,
    DEFAULT_HOTKEY,
    DEFAULT_LANG_HOTKEYS,
    MAX_PROFILE_SETS,
    MODE_LABELS,
    MODE_SHORTCUTS,
    MODES,
    MODIFIER_KEYCODES,
    binding_label,
    set_hotkey_label,
)
from ..i18n import strings, t
from ..profiles import PROMPT_TOKEN_BUDGET, active_set_index, budget_usage, meta_prompt


def set_login_item(enable: bool) -> bool:
    """Register/unregister this .app as a macOS login item via SMAppService
    (macOS 13+). Explicit opt-in only, fully reversible. Returns True on success.

    Works only when running as the installed .app bundle (mainAppService points
    at the bundle); from a `make up` terminal run it returns False, and the
    caller surfaces a "add it manually" hint."""
    try:
        from ServiceManagement import SMAppService

        svc = SMAppService.mainAppService()
        if enable:
            ok, err = svc.registerAndReturnError_(None)
        else:
            ok, err = svc.unregisterAndReturnError_(None)
        return bool(ok) and err is None
    except Exception as e:
        print(f"⚠️ login item: {e}")
        return False


def login_item_enabled() -> bool | None:
    """Real registration status from SMAppService, or None if it can't be read
    (not running as the .app, framework missing). Used to sync the menu/toggle
    to the *actual* OS state on launch instead of trusting our settings file —
    otherwise the checkmark drifts (resets) whenever registration silently
    failed or the user toggled it in System Settings."""
    try:
        from ServiceManagement import (
            SMAppService,
            SMAppServiceStatusEnabled,
        )

        return SMAppService.mainAppService().status() == SMAppServiceStatusEnabled
    except Exception:
        return None


def _set_app_name(name: str) -> None:
    """Override the Dock/menu name of the running process. Must run before the
    NSApplication main menu is built (AppKit caches the name then). Framework
    python registers as org.python.python, so this is how it stops showing as
    "Python". Cosmetic — any failure is swallowed."""
    try:
        from Foundation import NSBundle, NSProcessInfo

        NSProcessInfo.processInfo().setProcessName_(name)
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        if info is not None:
            info["CFBundleName"] = name
            info["CFBundleDisplayName"] = name
    except Exception as e:
        print(f"⚠️ could not set app name: {e}")


# ── Hotkey ───────────────────────────────────────────────────────────────────
# Modifier name → CGEvent flag bit. ("option" is AppKit's "Alternate".)
def _mod_masks() -> dict[str, int]:
    return {
        "control": Quartz.kCGEventFlagMaskControl,
        "option": Quartz.kCGEventFlagMaskAlternate,
        "command": Quartz.kCGEventFlagMaskCommand,
        "shift": Quartz.kCGEventFlagMaskShift,
    }


class HotkeyListener:
    """Global hotkey dispatch via a listen-only CGEventTap.

    Every hotkey is a binding {"keycode", "mods"} with an action ("__toggle__"
    for dictation, or a language code). Each binding is detected one of three
    ways, decided by its shape:
      • bare Caps Lock — its LED flag (AlphaShift). Toggles on every state change
        (1st tap → start, 2nd → stop). Shift-held events are skipped (macOS
        briefly clears AlphaShift on shift+caps → would mis-toggle).
      • bare modifier  — a right/left ⌘⌥⌃⇧ key. Fires only on a *clean tap*
        (pressed and released alone); used in a combo (⌘+arrow, ⌘+C) it's
        ignored, so the modifier still works normally elsewhere.
      • everything else — a key-down whose modifier set matches exactly. Covers
        ⌃⌥-letter combos and bare F-keys. OS auto-repeat is ignored.

    Bindings can be replaced live (set_bindings) — no relaunch — and the next
    keypress can be captured for the Settings UI (begin_capture).
    """

    def __init__(
        self,
        hotkey: dict | None = None,
        lang_hotkeys: list[dict] | None = None,
        set_hotkeys: list[dict] | None = None,
    ):
        self._on_toggle: Callable[[], None] | None = None
        self._on_mode: Callable[[str], None] | None = None
        self._on_set: Callable[[int], None] | None = None
        self._capture: Callable[[dict], None] | None = None
        self._cap_pending: int | None = None  # a bare modifier seen down, awaiting up
        self._flag_bindings: list[dict] = []  # bare caps/modifier bindings
        self._key_bindings: list[dict] = []  # key-down bindings (combos, F-keys)
        self.set_bindings(
            hotkey or dict(DEFAULT_HOTKEY), lang_hotkeys or DEFAULT_LANG_HOTKEYS, set_hotkeys
        )

    # ── Binding table ─────────────────────────────────────────────────────────
    def set_bindings(
        self, hotkey: dict, lang_hotkeys: list[dict], set_hotkeys: list[dict] | None = None
    ) -> None:
        """(Re)build the binding tables from settings. Safe to call live.
        Profile-set bindings carry a "set:<index>" action (⌃⌥<digit> combos)."""
        masks = _mod_masks()
        flag_b, key_b = [], []
        raw = (
            [{"action": "__toggle__", **hotkey}]
            + [
                {"action": h["action"], "keycode": h["keycode"], "mods": h.get("mods", [])}
                for h in lang_hotkeys
            ]
            + [
                {"action": h["action"], "keycode": h["keycode"], "mods": h.get("mods", [])}
                for h in (set_hotkeys or [])
            ]
        )
        for b in raw:
            kc, mods = b["keycode"], list(b.get("mods") or [])
            if kc is None:  # unassigned language slot — no binding
                continue
            if not mods and kc == 57:  # bare Caps Lock
                flag_b.append({"action": b["action"], "keycode": kc, "kind": "caps", "down": False})
            elif not mods and kc in MODIFIER_KEYCODES:  # bare modifier (tap-only)
                flag_b.append(
                    {
                        "action": b["action"],
                        "keycode": kc,
                        "kind": "mod",
                        "mask": masks[MODIFIER_KEYCODES[kc]],
                        "down": False,
                        "armed": False,  # set on a clean press, cleared by any combo
                    }
                )
            else:  # key-down: combo or bare F-key
                key_b.append({"action": b["action"], "keycode": kc, "mods": set(mods)})
        self._flag_bindings, self._key_bindings = flag_b, key_b
        # Mask of every modifier bit, to tell "this modifier alone" from a combo.
        self._all_mod_mask = 0
        for m in masks.values():
            self._all_mod_mask |= m

    def begin_capture(self, on_captured: Callable[[dict], None]) -> None:
        """Capture the next keypress as a binding and hand {keycode,mods} to
        `on_captured` (invoked on the main thread). Used by the Settings UI."""
        self._cap_pending = None
        self._capture = lambda binding: AppHelper.callAfter(on_captured, binding)

    def start(
        self,
        on_toggle: Callable[[], None],
        on_mode: Callable[[str], None] | None = None,
        on_set: Callable[[int], None] | None = None,
    ) -> None:
        self._on_toggle = on_toggle
        self._on_mode = on_mode
        self._on_set = on_set

        event_mask = (1 << Quartz.kCGEventFlagsChanged) | (1 << Quartz.kCGEventKeyDown)
        tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionListenOnly,
            event_mask,
            self._callback,
            None,
        )
        if tap is None:
            raise RuntimeError(
                "Failed to create CGEventTap.\n"
                "System Settings → Privacy & Security → Input Monitoring\n"
                "Add Terminal (or iTerm) and toggle the switch on."
            )

        source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
        Quartz.CFRunLoopAddSource(
            Quartz.CFRunLoopGetCurrent(), source, Quartz.kCFRunLoopDefaultMode
        )
        Quartz.CGEventTapEnable(tap, True)
        print("✅ Hotkey listener started (hotkeys are configurable in Settings)")
        Quartz.CFRunLoopRun()

    # ── Event handling ────────────────────────────────────────────────────────
    @staticmethod
    def _mods_from_flags(flags: int) -> list[str]:
        return [name for name, mask in _mod_masks().items() if flags & mask]

    def _fire(self, action: str) -> None:
        if action == "__toggle__":
            if self._on_toggle:
                self._on_toggle()
        elif action.startswith("set:"):
            if self._on_set:
                self._on_set(int(action[4:]))
        elif self._on_mode:
            self._on_mode(action)

    def _callback(self, proxy, event_type, event, refcon):
        try:
            keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
            flags = Quartz.CGEventGetFlags(event)

            if self._capture is not None:
                self._handle_capture(event_type, keycode, flags)
                return event

            if event_type == Quartz.kCGEventFlagsChanged:
                shift_down = bool(flags & Quartz.kCGEventFlagMaskShift)
                # Any modifier event for a *different* key disarms a pending tap —
                # it means a second modifier joined, i.e. a combo is forming.
                for b in self._flag_bindings:
                    if b["kind"] == "mod" and b["down"] and b["keycode"] != keycode:
                        b["armed"] = False
                for b in self._flag_bindings:
                    if b["keycode"] != keycode:
                        continue
                    if b["kind"] == "caps":
                        down = bool(flags & Quartz.kCGEventFlagMaskAlphaShift)
                        if not shift_down and down != b["down"]:
                            self._fire(b["action"])
                        b["down"] = down
                    else:  # bare modifier — fire only on a clean tap (down then up,
                        # with no other key/modifier in between).
                        down = bool(flags & b["mask"])
                        if down and not b["down"]:
                            # Clean only if no *other* modifier is held right now.
                            other = flags & self._all_mod_mask & ~b["mask"]
                            b["armed"] = other == 0
                        elif not down and b["down"]:
                            if b["armed"]:
                                self._fire(b["action"])
                            b["armed"] = False
                        b["down"] = down

            elif event_type == Quartz.kCGEventKeyDown:
                repeat = Quartz.CGEventGetIntegerValueField(
                    event, Quartz.kCGKeyboardEventAutorepeat
                )
                if repeat:
                    return event
                # A real key press means any held modifier is being used in a combo,
                # not tapped — disarm every pending bare-modifier tap.
                for b in self._flag_bindings:
                    if b["kind"] == "mod":
                        b["armed"] = False
                mods = set(self._mods_from_flags(flags))
                for b in self._key_bindings:
                    if b["keycode"] == keycode and b["mods"] == mods:
                        self._fire(b["action"])
                        break
        except Exception as e:
            print(f"⚠️ hotkey callback: {e}")
        return event

    def _handle_capture(self, event_type, keycode, flags) -> None:
        """While capturing, resolve the user's next keypress into a binding.
        A key-down wins immediately (key + held modifiers); a bare modifier or
        Caps Lock is captured on its release (press → release with nothing in
        between), so the modifiers of a combo aren't mistaken for the binding."""
        if event_type == Quartz.kCGEventKeyDown:
            self._finish_capture(keycode, self._mods_from_flags(flags))
        elif event_type == Quartz.kCGEventFlagsChanged:
            if keycode == 57:  # Caps Lock — distinctive, capture at once
                self._finish_capture(57, [])
            elif keycode in MODIFIER_KEYCODES:
                mask = _mod_masks()[MODIFIER_KEYCODES[keycode]]
                if flags & mask:  # pressed down → remember, wait for release
                    self._cap_pending = keycode
                elif self._cap_pending == keycode:  # released bare → capture it
                    self._finish_capture(keycode, [])

    def _finish_capture(self, keycode: int, mods: list[str]) -> None:
        cb, self._capture, self._cap_pending = self._capture, None, None
        if cb:
            cb({"keycode": keycode, "mods": mods})


# ── Paste ────────────────────────────────────────────────────────────────────
_KEYCODE_V = 9  # virtual keycode for 'v', layout-independent
_KEYCODE_CMD = 0x37  # kVK_Command (left ⌘)


def _utf16_slices(text: str, size: int):
    """Yield `text` in slices of at most `size` code points. Cutting on a code
    point (never inside a Python str index) keeps surrogate pairs intact."""
    for i in range(0, len(text), size):
        yield text[i : i + size]


class Paster:
    """Delivers dictated text where it was started.

    Two strategies, in order:
      1. Accessibility (AX): write straight into the text element that was
         focused when recording began. This is focus-independent — the text
         lands in the right field even if the user has since switched to
         Spotlight or another window — and never touches the clipboard.
      2. Clipboard + Cmd+V fallback: only when AX can't insert (the app exposes
         no settable text element — common in Electron/terminals). Refocuses the
         target and verifies it before the keystroke; if focus can't be returned,
         the text is LEFT on the clipboard and the caller notifies the user.
    """

    @staticmethod
    def capture_target() -> dict:
        """Snapshot, at record start: the frontmost app *and* the focused AX text
        element. Returned as a dict for paste_text. Cheap and never raises."""
        from ..logsetup import log

        app = None
        with contextlib.suppress(Exception):
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
        ax = Paster._capture_focused_ax(app)
        name = app.localizedName() if app is not None else "?"
        log(f"capture_target: app={name} ax={'yes' if ax is not None else 'none'}")
        return {"app": app, "ax": ax, "name": name}

    @staticmethod
    def _capture_focused_ax(app):
        """The focused UI element *inside `app`* (a text field/area when the caret
        is in one), or None. Queries the app element by PID rather than the
        system-wide element — the latter returns NoValue for Chromium/Electron
        apps (Claude). For Electron we also poke AXManualAccessibility, which
        wakes Chromium's accessibility tree (it sleeps until an AT asks)."""
        from ..logsetup import log

        if app is None:
            return None
        try:
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementCreateApplication,
                AXUIElementSetAttributeValue,
                kAXFocusedUIElementAttribute,
            )

            app_el = AXUIElementCreateApplication(app.processIdentifier())
            # Wake Chromium's a11y tree (no-op / harmless on native apps).
            with contextlib.suppress(Exception):
                AXUIElementSetAttributeValue(app_el, "AXManualAccessibility", True)
            err, focused = AXUIElementCopyAttributeValue(app_el, kAXFocusedUIElementAttribute, None)
            if err == 0 and focused is not None:
                return focused
            log(f"_capture_focused_ax: no element (err={err})")
        except Exception as e:
            log(f"_capture_focused_ax: {e}")
        return None

    @staticmethod
    def _ax_insert(element, text: str) -> bool:
        """Insert `text` at the caret of `element` by setting its selected text
        (replaces any selection, like a real paste). Returns True only when the
        write is *verified* by reading the field value back — never just because
        the API returned 0. Only AXSelectedText is used to write — never AXValue,
        which would clobber the whole field. Unverified write → caller falls back
        to Cmd+V.

        Why verify: Electron/Chromium (Claude) returns err=0 from
        AXUIElementSetAttributeValue even when the text lands nowhere — the node
        we captured at record-start is stale/detached by paste time. A blind
        "OK" then lies to the user (status says ✓ → Claude, field stays empty).

        Logs the element role and whether the field reports AXSelectedText/AXValue
        as settable, so we can tell from the log whether AX is viable per app."""
        from ..logsetup import log

        try:
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementIsAttributeSettable,
                AXUIElementSetAttributeValue,
                kAXRoleAttribute,
                kAXSelectedTextAttribute,
                kAXValueAttribute,
            )

            _, role = AXUIElementCopyAttributeValue(element, kAXRoleAttribute, None)
            _, sel_settable = AXUIElementIsAttributeSettable(
                element, kAXSelectedTextAttribute, None
            )
            _, val_settable = AXUIElementIsAttributeSettable(element, kAXValueAttribute, None)
            log(
                f"_ax_insert: role={role} selText_settable={sel_settable} value_settable={val_settable}"
            )

            def _value() -> str | None:
                err, v = AXUIElementCopyAttributeValue(element, kAXValueAttribute, None)
                return v if err == 0 and isinstance(v, str) else None

            before = _value()

            # Chromium/Electron ignores AXSelectedText when it has no concrete
            # insertion point (the field isn't first-responder while we're in the
            # background). Seed the caret at end-of-value first via a zero-length
            # AXSelectedTextRange — that's what makes the subsequent write land.
            with contextlib.suppress(Exception):
                from ApplicationServices import (
                    AXValueCreate,
                    kAXSelectedTextRangeAttribute,
                    kAXValueCFRangeType,
                )

                end = len(before) if before is not None else 0
                rng = AXValueCreate(kAXValueCFRangeType, (end, 0))
                if rng is not None:
                    AXUIElementSetAttributeValue(element, kAXSelectedTextRangeAttribute, rng)

            err = AXUIElementSetAttributeValue(element, kAXSelectedTextAttribute, text)
            if err != 0:
                log(f"_ax_insert: AXSelectedText set failed err={err}")
                return False

            # err==0 is not enough — confirm the value actually changed. We can't
            # read it back → can't trust the write (Cmd+V is safer). Poll, since
            # the a11y tree updates async.
            if before is None:
                log("_ax_insert: value unreadable → can't verify, fall back to Cmd+V")
                return False
            needle = text.strip()[:24]
            for _ in range(15):  # ~0.3 s
                after = _value()
                if after is not None and after != before and (not needle or needle in after):
                    log("_ax_insert: OK via AXSelectedText (verified)")
                    return True
                time.sleep(0.02)
            log("_ax_insert: set returned 0 but value unchanged → fall back to Cmd+V")
        except Exception as e:
            log(f"_ax_insert: {e}")
        return False

    @staticmethod
    def _frontmost_pid():
        """PID of the app frontmost right now, or None. Used to verify focus
        actually returned to the dictation target before we fire Cmd+V."""
        try:
            from AppKit import NSWorkspace

            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return app.processIdentifier() if app is not None else None
        except Exception:
            return None

    @staticmethod
    def _overlay_capturing_keys() -> str | None:
        """Name of a system overlay (Spotlight, Siri) showing an on-screen
        window right now, or None. Such overlays float *over* the frontmost
        app: NSWorkspace.frontmostApplication() still reports the app behind
        them (Claude), so a frontmost-pid check passes — yet the overlay eats
        the keyboard and Cmd+V lands in *it*, not the field. Detect via the
        window list and refuse to paste rather than dump text into Spotlight."""
        try:
            from Quartz import (
                CGWindowListCopyWindowInfo,
                kCGNullWindowID,
                kCGWindowListOptionOnScreenOnly,
            )

            overlays = {"Spotlight", "Siri", "SiriNCService"}
            info = (
                CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID) or []
            )
            for w in info:
                owner = w.get("kCGWindowOwnerName")
                layer = w.get("kCGWindowLayer", 0)
                # overlays sit above normal windows (layer 0); a visible
                # Spotlight panel is a real on-screen window at a high layer.
                if owner in overlays and layer and layer > 0:
                    return owner
        except Exception:
            pass
        return None

    def _refocus_target(self, target) -> bool:
        """Bring `target` back to the front and confirm it actually became
        frontmost. macOS 14+ often *silently ignores* programmatic activation of
        a non-frontmost app, so we must verify rather than assume — otherwise a
        Cmd+V lands in whatever stole focus (Spotlight, another window).

        Returns True only when `target` is verified frontmost. None target means
        "paste wherever we are" (no specific window to honor) → treated as ok."""
        if target is None:
            return True
        want = target.processIdentifier()
        try:
            target.activateWithOptions_(2)  # NSApplicationActivateIgnoringOtherApps
        except Exception as e:
            print(f"⚠️ could not refocus paste target: {e}")
        # Poll until the target is really frontmost (activation is async and can
        # lag under memory pressure), up to ~0.6 s, then give up.
        for _ in range(30):
            if self._frontmost_pid() == want:
                return True
            time.sleep(0.02)
        return False

    def paste_text(self, text: str, target=None) -> bool:
        """Deliver `text` to `target`. Returns True if it landed in the field
        (via AX or Cmd+V), False if it could only be left on the clipboard for a
        manual ⌘V (focus could not be returned and AX was unavailable). The
        caller surfaces that to the user with a visible notification."""
        from ..logsetup import log

        # `target` is the dict from capture_target; tolerate a bare app / None
        # (older callers, tests) so the AX path is simply skipped.
        if isinstance(target, dict):
            app, ax, name = target.get("app"), target.get("ax"), target.get("name", "?")
        else:
            app, ax, name = target, None, "?"
        log(f"paste_text: target={name} ax={'yes' if ax is not None else 'none'} len={len(text)}")

        # 1) AX first: write straight into the focused field. Focus-independent,
        # no clipboard, lands exactly where dictation began. Best case.
        # Re-resolve the focused element *now* rather than trusting the handle
        # captured at record-start — by paste time (seconds later) a Chromium/
        # Electron node is often stale/detached and silently rejects the write.
        # An app-level AXFocusedUIElement query is per-app, so it still returns
        # Claude's textarea even while we're sitting in Spotlight.
        if ax is not None:
            fresh = self._capture_focused_ax(app) if app is not None else None
            if self._ax_insert(fresh if fresh is not None else ax, text):
                return True

        # 2) Clipboard + Cmd+V fallback (app has no settable AX text element).
        payload = text.encode("utf-8")
        saved = self._read_clipboard()
        self._write_clipboard(payload)

        # Restore focus to the app we were dictating into before pasting. A long
        # transcription gives the user time to click away. If we can't verify the
        # target is frontmost again, we DON'T paste — the text stays on the
        # clipboard so the user can ⌘V it into the right place themselves.
        if not self._refocus_target(app):
            log("paste_text: focus not returned and AX unavailable → left on clipboard")
            return False  # leave `payload` on the clipboard; do NOT restore `saved`

        # Even with the target frontmost, a Spotlight/Siri overlay may be
        # floating above it, invisibly grabbing the keyboard. Cmd+V would land
        # there, not in the field — so refuse and leave the text on the
        # clipboard for the user to ⌘V once they dismiss the overlay.
        overlay = self._overlay_capturing_keys()
        if overlay is not None:
            log(f"paste_text: {overlay} overlay grabbing keys → left on clipboard")
            return False  # leave `payload` on the clipboard; do NOT restore `saved`
        log("paste_text: pasting via Cmd+V")

        # Don't paste until our text is actually on the clipboard. Under memory
        # pressure (8 GB + Resolve/PS) the pbcopy write lags, and pressing Cmd+V
        # early grabs stale content — the old clipboard or a previous dictation.
        # Poll instead of a fixed sleep so it's correct whether fast or slow.
        for _ in range(50):  # up to ~1 s
            if self._read_clipboard() == payload:
                break
            time.sleep(0.02)

        self._press_cmd_v()

        # Restore the previous clipboard only after the front app has had time to
        # read the paste; restoring too early swaps the old text back in before
        # it's consumed (the other half of the same race). Do it off the hot path
        # in a background thread so dictation isn't blocked by the restore delay —
        # the caller can accept the next Caps-Lock tap immediately.
        def _restore() -> None:
            time.sleep(CLIPBOARD_RESTORE_DELAY)
            try:
                self._write_clipboard(saved)
            except Exception as e:
                print(f"⚠️ failed to restore clipboard: {e}")

        threading.Thread(target=_restore, daemon=True).start()
        return True

    @staticmethod
    def _read_clipboard() -> bytes:
        return subprocess.run(["pbpaste"], capture_output=True).stdout

    @staticmethod
    def _write_clipboard(data: bytes) -> None:
        subprocess.run(["pbcopy"], input=data, check=True)

    @staticmethod
    def _press_cmd_v() -> None:
        # Press ⌘ as a *real key* (down → V down → V up → ⌘ up) rather than just
        # tagging the V events with a Command flag. Flag-only synthesis leaves no
        # explicit "Command released" signal, so the target app keeps Command
        # logically held after the paste — beeps on every key, tabs need a
        # double-click, and Space becomes ⌘Space (opens Spotlight, so the next
        # dictation lands there). The matching ⌘-up event below guarantees the
        # modifier is cleared. Private source state keeps it isolated from the
        # physical keyboard.
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        cmd = Quartz.kCGEventFlagMaskCommand

        cmd_down = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_CMD, True)
        Quartz.CGEventSetFlags(cmd_down, cmd)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cmd_down)
        time.sleep(0.005)

        down = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_V, True)
        Quartz.CGEventSetFlags(down, cmd)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
        time.sleep(0.01)

        up = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_V, False)
        Quartz.CGEventSetFlags(up, cmd)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
        time.sleep(0.005)

        # Release Command explicitly with cleared flags — this is the signal the
        # earlier flag-only version was missing, and what kept Command "stuck".
        cmd_up = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_CMD, False)
        Quartz.CGEventSetFlags(cmd_up, 0)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, cmd_up)

    @staticmethod
    def type_text(text: str) -> None:
        """Type `text` straight into the focused field as synthetic Unicode key
        events — no clipboard, no Command modifier. Used by streaming dictation to
        insert each sentence as it's transcribed, so nothing clobbers the user's
        clipboard mid-dictation.

        Goes wherever the keyboard focus is (same as the user typing). A *private*
        event source keeps it isolated from the physical keyboard (the same
        isolation lesson as the Cmd+V fix). Long strings are posted in small
        UTF-16 slices — CGEventKeyboardSetUnicodeString truncates an over-long
        buffer, so we keep each event short."""
        if not text:
            return
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
        for chunk in _utf16_slices(text, 16):
            units = len(chunk.encode("utf-16-le")) // 2  # surrogate-pair safe length
            down = Quartz.CGEventCreateKeyboardEvent(src, 0, True)
            Quartz.CGEventKeyboardSetUnicodeString(down, units, chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)
            up = Quartz.CGEventCreateKeyboardEvent(src, 0, False)
            Quartz.CGEventKeyboardSetUnicodeString(up, units, chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)
            time.sleep(0.002)

    @staticmethod
    def has_editable_focus(target=None) -> bool:
        """Best-effort: can we safely type a streaming sentence into the keyboard
        focus *right now*? Streaming types blind via CGEvent — wherever focus is —
        so before each sentence we check there's a real text field to land in. No
        field → the caller buffers the sentence on the clipboard instead.

        Biased so the normal path is never silently swallowed:
          • A Spotlight/Siri overlay grabbing the keyboard → False (typing would
            land in it — exactly the bug we're fixing).
          • Still in the app dictation started in (same PID as `target`) → True,
            unconditionally. The field is there; this protects Claude/Electron,
            whose AX roles we can't always read, on the main path.
          • Switched elsewhere → True only if the new frontmost app's focused
            element is an editable text control (a web page / Finder / the desktop
            reads as non-editable → buffer).
        """
        from ..logsetup import log

        if Paster._overlay_capturing_keys() is not None:
            return False
        front = None
        with contextlib.suppress(Exception):
            from AppKit import NSWorkspace

            front = NSWorkspace.sharedWorkspace().frontmostApplication()
        if front is None:
            return False
        # Same app we started in → trust it (don't depend on flaky Electron roles).
        start_app = target.get("app") if isinstance(target, dict) else None
        if start_app is not None:
            with contextlib.suppress(Exception):
                if front.processIdentifier() == start_app.processIdentifier():
                    return True
        el = Paster._capture_focused_ax(front)
        if el is None:
            log("has_editable_focus: no focused element → no field")
            return False
        return Paster._is_editable(el)

    @staticmethod
    def _is_editable(element) -> bool:
        """True when `element` is a text-entry control: a known editable role, or
        it reports AXValue/AXSelectedText as settable. Web areas and other
        containers read as non-editable. On any read error, assume editable so the
        live-typing path is never wrongly blocked."""
        from ..logsetup import log

        try:
            from ApplicationServices import (
                AXUIElementCopyAttributeValue,
                AXUIElementIsAttributeSettable,
                kAXRoleAttribute,
                kAXSelectedTextAttribute,
                kAXValueAttribute,
            )

            _, role = AXUIElementCopyAttributeValue(element, kAXRoleAttribute, None)
            if role in _EDITABLE_ROLES:
                return True
            _, sel = AXUIElementIsAttributeSettable(element, kAXSelectedTextAttribute, None)
            _, val = AXUIElementIsAttributeSettable(element, kAXValueAttribute, None)
            log(f"_is_editable: role={role} selText_settable={sel} value_settable={val}")
            return bool(sel) or bool(val)
        except Exception as e:
            log(f"_is_editable: {e}")
            return True

    def set_clipboard(self, text: str) -> None:
        """Put `text` on the system clipboard. Used by streaming buffer mode when
        there's no field to type into — the user pastes it manually with ⌘V."""
        self._write_clipboard(text.encode("utf-8"))


# Text-entry AX roles that streaming may type into (see Paster._is_editable).
_EDITABLE_ROLES = {"AXTextField", "AXTextArea", "AXComboBox", "AXSearchField"}


# ── Tray ─────────────────────────────────────────────────────────────────────
class Tray:
    """Menu-bar tray via rumps. All modes live under the «🌍 Languages» submenu."""

    def __init__(
        self,
        modes: list[tuple[str, str]],
        current_mode: str,
        on_mode_select: Callable[[str], None],
        save_recordings: bool = False,
        keep_last: int = 10,
        keep_last_options: tuple[int, ...] = (5, 10, 20),
        on_toggle_save: Callable[[bool], None] | None = None,
        on_set_keep_last: Callable[[int], None] | None = None,
        recordings_dir: str | None = None,
        profiles: list[dict] | None = None,
        active_profiles: dict[str, list[str]] | None = None,
        on_toggle_profile: Callable[[str, bool], None] | None = None,
        on_import_profiles: Callable[[str], tuple] | None = None,
        on_save_profile: Callable[[str, str, str, str | None], tuple] | None = None,
        on_delete_profile: Callable[[str], list] | None = None,
        mics: list[str] | None = None,
        current_mic: str | None = None,
        on_select_mic: Callable[[str | None], None] | None = None,
        launch_at_login: bool = False,
        on_toggle_login: Callable[[bool], None] | None = None,
        ui_theme: str = "auto",
        on_set_theme: Callable[[str], None] | None = None,
        ui_lang: str = "uk",
        on_set_lang: Callable[[str], None] | None = None,
        dictation_mode: str = "batch",
        on_set_dictation_mode: Callable[[str], None] | None = None,
        hotkey: dict | None = None,
        lang_hotkeys: list[dict] | None = None,
        on_capture_hotkey: Callable[[str], None] | None = None,
        on_clear_hotkey: Callable[[str], None] | None = None,
        profile_sets: list[dict] | None = None,
        on_save_set: Callable[[int | None, str, list], tuple] | None = None,
        on_delete_set: Callable[[int], list] | None = None,
        on_activate_set: Callable[[int], None] | None = None,
    ):
        # Name the app *before* rumps builds NSApplication below — AppKit reads
        # the bundle/process name once, when the main menu is first created, so a
        # later override (e.g. in run()) is ignored. "Custom" makes clear this is
        # our fork of the upstream Cream Typer, not the original.
        _set_app_name("Pysar")

        self._current = current_mode
        self._on_mode_select = on_mode_select
        self._on_toggle_save = on_toggle_save
        self._on_set_keep_last = on_set_keep_last
        self._recordings_dir = recordings_dir
        self._profiles = profiles or []
        # active_profiles: {lang: [names]} — one toggled-on group per language.
        self._active_by_lang = {lng: set(v) for lng, v in (active_profiles or {}).items()}
        self._on_toggle_profile = on_toggle_profile
        self._on_import_profiles = on_import_profiles
        self._on_save_profile = on_save_profile
        self._on_delete_profile = on_delete_profile
        self._current_mic = current_mic
        self._on_select_mic = on_select_mic
        self._on_toggle_login = on_toggle_login
        # Plain mirrors of the settings state — the Settings window reads these
        # via _settings_state() each time it opens, so it's never stale.
        self._save_recordings = save_recordings
        self._keep_last = keep_last
        self._keep_last_options = keep_last_options
        self._mics = mics or []
        self._launch_at_login = launch_at_login
        self._ui_theme = ui_theme
        self._on_set_theme = on_set_theme
        self._ui_lang = ui_lang
        self._on_set_lang = on_set_lang
        self._dictation_mode = dictation_mode
        self._on_set_dictation_mode = on_set_dictation_mode
        self._hotkey = hotkey or dict(DEFAULT_HOTKEY)
        self._lang_hotkeys = lang_hotkeys or [dict(h) for h in DEFAULT_LANG_HOTKEYS]
        self._on_capture_hotkey = on_capture_hotkey
        self._on_clear_hotkey = on_clear_hotkey
        self._profile_sets = profile_sets if profile_sets is not None else []
        self._on_save_set = on_save_set
        self._on_delete_set = on_delete_set
        self._on_activate_set = on_activate_set
        self._settings_window = None  # built lazily on first open
        self._hud = None  # streaming status overlay, built lazily on first show

        self._app = rumps.App("🎙", quit_button=self._t("tray.quit"))
        self._status = rumps.MenuItem(self._t("tray.ready"))
        self._hint = rumps.MenuItem(
            self._t(
                "tray.hotkey", label=binding_label(self._hotkey["keycode"], self._hotkey["mods"])
            )
        )

        self._mode_items: dict[str, rumps.MenuItem] = {}
        for code, label in modes:
            item = rumps.MenuItem(label, callback=self._make_callback(code))
            # Show the Ctrl+Option shortcut greyed on the right (native key-equivalent
            # rendering). It also works as a real shortcut while our window is key;
            # the global combo is handled by the event tap regardless.
            letter = MODE_SHORTCUTS.get(code)
            if letter:
                with contextlib.suppress(Exception):
                    from AppKit import NSEventModifierFlagControl, NSEventModifierFlagOption

                    mi = item._menuitem
                    mi.setKeyEquivalent_(letter.lower())
                    mi.setKeyEquivalentModifierMask_(
                        NSEventModifierFlagControl | NSEventModifierFlagOption
                    )
            self._mode_items[code] = item
        self._refresh_checkmarks()

        lang_submenu = rumps.MenuItem(self._t("tray.languages"))
        for code, _ in modes:
            lang_submenu.add(self._mode_items[code])

        self._profiles_submenu = rumps.MenuItem(self._t("tray.profiles"))
        self._populate_profiles_menu()

        settings_item = rumps.MenuItem(self._t("tray.settings"), callback=self._open_settings)

        self._app.menu = [
            self._status,
            self._hint,
            None,
            lang_submenu,
            self._profiles_submenu,
            settings_item,
            None,
        ]

    def _t(self, key: str, **kw) -> str:
        """Localized UI string in the current app language (see i18n.py)."""
        return t(self._ui_lang, key, **kw)

    # ── Settings window ───────────────────────────────────────────────────────
    def _open_settings(self, _sender) -> None:
        """Open the WKWebView settings panel (built lazily on first use)."""
        try:
            if self._settings_window is None:
                from .settings_window import SettingsWindow

                self._settings_window = SettingsWindow(
                    state_provider=self._settings_state,
                    handlers={
                        "set_mic": self._set_mic,
                        "set_keep": self._set_keep,
                        "set_save": self._set_save,
                        "set_login": self._set_login,
                        "open_folder": self._open_recordings_folder,
                        "toggle_profile": self._win_toggle_profile,
                        "save_profile": self._win_save_profile,
                        "delete_profile": self._win_delete_profile,
                        "import_profiles": self._win_import_profiles,
                        "copy_ai_prompt": self._win_copy_ai_prompt,
                        "set_theme": self._set_theme,
                        "set_lang": self._set_lang,
                        "set_dictation_mode": self._set_dictation_mode,
                        "capture_hotkey": self._capture_hotkey,
                        "clear_hotkey": self._clear_hotkey,
                        "save_set": self._win_save_set,
                        "delete_set": self._win_delete_set,
                        "activate_set": self._win_activate_set,
                    },
                )
            self._settings_window.show()
        except Exception as e:
            rumps.notification("Pysar", self._t("notif.cantOpenSettings"), str(e)[:120])

    def _settings_state(self) -> dict:
        """Fresh snapshot for the settings window each time it opens."""
        return {
            "mics": self._mics,
            "current_mic": self._current_mic,
            "save_recordings": self._save_recordings,
            "keep_last": self._keep_last,
            "keep_last_options": list(self._keep_last_options),
            "launch_at_login": self._launch_at_login,
            "ui_theme": self._ui_theme,
            "ui_lang": self._ui_lang,
            "dictation_mode": self._dictation_mode,
            "t": strings(self._ui_lang),
            "hotkey": dict(self._hotkey),
            "hotkey_label": binding_label(self._hotkey["keycode"], self._hotkey["mods"]),
            "lang_hotkeys": self._lang_hotkeys_state(),
            "recordings_dir": self._recordings_dir or "",
            # Profile editor: the full library + the toggled-on group per language,
            # plus the token budget so the meter can update live in the window.
            "profiles": [dict(p) for p in self._profiles],
            "active_profiles": {lng: sorted(v) for lng, v in self._active_by_lang.items()},
            "current_lang": self._lang(),
            "token_budget": PROMPT_TOKEN_BUDGET,
            # Profile sets: each with its fixed ⌃⌥<digit> shortcut label by index,
            # plus whether it's the currently-live selection (explicit "active"
            # indicator — cleared once the user hand-edits a toggle).
            "profile_sets": self._profile_sets_state(),
            "max_sets": MAX_PROFILE_SETS,
        }

    def _profile_sets_state(self) -> list[dict]:
        active = {lng: list(v) for lng, v in self._active_by_lang.items()}
        live = active_set_index(self._profile_sets, self._profiles, active)
        return [
            {
                "name": s["name"],
                "members": list(s.get("members", [])),
                "label": set_hotkey_label(i, s),
                "assigned": s.get("keycode") is not None,  # True = custom override
                "active": i == live,
            }
            for i, s in enumerate(self._profile_sets)
        ]

    # Settings handlers — invoked on the main thread from the JS bridge.
    def _set_mic(self, name: str | None) -> None:
        self._current_mic = name
        if self._on_select_mic:
            self._on_select_mic(name)

    def _set_keep(self, n: int) -> None:
        self._keep_last = int(n)
        if self._on_set_keep_last:
            self._on_set_keep_last(int(n))

    def _set_save(self, enabled: bool) -> None:
        self._save_recordings = bool(enabled)
        if self._on_toggle_save:
            self._on_toggle_save(bool(enabled))

    def _set_login(self, enabled: bool) -> None:
        ok = set_login_item(bool(enabled))
        self._launch_at_login = bool(enabled) and ok
        if self._on_toggle_login:
            self._on_toggle_login(self._launch_at_login)
        if enabled and not ok:
            rumps.notification(
                "Pysar",
                self._t("notif.cantLogin"),
                self._t("notif.cantLoginBody"),
            )

    def _set_dictation_mode(self, mode: str) -> None:
        self._dictation_mode = mode if mode in ("batch", "streaming") else "batch"
        if self._on_set_dictation_mode:
            self._on_set_dictation_mode(self._dictation_mode)

    def _set_theme(self, theme: str) -> None:
        self._ui_theme = theme if theme in ("auto", "light", "dark") else "auto"
        if self._on_set_theme:
            self._on_set_theme(self._ui_theme)
        if self._settings_window is not None:
            with contextlib.suppress(Exception):
                self._settings_window.apply_theme(self._ui_theme)

    def _open_recordings_folder(self) -> None:
        if self._recordings_dir:
            subprocess.run(["open", self._recordings_dir], check=False)

    # Profile editor handlers (from the Settings window's JS bridge) ───────────
    def _win_toggle_profile(self, value: dict) -> None:
        """A profile's on/off switch flipped in the window. Persist via the app
        callback and mirror it into the menu's checkmarks — no window reload, the
        page updates its own meter live."""
        name, active = value.get("name", ""), bool(value.get("active"))
        lang = next(
            (p.get("language", "uk") for p in self._profiles if p.get("name") == name), "uk"
        )
        group = self._active_by_lang.setdefault(lang, set())
        if active:
            group.add(name)
        else:
            group.discard(name)
        if self._on_toggle_profile:
            self._on_toggle_profile(name, active)
        AppHelper.callAfter(self._populate_profiles_menu, True)

    def _win_save_profile(self, value: dict) -> None:
        """Add or edit a profile. On a name clash/blank the app callback returns
        an error → notify and leave the window as-is; on success refresh both the
        menu and the window (so the list, meter and any rename are reflected)."""
        if not self._on_save_profile:
            return
        original = value.get("original") or None
        updated, err = self._on_save_profile(
            value.get("name", ""), value.get("language", "uk"), value.get("prompt", ""), original
        )
        if err:
            rumps.notification("Pysar", self._t("notif.cantSaveProfile"), err)
            return
        self._profiles = updated
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window(self._t("notice.saved"))

    def _win_delete_profile(self, name: str) -> None:
        if not self._on_delete_profile:
            return
        self._profiles = self._on_delete_profile(name)
        for group in self._active_by_lang.values():
            group.discard(name)
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window(self._t("notice.deleted", name=name))

    def _win_import_profiles(self, text: str) -> None:
        """JSON pasted into the window's import panel: merge, sync menu, report."""
        if not self._on_import_profiles:
            return
        updated, count, err = self._on_import_profiles(text)
        if err:
            self._refresh_settings_window(self._t("notice.importFail", err=err))
            return
        self._profiles = updated
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window(self._t("notice.imported", count=count))

    def _win_copy_ai_prompt(self, lang: str | None = None) -> None:
        # Copy the prompt in the picked language (sent with the action, so it works
        # even if the set_lang message hasn't been processed yet).
        use = lang if lang in ("uk", "en") else self._ui_lang
        subprocess.run(["pbcopy"], input=meta_prompt(use).encode("utf-8"), check=False)

    def _set_lang(self, lang: str) -> None:
        self._ui_lang = lang if lang in ("uk", "en") else "uk"
        if self._on_set_lang:
            self._on_set_lang(self._ui_lang)
        # Re-localize the live surfaces: the hint line, the open Settings window
        # (its labels re-render from the new `t` table). The menu titles are built
        # once and keep the launch language until the next restart.
        self._hint.title = self._t(
            "tray.hotkey", label=binding_label(self._hotkey["keycode"], self._hotkey["mods"])
        )
        self._refresh_settings_window()

    def _capture_hotkey(self, slot: str) -> None:
        """Begin live key-capture for a hotkey slot ("__toggle__" or a language
        code). The rebind + persist happens in the app callback once a key lands;
        bindings apply immediately (no relaunch)."""
        if self._on_capture_hotkey:
            self._on_capture_hotkey(slot)

    def _clear_hotkey(self, action: str) -> None:
        """Unassign a language slot's shortcut (back to no hotkey)."""
        if self._on_clear_hotkey:
            self._on_clear_hotkey(action)

    # Profile-set handlers (from the Settings window's JS bridge) ───────────────
    def _win_save_set(self, value: dict) -> None:
        """Create/replace a profile set from the window editor. On error notify
        and leave the window; on success store and re-render so the new ⌃⌥<digit>
        badge and member summary show at once."""
        if not self._on_save_set:
            return
        idx = value.get("index")
        idx = int(idx) if isinstance(idx, int) else None
        sets, err = self._on_save_set(idx, value.get("name", ""), value.get("members") or [])
        if err:
            self._refresh_settings_window(err)
            return
        self._profile_sets = sets
        self._refresh_settings_window(self._t("notice.saved"))

    def _win_delete_set(self, index: int) -> None:
        if not self._on_delete_set:
            return
        self._profile_sets = self._on_delete_set(int(index))
        self._refresh_settings_window()

    def _win_activate_set(self, index: int) -> None:
        if self._on_activate_set:
            self._on_activate_set(int(index))

    def set_active_profiles(self, active_profiles: dict) -> None:
        """Mirror an externally-changed active selection (a set activated by its
        ⌃⌥<digit> hotkey, possibly off the listener thread) into the menu
        checkmarks and the open window. Marshalled to the main thread."""

        def _apply() -> None:
            self._active_by_lang = {lng: set(v) for lng, v in (active_profiles or {}).items()}
            self._populate_profiles_menu(True)
            self._refresh_settings_window()

        AppHelper.callAfter(_apply)

    def update_hotkeys(self, hotkey: dict, lang_hotkeys: list[dict]) -> None:
        """Reflect a freshly-captured binding set: update the menu hint and push
        new state into the open Settings window."""
        self._hotkey = hotkey
        self._lang_hotkeys = lang_hotkeys
        self._hint.title = self._t(
            "tray.hotkey", label=binding_label(hotkey["keycode"], hotkey["mods"])
        )
        self._refresh_settings_window()

    def _lang_hotkeys_state(self) -> list[dict]:
        out = []
        for h in self._lang_hotkeys:
            assigned = h.get("keycode") is not None
            out.append(
                {
                    "action": h["action"],
                    "assigned": assigned,
                    "label": binding_label(h["keycode"], h.get("mods", [])) if assigned else "",
                    "lang_label": MODE_LABELS.get(h["action"], h["action"]),
                }
            )
        return out

    def _refresh_settings_window(self, notice: str | None = None) -> None:
        """Push fresh state into the open window (after add/edit/delete/import)
        without a reload, so the user stays on the Profiles screen."""
        if self._settings_window is not None:
            with contextlib.suppress(Exception):
                self._settings_window.refresh(notice)

    def _make_callback(self, code: str):
        def _cb(_sender):
            self.set_current_mode(code)
            self._on_mode_select(code)

        return _cb

    # ── Profiles ──────────────────────────────────────────────────────────────
    def _lang(self) -> str:
        """Whisper decode language of the active mode — profiles are filtered by it."""
        mode = MODES.get(self._current)
        return mode["language"] if mode else "uk"

    def _populate_profiles_menu(self, rebuild: bool = False) -> None:
        """Build (or rebuild, after an import) the Profiles submenu: a token-budget
        line, one multi-select toggle per profile, then import / copy-prompt.

        `rebuild=False` on first build (called from __init__ *before* the submenu
        is attached to the app menu — clearing then would hit removeAllItems on a
        nil NSMenu). `rebuild=True` after an import, when it's live and must be
        cleared before re-adding."""
        sub = self._profiles_submenu
        if rebuild:
            sub.clear()
        # Budget meter — info only (no callback → shown disabled).
        self._budget_item = rumps.MenuItem("")
        self._refresh_budget()
        sub.add(self._budget_item)
        sub.add(rumps.separator)

        # Show only the current language's profiles, with checkmarks for that
        # language's active group. Switching mode rebuilds this list.
        cur = self._lang()
        active = self._active_by_lang.get(cur, set())
        self._profile_items: dict[str, rumps.MenuItem] = {}
        shown = 0
        for p in self._profiles:
            if p.get("language", "") != cur:
                continue
            name = p.get("name", "")
            item = rumps.MenuItem(name, callback=self._make_profile_callback(name))
            item.state = 1 if name in active else 0
            self._profile_items[name] = item
            sub.add(item)
            shown += 1
        if shown == 0:
            sub.add(rumps.MenuItem(self._t("tray.noProfiles", lang=cur)))

        # Editing, import and the AI-prompt helper all live in Settings → Speech
        # profiles now; the menu keeps only the quick on/off toggles for the
        # language you're dictating in.
        sub.add(rumps.separator)
        sub.add(rumps.MenuItem(self._t("tray.editInSettings"), callback=self._open_settings))

    def _refresh_budget(self) -> None:
        lang = self._lang()
        active = list(self._active_by_lang.get(lang, set()))
        used, budget = budget_usage(self._profiles, active, lang)
        warn = self._t("tray.overBudget") if used > budget else ""
        self._budget_item.title = self._t(
            "tray.tokens", lang=lang, used=used, budget=budget, warn=warn
        )

    def _make_profile_callback(self, name: str):
        def _cb(_sender):
            item = self._profile_items[name]
            now_on = not bool(item.state)
            item.state = 1 if now_on else 0
            group = self._active_by_lang.setdefault(self._lang(), set())
            if now_on:
                group.add(name)
            else:
                group.discard(name)
            self._refresh_budget()
            if self._on_toggle_profile:
                self._on_toggle_profile(name, now_on)

        return _cb

    def _refresh_checkmarks(self) -> None:
        for code, item in self._mode_items.items():
            item.state = 1 if code == self._current else 0

    # NSStatusItem / NSMenuItem must be mutated on the main thread or AppKit
    # raises NSException → SIGABRT. Our callers (CGEventTap CFRunLoop, the
    # _finish daemon thread, the whisper health check) live on background
    # threads, so we hop to main via AppHelper.callAfter.

    def set_title(self, title: str) -> None:
        AppHelper.callAfter(setattr, self._app, "title", title)

    def set_status(self, text: str) -> None:
        AppHelper.callAfter(setattr, self._status, "title", text)

    def show_hud(self, text: str, state: str = "listening") -> None:
        """Show/update the floating streaming-status overlay, anchored under our
        own menu-bar icon so it visually drops out of it. Built lazily on the main
        thread; any AppKit failure is swallowed so a missing overlay never breaks
        dictation."""

        def _do() -> None:
            with contextlib.suppress(Exception):
                if self._hud is None:
                    from ._hud import StatusHUD

                    self._hud = StatusHUD()
                self._hud.show(text, self._ui_theme, state, self._status_item_frame())

        AppHelper.callAfter(_do)

    def _status_item_frame(self):
        """Screen frame (x, y, w, h) of our status-bar button, so the HUD can
        anchor under the icon. None if it can't be read (HUD falls back to the
        top-right corner)."""
        with contextlib.suppress(Exception):
            button = self._app._nsapp.nsstatusitem.button()
            f = button.window().frame()
            return (f.origin.x, f.origin.y, f.size.width, f.size.height)
        return None

    def hide_hud(self) -> None:
        def _do() -> None:
            with contextlib.suppress(Exception):
                if self._hud is not None:
                    self._hud.hide()

        AppHelper.callAfter(_do)

    def notify(self, title: str, subtitle: str, message: str) -> None:
        """A real system notification — visible without opening the menu. Used
        for the can't-paste fallback so the user knows the text is on the
        clipboard awaiting a manual ⌘V."""
        with contextlib.suppress(Exception):
            AppHelper.callAfter(rumps.notification, title, subtitle, message)

    def set_current_mode(self, code: str) -> None:
        self._current = code
        AppHelper.callAfter(self._refresh_checkmarks)
        # Language changed → swap the visible profile group (and its budget meter).
        AppHelper.callAfter(self._populate_profiles_menu, True)

    def run(self) -> None:
        # When run as a bare python process (dev) the Dock/⌘-Tab name is "Python"
        # with a generic icon. Override both at runtime so that whenever the
        # Settings window flips us to a Regular app, we show as "Pysar".
        self._brand_app()
        # Hide the Dock icon — this is a menu-bar agent, not a windowed app.
        # NSApplicationActivationPolicyAccessory (= 1) keeps the status-bar item
        # alive while removing the Dock tile and the ⌘-Tab entry.
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)
        except Exception as e:
            print(f"⚠️ could not hide Dock icon: {e}")
        self._app.run()

    @staticmethod
    def _brand_app() -> None:
        """Set the Dock icon early. (The name is set in _set_app_name before the
        menu is built; the icon is also re-applied in settings_window on the
        accessory→regular switch, which is when the Dock tile actually appears.)
        Cosmetic — any failure is swallowed."""
        from pathlib import Path

        try:
            from AppKit import NSApplication, NSImage

            icns = Path(__file__).resolve().parents[2] / "assets" / "CreamTyper.icns"
            if icns.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(icns))
                if img is not None:
                    NSApplication.sharedApplication().setApplicationIconImage_(img)
        except Exception as e:
            print(f"⚠️ could not set Dock icon: {e}")
