"""Entry point. Voice-typing business logic, free of platform-specific code.

Event flow:
  Caps Lock (tap 1) → start recording
  Caps Lock (tap 2) → stop → Whisper → clipboard → Cmd+V → restore clipboard

Platform-specific adapters (hotkey, paste, tray) live in pysar.backend.
"""

import threading
import time

from .backend import HotkeyListener, Paster, Tray
from .config import (
    DEFAULT_MODE,
    IDLE_ICON_FALLBACK,
    MENU_MODES,
    MODE_ICONS,
    MODE_LABELS,
    MODES,
)
from .profiles import (
    compose_prompt,
    merge_profiles,
    parse_imported,
    remove_profile,
    upsert_profile,
)
from .recorder import AudioRecorder, list_input_devices
from .recordings import (
    KEEP_LAST_OPTIONS,
    load_settings,
    recordings_dir,
    save_recording,
    save_settings,
)
from .transcriber import is_alive, transcribe


class VoiceTyper:
    def __init__(self):
        # Persisted settings (off by default — audio stays in memory).
        self._settings = load_settings()

        # Restore the last-used language; fall back if the stored code is unknown.
        saved_mode = self._settings.get("mode", DEFAULT_MODE)
        self._mode = saved_mode if saved_mode in MODES else DEFAULT_MODE

        self._recorder = AudioRecorder(device=self._settings.get("mic"))
        self._paster = Paster()
        self._recording = False
        self._busy = False  # blocks re-entry while a transcription is in flight

        self._tray = Tray(
            modes=[(code, MODE_LABELS[code]) for code in MENU_MODES],
            current_mode=self._mode,
            on_mode_select=self._on_mode_select,
            save_recordings=self._settings["save_recordings"],
            keep_last=self._settings["keep_last"],
            keep_last_options=KEEP_LAST_OPTIONS,
            on_toggle_save=self._on_toggle_save,
            on_set_keep_last=self._on_set_keep_last,
            recordings_dir=str(recordings_dir()),
            profiles=self._settings["profiles"],
            active_profiles=self._settings["active_profiles"],
            on_toggle_profile=self._on_toggle_profile,
            on_import_profiles=self._on_import_profiles,
            on_save_profile=self._on_save_profile,
            on_delete_profile=self._on_delete_profile,
            mics=list_input_devices(),
            current_mic=self._settings.get("mic"),
            on_select_mic=self._on_select_mic,
            launch_at_login=self._settings.get("launch_at_login", False),
            on_toggle_login=self._on_toggle_login,
            ui_theme=self._settings.get("ui_theme", "auto"),
            on_set_theme=self._on_set_theme,
        )

        # Hotkey listener is blocking — runs in its own thread. The toggle key is
        # remappable; a change applies on next launch (the event tap is built once).
        listener = HotkeyListener(self._settings.get("hotkey_keycode"))
        threading.Thread(
            target=listener.start,
            args=(self._on_toggle, self._on_mode_select),
            daemon=True,
        ).start()

        # Whisper-server health check at startup.
        threading.Thread(target=self._check_whisper, daemon=True).start()

        # Show the active language in the menu bar from the start.
        self._tray.set_title(self._idle_title())

    def run(self) -> None:
        self._tray.run()

    def _idle_title(self) -> str:
        """Menu-bar glyph when idle — the active language's flag."""
        return MODE_ICONS.get(self._mode, IDLE_ICON_FALLBACK)

    # ── Hotkey ───────────────────────────────────────────────────────────────
    def _on_toggle(self) -> None:
        if self._busy:
            return

        if not self._recording:
            self._recording = True
            # Remember the focused app now, so the result pastes back here even if
            # the user clicks away during a slow transcription.
            self._paste_target = self._paster.capture_target()
            self._tray.set_title("🔴")
            self._tray.set_status("● Recording…")
            self._recorder.start()
        else:
            self._recording = False
            self._busy = True
            self._tray.set_title("⏳")
            self._tray.set_status("⏳ Transcribing…")
            threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self) -> None:
        try:
            wav = self._recorder.stop()
            if wav is None:
                self._tray.set_status("⚠️ Too short")
                self._tray.set_title(self._idle_title())
                return

            # Persist the audio BEFORE transcribing, so a failed/aborted run is
            # recoverable (re-transcribe later instead of re-speaking). Never let
            # a save error break dictation.
            if self._settings["save_recordings"]:
                try:
                    save_recording(wav, self._settings["keep_last"])
                except Exception as e:
                    print(f"⚠️ save_recording failed: {e}")

            # Compose the whisper prompt from this language's active profile group.
            lang = MODES.get(self._mode, MODES[DEFAULT_MODE])["language"]
            active = self._settings["active_profiles"].get(lang, [])
            prompt = compose_prompt(self._settings["profiles"], active, lang)

            t0 = time.time()
            text, err = transcribe(wav, mode=self._mode, prompt=prompt)
            dur = time.time() - t0

            if err:
                self._tray.set_status(f"⚠️ {err[:60]}")
                self._tray.set_title("⚠️")
                return
            if not text:
                self._tray.set_status("⚠️ Silence")
                self._tray.set_title(self._idle_title())
                return

            self._paster.paste_text(text, getattr(self, "_paste_target", None))
            preview = text[:40] + ("…" if len(text) > 40 else "")
            self._tray.set_status(f"✓ ({dur:.1f}s) {preview}")
            self._tray.set_title(self._idle_title())
        finally:
            self._busy = False

    # ── Mode selection ───────────────────────────────────────────────────────
    def _on_mode_select(self, code: str) -> None:
        self._mode = code
        # Persist so the chosen language survives a restart.
        self._settings["mode"] = code
        save_settings(self._settings)
        self._tray.set_current_mode(code)  # update the menu checkmark
        self._tray.set_status(f"Mode: {MODE_LABELS[code]}")
        # Reflect the language in the menu-bar icon for instant confirmation,
        # unless a record/transcribe cycle owns the title right now.
        if not self._recording and not self._busy:
            self._tray.set_title(self._idle_title())

    # ── Recording-archive settings ───────────────────────────────────────────
    def _on_toggle_save(self, enabled: bool) -> None:
        self._settings["save_recordings"] = enabled
        save_settings(self._settings)
        self._tray.set_status("💾 Saving recordings" if enabled else "Recordings: memory only")

    def _on_set_keep_last(self, n: int) -> None:
        self._settings["keep_last"] = n
        save_settings(self._settings)
        self._tray.set_status(f"Keeping last {n} recordings")

    # ── Speech profiles ───────────────────────────────────────────────────────
    def _on_toggle_profile(self, name: str, active: bool) -> None:
        # A profile belongs to its own language group, independent of the current
        # mode — toggling "English" affects the en group, "Розробка" the uk group.
        lang = next(
            (p.get("language", "uk") for p in self._settings["profiles"] if p.get("name") == name),
            "uk",
        )
        group = self._settings["active_profiles"].setdefault(lang, [])
        if active and name not in group:
            group.append(name)
        elif not active and name in group:
            group.remove(name)
        save_settings(self._settings)
        self._tray.set_status(f"👤 {name}: {'on' if active else 'off'}")

    def _on_save_profile(
        self, name: str, language: str, prompt: str, original_name: str | None
    ) -> tuple[list[dict] | None, str | None]:
        """Add or edit a profile from the Settings-window editor. Returns
        (updated_profiles | None, error). Keeps the active group in sync when a
        profile is renamed, so a toggled-on group doesn't lose its member."""
        updated, err = upsert_profile(
            self._settings["profiles"], name, language, prompt, original_name
        )
        if err:
            return None, err
        if original_name and original_name != name:
            for group in self._settings["active_profiles"].values():
                if original_name in group:
                    group[group.index(original_name)] = name
        self._settings["profiles"] = updated
        save_settings(self._settings)
        return updated, None

    def _on_delete_profile(self, name: str) -> list[dict]:
        """Remove a profile and drop it from every active group."""
        self._settings["profiles"] = remove_profile(self._settings["profiles"], name)
        for group in self._settings["active_profiles"].values():
            if name in group:
                group.remove(name)
        save_settings(self._settings)
        return self._settings["profiles"]

    def _on_import_profiles(self, text: str) -> tuple[list[dict] | None, int, str | None]:
        """Parse pasted JSON, merge into the library, persist. Returns
        (updated_profiles | None, added_count, error) for the tray to react."""
        incoming, err = parse_imported(text)
        if err:
            return None, 0, err
        self._settings["profiles"] = merge_profiles(self._settings["profiles"], incoming)
        save_settings(self._settings)
        return self._settings["profiles"], len(incoming), None

    def _on_select_mic(self, name: str | None) -> None:
        self._settings["mic"] = name
        save_settings(self._settings)
        self._recorder.set_device(name)
        self._tray.set_status(f"🎤 {name}" if name else "🎤 Default mic")

    def _on_toggle_login(self, enabled: bool) -> None:
        self._settings["launch_at_login"] = enabled
        save_settings(self._settings)
        self._tray.set_status("🚀 Launch at login: on" if enabled else "Launch at login: off")

    def _on_set_theme(self, theme: str) -> None:
        self._settings["ui_theme"] = theme
        save_settings(self._settings)

    # ── Whisper health ───────────────────────────────────────────────────────
    def _check_whisper(self) -> None:
        time.sleep(0.5)
        if not is_alive():
            self._tray.set_status("⚠️ Whisper not running — `make whisper`")
            self._tray.set_title("⚠️")


def main() -> None:
    """Console-script entry point (see pyproject.toml [project.scripts])."""
    VoiceTyper().run()


if __name__ == "__main__":
    main()
