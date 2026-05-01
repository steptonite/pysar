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

    def paste_text(self, text: str) -> None: ...


class TrayBackend(Protocol):
    """Menu-bar / system-tray icon with a submenu for mode selection."""

    def set_title(self, title: str) -> None: ...
    def set_status(self, text: str) -> None: ...
    def set_current_mode(self, code: str) -> None: ...

    def run(self) -> None:
        """Blocking tray event loop. Must run on the main thread."""
        ...
