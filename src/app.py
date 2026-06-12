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
)
from .recorder import AudioRecorder
from .transcriber import is_alive, transcribe


class VoiceTyper:
    def __init__(self):
        self._mode = DEFAULT_MODE
        self._recorder = AudioRecorder()
        self._paster = Paster()
        self._recording = False
        self._busy = False  # blocks re-entry while a transcription is in flight

        self._tray = Tray(
            modes=[(code, MODE_LABELS[code]) for code in MENU_MODES],
            current_mode=self._mode,
            on_mode_select=self._on_mode_select,
        )

        # Hotkey listener is blocking — runs in its own thread.
        listener = HotkeyListener()
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

            t0 = time.time()
            text, err = transcribe(wav, mode=self._mode)
            dur = time.time() - t0

            if err:
                self._tray.set_status(f"⚠️ {err[:60]}")
                self._tray.set_title("⚠️")
                return
            if not text:
                self._tray.set_status("⚠️ Silence")
                self._tray.set_title(self._idle_title())
                return

            self._paster.paste_text(text)
            preview = text[:40] + ("…" if len(text) > 40 else "")
            self._tray.set_status(f"✓ ({dur:.1f}s) {preview}")
            self._tray.set_title(self._idle_title())
        finally:
            self._busy = False

    # ── Mode selection ───────────────────────────────────────────────────────
    def _on_mode_select(self, code: str) -> None:
        self._mode = code
        self._tray.set_current_mode(code)  # update the menu checkmark
        self._tray.set_status(f"Mode: {MODE_LABELS[code]}")
        # Reflect the language in the menu-bar icon for instant confirmation,
        # unless a record/transcribe cycle owns the title right now.
        if not self._recording and not self._busy:
            self._tray.set_title(self._idle_title())

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
