"""Protocol contracts for the platform backends.

Concrete implementations live in _macos.py / _windows.py / _linux.py.
This file is type-hint and documentation glue — read it before writing a new
backend (see ROADMAP.md).
"""

from collections.abc import Callable
from typing import Protocol


class HotkeyBackend(Protocol):
    """Global toggle hotkey. Must not block input from other applications."""

    def start(self, on_toggle: Callable[[], None]) -> None:
        """Blocking call — invoke from a dedicated thread."""
        ...


class PasteBackend(Protocol):
    """Pastes text into the active window. The previous clipboard is restored."""

    def capture_target(self):
        """Snapshot the frontmost app to refocus before pasting (or None)."""
        ...

    def paste_text(self, text: str, target=None) -> bool:
        """Returns True if pasted, False if it was left on the clipboard for a
        manual ⌘V (focus could not be returned to `target`)."""
        ...

    def type_text(self, text: str) -> None:
        """Type `text` into the focused field as synthetic key events, without
        touching the clipboard. Used by streaming dictation (one sentence at a
        time as it's transcribed)."""
        ...

    def has_editable_focus(self, target=None) -> bool:
        """True when keyboard focus is in a text field safe to type into right
        now. Streaming checks this before each sentence; False (no field, an
        overlay grabbing keys) means buffer the sentence on the clipboard."""
        ...

    def set_clipboard(self, text: str) -> None:
        """Put `text` on the system clipboard (streaming buffer mode)."""
        ...


class TrayBackend(Protocol):
    """Menu-bar / system-tray icon with a submenu for mode selection."""

    def set_title(self, title: str) -> None: ...
    def set_status(self, text: str) -> None: ...
    def set_current_mode(self, code: str) -> None: ...

    def run(self) -> None:
        """Blocking tray event loop. Must run on the main thread."""
        ...
