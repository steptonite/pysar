"""macOS backend implementation: Quartz + rumps.

- HotkeyListener: CGEventTap on Caps Lock (toggle by AlphaShift state change)
- Paster:         pbcopy + Cmd+V simulation via CGEvent, restores previous clipboard
- Tray:           rumps.App with «🌍 Languages» submenu
"""

import subprocess
import time
from collections.abc import Callable

import Quartz
import rumps
from PyObjCTools import AppHelper

from ..config import CLIPBOARD_RESTORE_DELAY, HOTKEY_KEYCODE, LANG_HOTKEYS


# ── Hotkey ───────────────────────────────────────────────────────────────────
class HotkeyListener:
    """Caps Lock toggle via CGEventTap.

    Caps Lock is a stateful key with an LED (kCGEventFlagMaskAlphaShift).
    1st tap: state 0→1 → start. 2nd tap: state 1→0 → stop.
    Events with Shift held are ignored — macOS temporarily clears the AlphaShift
    flag on shift+caps, which would otherwise trigger spurious toggles.
    """

    def __init__(self):
        self._caps_was_down = False
        self._on_toggle: Callable[[], None] | None = None
        self._on_mode: Callable[[str], None] | None = None

    def start(
        self,
        on_toggle: Callable[[], None],
        on_mode: Callable[[str], None] | None = None,
    ) -> None:
        self._on_toggle = on_toggle
        self._on_mode = on_mode

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
        print("✅ Hotkey listener started (Caps Lock; Ctrl+Option+U/R/E to switch language)")
        Quartz.CFRunLoopRun()

    def _callback(self, proxy, event_type, event, refcon):
        try:
            if event_type == Quartz.kCGEventFlagsChanged:
                keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                if keycode == HOTKEY_KEYCODE:
                    flags = Quartz.CGEventGetFlags(event)
                    caps_down = bool(flags & Quartz.kCGEventFlagMaskAlphaShift)
                    shift_down = bool(flags & Quartz.kCGEventFlagMaskShift)

                    if not shift_down and caps_down != self._caps_was_down and self._on_toggle:
                        self._on_toggle()
                    self._caps_was_down = caps_down

            elif event_type == Quartz.kCGEventKeyDown and self._on_mode:
                keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
                if keycode in LANG_HOTKEYS:
                    flags = Quartz.CGEventGetFlags(event)
                    ctrl = bool(flags & Quartz.kCGEventFlagMaskControl)
                    alt = bool(flags & Quartz.kCGEventFlagMaskAlternate)
                    if ctrl and alt:
                        self._on_mode(LANG_HOTKEYS[keycode])
        except Exception as e:
            print(f"⚠️ hotkey callback: {e}")
        return event


# ── Paste ────────────────────────────────────────────────────────────────────
_KEYCODE_V = 9  # virtual keycode for 'v', layout-independent


class Paster:
    """Pastes via clipboard + Cmd+V, restoring the previous clipboard contents."""

    def paste_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        saved = self._read_clipboard()
        self._write_clipboard(payload)

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
        # it's consumed (the other half of the same race).
        time.sleep(CLIPBOARD_RESTORE_DELAY)
        try:
            self._write_clipboard(saved)
        except Exception as e:
            print(f"⚠️ failed to restore clipboard: {e}")

    @staticmethod
    def _read_clipboard() -> bytes:
        return subprocess.run(["pbpaste"], capture_output=True).stdout

    @staticmethod
    def _write_clipboard(data: bytes) -> None:
        subprocess.run(["pbcopy"], input=data, check=True)

    @staticmethod
    def _press_cmd_v() -> None:
        src = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStateHIDSystemState)

        down = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_V, True)
        Quartz.CGEventSetFlags(down, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, down)

        time.sleep(0.01)

        up = Quartz.CGEventCreateKeyboardEvent(src, _KEYCODE_V, False)
        Quartz.CGEventSetFlags(up, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, up)


# ── Tray ─────────────────────────────────────────────────────────────────────
class Tray:
    """Menu-bar tray via rumps. All modes live under the «🌍 Languages» submenu."""

    def __init__(
        self,
        modes: list[tuple[str, str]],
        current_mode: str,
        on_mode_select: Callable[[str], None],
    ):
        self._current = current_mode
        self._on_mode_select = on_mode_select

        self._app = rumps.App("🎙", quit_button="Quit")
        self._status = rumps.MenuItem("Ready")
        self._hint = rumps.MenuItem("Hotkey: Caps Lock")

        self._mode_items: dict[str, rumps.MenuItem] = {}
        for code, label in modes:
            item = rumps.MenuItem(label, callback=self._make_callback(code))
            self._mode_items[code] = item
        self._refresh_checkmarks()

        lang_submenu = rumps.MenuItem("🌍 Languages")
        for code, _ in modes:
            lang_submenu.add(self._mode_items[code])

        self._app.menu = [self._status, self._hint, None, lang_submenu, None]

    def _make_callback(self, code: str):
        def _cb(_sender):
            self.set_current_mode(code)
            self._on_mode_select(code)

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

    def set_current_mode(self, code: str) -> None:
        self._current = code
        AppHelper.callAfter(self._refresh_checkmarks)

    def run(self) -> None:
        # Hide the Dock icon — this is a menu-bar agent, not a windowed app.
        # NSApplicationActivationPolicyAccessory (= 1) keeps the status-bar item
        # alive while removing the Dock tile and the ⌘-Tab entry.
        try:
            from AppKit import NSApplication

            NSApplication.sharedApplication().setActivationPolicy_(1)
        except Exception as e:
            print(f"⚠️ could not hide Dock icon: {e}")
        self._app.run()
