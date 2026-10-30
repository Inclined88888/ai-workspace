"""Linux / X11: python-xlib for input (XTest) and windows (EWMH), mss for screenshots, AT-SPI (pyatspi, when the
system has it) for accessibility trees. Wayland sessions block synthetic input and screen capture by design: run an
X11 session ("GNOME on Xorg") or use XWayland apps."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time

from .base import Backend, BackendUnavailable, Element, Window, normalise_keys

_KEYSYMS = {
    "ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L", "cmd": "Super_L", "enter": "Return", "tab": "Tab",
    "esc": "Escape", "backspace": "BackSpace", "delete": "Delete", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next", "space": "space",
    "insert": "Insert", "capslock": "Caps_Lock", "printscreen": "Print",
}
_CHAR_KEYSYMS = {"\n": "Return", "\t": "Tab", " ": "space"}
_BUTTONS = {"left": 1, "middle": 2, "right": 3}


class LinuxBackend(Backend):
    name = "linux-x11"
    platform = "linux"

    def __init__(self):
        if os.getenv("WAYLAND_DISPLAY") and not os.getenv("DISPLAY"):
            raise BackendUnavailable("this is a Wayland session without X11. Log in with an X11 session (e.g. \"GNOME on "
                                     "Xorg\") so Rooks Desk can see the screen and use the mouse and keyboard.")
        if not os.getenv("DISPLAY"):
            raise BackendUnavailable("no display (DISPLAY is not set). On a server, start one with: Xvfb :99 & export DISPLAY=:99")
        try:
            from Xlib import X, XK, display  # noqa: F401
            from Xlib.ext import xtest  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable("python-xlib is missing: pip install python-xlib") from e
        from Xlib import display as xdisplay
        self.X = __import__("Xlib.X", fromlist=["X"])
        self.XK = __import__("Xlib.XK", fromlist=["XK"])
        self.xtest = __import__("Xlib.ext.xtest", fromlist=["xtest"])
        self.d = xdisplay.Display()
        if not self.d.has_extension("XTEST"):
            raise BackendUnavailable("the X server has no XTEST extension, so synthetic input is impossible")
        self.root = self.d.screen().root
        self._lock = threading.RLock()

    # --- input
    def cursor(self):
        p = self.root.query_pointer()
        return p.root_x, p.root_y

    def move(self, x, y):
        with self._lock:
            self.xtest.fake_input(self.d, self.X.MotionNotify, x=int(x), y=int(y))
            self.d.sync()

    def click(self, x, y, button="left", count=1):
        b = _BUTTONS.get(button, 1)
        with self._lock:
            self.move(x, y)
            time.sleep(0.03)
            for i in range(max(1, int(count))):
                self.xtest.fake_input(self.d, self.X.ButtonPress, b)
                self.d.sync()
                time.sleep(0.02)
                self.xtest.fake_input(self.d, self.X.ButtonRelease, b)
                self.d.sync()
                if i + 1 < count:
                    time.sleep(0.06)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4):
        b = _BUTTONS.get(button, 1)
        with self._lock:
            self.move(x1, y1)
            self.xtest.fake_input(self.d, self.X.ButtonPress, b)
            self.d.sync()
            steps = max(4, int(duration / 0.02))
            for i in range(1, steps + 1):
                self.move(x1 + (x2 - x1) * i / steps, y1 + (y2 - y1) * i / steps)
                time.sleep(duration / steps)
            self.xtest.fake_input(self.d, self.X.ButtonRelease, b)
            self.d.sync()

    def scroll(self, x, y, dx=0, dy=-3):
        with self._lock:
            self.move(x, y)
            for btn, n in ((4 if dy > 0 else 5, abs(int(dy))), (6 if dx < 0 else 7, abs(int(dx)))):
                for _ in range(n):
                    self.xtest.fake_input(self.d, self.X.ButtonPress, btn)
                    self.xtest.fake_input(self.d, self.X.ButtonRelease, btn)
                    self.d.sync()
                    time.sleep(0.01)

    def _keycode(self, keysym_name: str):
        ks = self.XK.string_to_keysym(keysym_name)
        return ks, (self.d.keysym_to_keycode(ks) if ks else 0)

    def _tap(self, keycode: int, shift: bool = False):
        shift_kc = self.d.keysym_to_keycode(self.XK.string_to_keysym("Shift_L"))
        if shift:
            self.xtest.fake_input(self.d, self.X.KeyPress, shift_kc)
        self.xtest.fake_input(self.d, self.X.KeyPress, keycode)
        self.xtest.fake_input(self.d, self.X.KeyRelease, keycode)
        if shift:
            self.xtest.fake_input(self.d, self.X.KeyRelease, shift_kc)
        self.d.sync()

    def _type_char(self, ch: str):
        name = _CHAR_KEYSYMS.get(ch)
        ks = self.XK.string_to_keysym(name) if name else 0
        if not ks:
            ks = ord(ch) if ord(ch) < 0x100 else 0x01000000 | ord(ch)
            if ord(ch) < 0x100:
                # Latin-1: X keysyms equal the code point
                pass
        kc = self.d.keysym_to_keycode(ks)
        if kc:
            # is the keysym on the shifted level of that key?
            shift = self.d.keycode_to_keysym(kc, 0) != ks and self.d.keycode_to_keysym(kc, 1) == ks
            self._tap(kc, shift)
            return
        # Not on the keyboard (é, emoji, CJK): borrow an unused keycode for this one character, then give it back
        spare = self._spare_keycode()
        self.d.change_keyboard_mapping(spare, [(ks,) * 4])
        self.d.sync()
        time.sleep(0.02)
        self._tap(spare)
        time.sleep(0.02)
        self.d.change_keyboard_mapping(spare, [(0,) * 4])
        self.d.sync()

    def _spare_keycode(self) -> int:
        lo, hi = self.d.display.info.min_keycode, self.d.display.info.max_keycode
        mapping = self.d.get_keyboard_mapping(lo, hi - lo + 1)
        for i in range(len(mapping) - 1, -1, -1):
            if not any(mapping[i]):
                return lo + i
        return hi - 1

    def type_text(self, text, interval=0.0):
        with self._lock:
            for ch in text:
                if ch == "\r":
                    continue
                self._type_char(ch)
                time.sleep(interval or 0.008)

    def hotkey(self, keys):
        keys = normalise_keys(keys)
        codes = []
        for k in keys:
            name = _KEYSYMS.get(k) or (k.upper() if k.startswith("f") and k[1:].isdigit() else k)
            ks, kc = self._keycode(name)
            if not kc:
                raise ValueError(f"no key for {k!r} on this keyboard")
            codes.append(kc)
        with self._lock:
            for kc in codes:
                self.xtest.fake_input(self.d, self.X.KeyPress, kc)
                self.d.sync()
                time.sleep(0.01)
            for kc in reversed(codes):
                self.xtest.fake_input(self.d, self.X.KeyRelease, kc)
            self.d.sync()

    # --- windows (EWMH)
    def _prop(self, win, name, kind=None):
        try:
            atom = self.d.intern_atom(name)
            p = win.get_full_property(atom, kind if kind is not None else self.X.AnyPropertyType)
            return p.value if p else None
        except Exception:  # noqa: BLE001 - windows vanish between list and query
            return None

    def windows(self):
        ids = self._prop(self.root, "_NET_CLIENT_LIST") or []
        active = self._prop(self.root, "_NET_ACTIVE_WINDOW")
        active_id = int(active[0]) if active is not None and len(active) else 0
        utf8 = self.d.intern_atom("UTF8_STRING")
        out = []
        for wid in ids:
            try:
                w = self.d.create_resource_object("window", int(wid))
                title = self._prop(w, "_NET_WM_NAME", utf8)
                title = title.decode("utf-8", "replace") if isinstance(title, bytes) else (w.get_wm_name() or "")
                cls = w.get_wm_class() or ("", "")
                pid = self._prop(w, "_NET_WM_PID")
                g = w.get_geometry()
                t = w.translate_coords(self.root, 0, 0)
                out.append(Window(str(int(wid)), str(title), str(cls[1] or cls[0]), int(pid[0]) if pid is not None and len(pid) else 0,
                                  -t.x, -t.y, g.width, g.height, int(wid) == active_id))
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda w: not w.focused)
        return out

    def focus_window(self, win):
        from Xlib.protocol import event
        w = self.d.create_resource_object("window", int(win.id))
        ev = event.ClientMessage(window=w, client_type=self.d.intern_atom("_NET_ACTIVE_WINDOW"),
                                 data=(32, [2, self.X.CurrentTime, 0, 0, 0]))
        mask = self.X.SubstructureRedirectMask | self.X.SubstructureNotifyMask
        self.root.send_event(ev, event_mask=mask)
        self.d.sync()
        time.sleep(0.2)

    def open_app(self, name, args=None):
        args = list(args or [])
        exe = shutil.which(name) or shutil.which(name.lower())
        if exe:
            subprocess.Popen([exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return f"started {exe}"
        desktop = _find_desktop_entry(name)
        if desktop and shutil.which("gtk-launch"):
            subprocess.Popen(["gtk-launch", desktop, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return f"launched {desktop}"
        if os.path.exists(name) and shutil.which("xdg-open"):
            subprocess.Popen(["xdg-open", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return f"opened {name}"
        raise ValueError(f"no app called {name!r} found on PATH or in the applications menu")

    # --- accessibility (AT-SPI)
    def ax_available(self):
        try:
            import pyatspi  # noqa: F401
            return True, "AT-SPI"
        except Exception:  # noqa: BLE001
            pass
        if _system_atspi():
            return True, f"AT-SPI (through {_system_atspi()})"
        return False, ("AT-SPI is not installed (sudo apt install python3-pyatspi at-spi2-core); element lists are off, "
                       "screenshots still work")

    def accessibility(self, win=None, max_nodes=400):
        win = win or self.focused_window()
        if not win:
            return []
        try:
            from . import _atspi_dump
            import pyatspi  # noqa: F401
            rows = _atspi_dump.dump(win.pid, win.title, max_nodes)
        except ImportError:
            py = _system_atspi()
            if not py:
                return []
            # pyatspi lives in the system Python (it needs PyGObject); ask it, as a separate process
            try:
                r = subprocess.run([py, os.path.join(os.path.dirname(__file__), "_atspi_dump.py"), str(win.pid), win.title, str(max_nodes)],
                                   capture_output=True, text=True, timeout=15)
                rows = json.loads(r.stdout or "[]")
            except (OSError, ValueError, subprocess.TimeoutExpired):
                return []
        return [Element(d["role"], d["name"], d["x"], d["y"], d["width"], d["height"], value=d.get("value", ""),
                        enabled=d.get("enabled", True), secret=d.get("secret", False), depth=d.get("depth", 0),
                        extra={"focused": True} if d.get("focused") else {}) for d in rows]


_SYS_PY: list = []


def _system_atspi() -> str:
    """A system Python that has pyatspi, when this one does not (uv-installed tools have their own Python)."""
    if not _SYS_PY:
        found = ""
        for py in ("/usr/bin/python3", "/usr/local/bin/python3"):
            if os.path.exists(py) and os.path.realpath(py) != os.path.realpath(sys.executable):
                try:
                    if subprocess.run([py, "-c", "import pyatspi"], capture_output=True, timeout=10).returncode == 0:
                        found = py
                        break
                except (OSError, subprocess.TimeoutExpired):
                    continue
        _SYS_PY.append(found)
    return _SYS_PY[0]


def _find_desktop_entry(name: str) -> str | None:
    n = name.lower().replace(" ", "")
    dirs = [os.path.expanduser("~/.local/share/applications"), "/usr/share/applications", "/usr/local/share/applications"]
    for d in dirs:
        try:
            for f in os.listdir(d):
                if f.endswith(".desktop"):
                    base = f[:-8]
                    if n in base.lower().replace("-", "").replace(".", ""):
                        return base
                    try:
                        with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                            for line in fh:
                                if line.startswith("Name=") and line[5:].strip().lower().replace(" ", "") == n:
                                    return base
                    except OSError:
                        continue
        except OSError:
            continue
    return None
