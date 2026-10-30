"""Ways to stop Rooks Desk at once, all of which set the same flag the agent checks before every action:

  * the global hotkey Ctrl+Alt+Esc (Ctrl+Option+Esc on a Mac), from any app
  * slamming the mouse into the top-left corner of the main screen (checked before each action)
  * the Stop button on the on-screen bar, or in the app's Computer page
  * `rooks-desk stop` from any terminal (it writes a STOP file this watches)
  * Ctrl+C in the terminal that started it
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

HOTKEY_LABEL = "Ctrl+Option+Esc" if sys.platform == "darwin" else "Ctrl+Alt+Esc"


class KillSwitch:
    def __init__(self, stop_file: Path | None = None, hotkey: bool = True, corner: bool = True):
        self.event = threading.Event()
        self.reason = ""
        self.stop_file = stop_file
        self.corner = corner
        self._threads: list[threading.Thread] = []
        self._listener = None
        self._x_display = None
        if stop_file:
            try:
                stop_file.unlink(missing_ok=True)
            except OSError:
                pass
            self._spawn(self._watch_file)
        self.hotkey_ok, self.hotkey_note = (self._start_hotkey() if hotkey else (False, "off"))

    @property
    def stopped(self) -> bool:
        return self.event.is_set()

    def trip(self, reason: str) -> None:
        if not self.event.is_set():
            self.reason = reason
            self.event.set()

    def check_corner(self, cursor: tuple[int, int]) -> None:
        if self.corner and cursor[0] <= 2 and cursor[1] <= 2:
            self.trip("mouse moved to the top-left corner")

    def close(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:  # noqa: BLE001
                pass
        self._closed = True

    # --- watchers
    def _spawn(self, fn):
        t = threading.Thread(target=fn, daemon=True, name=f"killswitch-{fn.__name__}")
        t.start()
        self._threads.append(t)

    def _watch_file(self):
        while not self.event.is_set() and not getattr(self, "_closed", False):
            if self.stop_file and self.stop_file.exists():
                self.trip("rooks-desk stop")
                try:
                    self.stop_file.unlink()
                except OSError:
                    pass
            time.sleep(0.2)

    def _start_hotkey(self) -> tuple[bool, str]:
        if sys.platform.startswith("linux"):
            return self._start_x11_hotkey()
        try:
            from pynput import keyboard
        except Exception as e:  # noqa: BLE001
            return False, f"pynput missing ({e})"
        try:
            self._listener = keyboard.GlobalHotKeys({"<ctrl>+<alt>+<esc>": lambda: self.trip(f"{HOTKEY_LABEL} pressed")})
            self._listener.daemon = True
            self._listener.start()
            return True, HOTKEY_LABEL
        except Exception as e:  # noqa: BLE001 - macOS without Accessibility permission, etc.
            return False, str(e)

    def _start_x11_hotkey(self) -> tuple[bool, str]:
        try:
            from Xlib import X, XK, display
            from Xlib.error import CatchError, BadAccess
        except Exception as e:  # noqa: BLE001
            return False, f"python-xlib missing ({e})"
        try:
            d = display.Display()
        except Exception as e:  # noqa: BLE001
            return False, f"no X display ({e})"
        root = d.screen().root
        code = d.keysym_to_keycode(XK.string_to_keysym("Escape"))
        base = X.ControlMask | X.Mod1Mask
        err = CatchError(BadAccess)
        for extra in (0, X.LockMask, X.Mod2Mask, X.LockMask | X.Mod2Mask):
            root.grab_key(code, base | extra, True, X.GrabModeAsync, X.GrabModeAsync, onerror=err)
        d.sync()
        if err.get_error():
            d.close()
            return False, "another program already uses Ctrl+Alt+Esc"
        self._x_display = d

        def loop():
            while not getattr(self, "_closed", False):
                while d.pending_events():
                    ev = d.next_event()
                    if ev.type == X.KeyPress:
                        self.trip(f"{HOTKEY_LABEL} pressed")
                time.sleep(0.05)
            try:
                d.close()
            except Exception:  # noqa: BLE001
                pass

        self._spawn(loop)
        return True, HOTKEY_LABEL
