"""Platform dispatch. Imports HotkeyListener / Paster / Tray from the
appropriate module based on sys.platform.

Backends:
  darwin  → _macos.py    (Quartz + rumps)         — shipped
  win32   → _windows.py  (pynput + pystray)       — TODO, see ROADMAP.md
  linux   → _linux.py    (pynput + pystray, X11)  — TODO, see ROADMAP.md
"""

import sys

if sys.platform == "darwin":
    from ._macos import HotkeyListener, Paster, Tray, login_item_enabled
elif sys.platform == "win32":
    from ._windows import HotkeyListener, Paster, Tray  # type: ignore[no-redef]
elif sys.platform.startswith("linux"):
    from ._linux import HotkeyListener, Paster, Tray  # type: ignore[no-redef]
else:
    raise RuntimeError(f"Platform {sys.platform!r} is not supported yet. See ROADMAP.md.")

if sys.platform != "darwin":  # login items are macOS-only

    def login_item_enabled() -> bool | None:  # type: ignore[no-redef]
        return None


__all__ = ["HotkeyListener", "Paster", "Tray", "login_item_enabled"]
