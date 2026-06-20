"""macOS backend implementation: Quartz + rumps.

- HotkeyListener: CGEventTap on Caps Lock (toggle by AlphaShift state change)
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
    HOTKEY_KEYCODE,
    LANG_HOTKEYS,
    MODE_SHORTCUTS,
    MODES,
)
from ..profiles import META_PROMPT, PROMPT_TOKEN_BUDGET, budget_usage


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
class HotkeyListener:
    """Caps Lock toggle via CGEventTap.

    Caps Lock is a stateful key with an LED (kCGEventFlagMaskAlphaShift).
    1st tap: state 0→1 → start. 2nd tap: state 1→0 → stop.
    Events with Shift held are ignored — macOS temporarily clears the AlphaShift
    flag on shift+caps, which would otherwise trigger spurious toggles.
    """

    def __init__(self, toggle_keycode: int | None = None):
        self._caps_was_down = False
        # Remappable push/toggle key (defaults to Caps Lock). Note: the AlphaShift
        # state-change detection below is Caps-Lock-specific; remapping to a plain
        # key would need keyDown handling too — that's a follow-up.
        self._toggle_keycode = toggle_keycode or HOTKEY_KEYCODE
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
                if keycode == self._toggle_keycode:
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

    @staticmethod
    def capture_target():
        """Remember the app that was frontmost when dictation began, so the text
        lands there even if the user clicks elsewhere during a long transcription.
        Returns an NSRunningApplication (or None) to hand back to paste_text."""
        try:
            from AppKit import NSWorkspace

            return NSWorkspace.sharedWorkspace().frontmostApplication()
        except Exception:
            return None

    def paste_text(self, text: str, target=None) -> None:
        payload = text.encode("utf-8")
        saved = self._read_clipboard()
        self._write_clipboard(payload)

        # Restore focus to the app we were dictating into before pasting. A long
        # transcription gives the user time to click away; without this the Cmd+V
        # would fire into whatever window happens to be frontmost now. Clipboard
        # still holds the text as a fallback if reactivation fails.
        if target is not None:
            try:
                target.activateWithOptions_(2)  # NSApplicationActivateIgnoringOtherApps
                time.sleep(0.12)  # let the app come forward before the keystroke
            except Exception as e:
                print(f"⚠️ could not refocus paste target: {e}")

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
    ):
        # Name the app *before* rumps builds NSApplication below — AppKit reads
        # the bundle/process name once, when the main menu is first created, so a
        # later override (e.g. in run()) is ignored. "Custom" makes clear this is
        # our fork of the upstream Pysar, not the original.
        _set_app_name("Pysar Custom")

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
        self._settings_window = None  # built lazily on first open

        self._app = rumps.App("🎙", quit_button="Quit")
        self._status = rumps.MenuItem("Ready")
        self._hint = rumps.MenuItem("Hotkey: Caps Lock")

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

        lang_submenu = rumps.MenuItem("🌍 Languages")
        for code, _ in modes:
            lang_submenu.add(self._mode_items[code])

        self._profiles_submenu = rumps.MenuItem("👤 Profiles")
        self._populate_profiles_menu()

        settings_item = rumps.MenuItem("⚙️ Settings…", callback=self._open_settings)

        self._app.menu = [
            self._status,
            self._hint,
            None,
            lang_submenu,
            self._profiles_submenu,
            settings_item,
            None,
        ]

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
                    },
                )
            self._settings_window.show()
        except Exception as e:
            rumps.notification("Pysar", "Couldn't open Settings", str(e)[:120])

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
            "hotkey_label": "Caps Lock",
            "recordings_dir": self._recordings_dir or "",
            # Profile editor: the full library + the toggled-on group per language,
            # plus the token budget so the meter can update live in the window.
            "profiles": [dict(p) for p in self._profiles],
            "active_profiles": {lng: sorted(v) for lng, v in self._active_by_lang.items()},
            "current_lang": self._lang(),
            "token_budget": PROMPT_TOKEN_BUDGET,
        }

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
                "Couldn't enable Launch at login",
                "Add it manually: System Settings → General → Login Items "
                "(only works from the installed app, not a terminal run).",
            )

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
            rumps.notification("Pysar", "Couldn't save profile", err)
            return
        self._profiles = updated
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window("Profile saved.")

    def _win_delete_profile(self, name: str) -> None:
        if not self._on_delete_profile:
            return
        self._profiles = self._on_delete_profile(name)
        for group in self._active_by_lang.values():
            group.discard(name)
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window(f"Deleted “{name}”.")

    def _win_import_profiles(self, text: str) -> None:
        """JSON pasted into the window's import panel: merge, sync menu, report."""
        if not self._on_import_profiles:
            return
        updated, count, err = self._on_import_profiles(text)
        if err:
            self._refresh_settings_window(f"Import failed: {err}")
            return
        self._profiles = updated
        AppHelper.callAfter(self._populate_profiles_menu, True)
        self._refresh_settings_window(f"Imported {count} profile(s).")

    def _win_copy_ai_prompt(self) -> None:
        subprocess.run(["pbcopy"], input=META_PROMPT.encode("utf-8"), check=False)

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
            sub.add(rumps.MenuItem(f"(no profiles for {cur})"))

        # Editing, import and the AI-prompt helper all live in Settings → Speech
        # profiles now; the menu keeps only the quick on/off toggles for the
        # language you're dictating in.
        sub.add(rumps.separator)
        sub.add(rumps.MenuItem("Edit in Settings…", callback=self._open_settings))

    def _refresh_budget(self) -> None:
        lang = self._lang()
        active = list(self._active_by_lang.get(lang, set()))
        used, budget = budget_usage(self._profiles, active, lang)
        warn = "  ⚠️ over budget" if used > budget else ""
        self._budget_item.title = f"Tokens ({lang}): {used}/{budget}{warn}"

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

            icns = Path(__file__).resolve().parents[2] / "assets" / "Pysar.icns"
            if icns.exists():
                img = NSImage.alloc().initWithContentsOfFile_(str(icns))
                if img is not None:
                    NSApplication.sharedApplication().setApplicationIconImage_(img)
        except Exception as e:
            print(f"⚠️ could not set Dock icon: {e}")
