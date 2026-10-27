"""Linux X11 adapter (python-xlib, LGPL-2.1+, optional). Real input through the XTEST extension and real window state.

X11 has no accessibility tree of its own (desktops expose one through AT-SPI, which this adapter does not use).
Elements therefore come only from windows that publish a _ROOKS_ELEMENTS property; ROOKS's test fixture app does.
For other apps this adapter offers windows, screenshots, keys, typing and clicks at coordinates. It exists so the
engine can be tested against a real display; macOS is the supported desktop platform.
"""

from __future__ import annotations

import json
import os
import struct
import time
import zlib

from .base import Adapter, NotSupported

CHARS = {" ": "space", "\n": "Return", "\t": "Tab", ".": "period", ",": "comma", "-": "minus", "_": "underscore",
         ":": "colon", ";": "semicolon", "/": "slash", "!": "exclam", "?": "question", "'": "apostrophe", '"': "quotedbl",
         "(": "parenleft", ")": "parenright", "@": "at", "#": "numbersign", "$": "dollar", "%": "percent", "&": "ampersand",
         "*": "asterisk", "+": "plus", "=": "equal"}
KEYS = {"enter": "Return", "return": "Return", "tab": "Tab", "esc": "Escape", "escape": "Escape", "space": "space",
        "backspace": "BackSpace", "delete": "Delete", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
        "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next"}
MODS = {"ctrl": "Control_L", "cmd": "Control_L", "alt": "Alt_L", "option": "Alt_L", "shift": "Shift_L", "super": "Super_L", "meta": "Super_L"}


def png(width: int, height: int, rgb: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgb[y * width * 3:(y + 1) * width * 3] for y in range(height))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


class X11Adapter(Adapter):
    name = "x11"
    os = "Linux (X11)"

    def __init__(self, display: str | None = None):
        from Xlib import X, XK, display as xdisplay  # optional dependency
        from Xlib.ext import xtest

        self.X, self.XK, self.xtest = X, XK, xtest
        self.d = xdisplay.Display(display or os.environ.get("DISPLAY"))
        self.root = self.d.screen().root
        if not self.d.has_extension("XTEST"):
            raise NotSupported("This X server has no XTEST extension, so input cannot be sent.")
        self.A_ELEMENTS = self.d.intern_atom("_ROOKS_ELEMENTS")
        self.A_NAME = self.d.intern_atom("_NET_WM_NAME")
        self.A_UTF8 = self.d.intern_atom("UTF8_STRING")

    def permissions(self) -> dict:
        return {"accessibility": None, "screen_recording": True, "notes": "X11 has no permission prompts; anything on this display can be read and controlled."}

    def _top(self):
        out = []
        for w in self.root.query_tree().children:
            try:
                attrs = w.get_attributes()
                if attrs.map_state != self.X.IsViewable:
                    continue
                cls = w.get_wm_class()
                title = self._title(w)
                if not cls and not title:
                    continue
                g = w.get_geometry()
                out.append((w, (cls[1] if cls else "") or title, title, [g.x, g.y, g.width, g.height]))
            except Exception:  # noqa: BLE001 - windows come and go while we look
                continue
        return out

    def _title(self, w) -> str:
        p = w.get_full_property(self.A_NAME, self.A_UTF8)
        if p and p.value:
            return p.value.decode("utf-8", "replace") if isinstance(p.value, bytes) else str(p.value)
        return w.get_wm_name() or ""

    def _focus_id(self):
        """The focused window; with no window manager (focus follows the pointer), the window under the pointer."""
        focus = self.d.get_input_focus().focus
        fid = getattr(focus, "id", None)
        if fid in (None, self.root.id):
            child = self.root.query_pointer().child
            fid = getattr(child, "id", None) if child else None
        return fid

    def windows(self) -> list[dict]:
        fid = self._focus_id()
        return [{"app": app, "title": t, "focused": w.id == fid, "bbox": b, "id": w.id} for w, app, t, b in self._top()]

    def active_app(self) -> str:
        return next((w["app"] for w in self.windows() if w["focused"]), "")

    def elements(self, app: str = "") -> list[dict]:
        wins = self._top()
        focus = self._focus_id()
        target = next((x for x in wins if (app and x[1].lower() == app.lower()) or (not app and x[0].id == focus)), None)
        if not target:
            return []
        w, wapp, _, (wx, wy, _, _) = target
        p = w.get_full_property(self.A_ELEMENTS, self.A_UTF8)
        if not p or not p.value:
            return []
        out = []
        for i, e in enumerate(json.loads(p.value.decode() if isinstance(p.value, bytes) else p.value)):
            x, y, ww, hh = e["bbox"]
            out.append({"role": e.get("role", ""), "label": e.get("label", ""), "value": e.get("value", ""), "secure": bool(e.get("secure")),
                        "bbox": [wx + x, wy + y, ww, hh], "path": [w.id, i], "app": wapp})
        return out

    def screenshot(self) -> bytes | None:
        g = self.root.get_geometry()
        img = self.root.get_image(0, 0, g.width, g.height, self.X.ZPixmap, 0xFFFFFFFF)
        data = img.data
        rgb = bytearray(g.width * g.height * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
        return png(g.width, g.height, bytes(rgb))

    def capture_window(self, wid: int) -> bytes | None:
        """One window, by reading its area of the screen (it must be on top to be fully visible)."""
        w = next((x for x in self.windows() if x["id"] == wid), None)
        if not w:
            return None
        x, y, ww, hh = w["bbox"]
        img = self.root.get_image(x, y, ww, hh, self.X.ZPixmap, 0xFFFFFFFF)
        data = img.data
        rgb = bytearray(ww * hh * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = data[2::4], data[1::4], data[0::4]
        return png(ww, hh, bytes(rgb))

    def open_app(self, name: str) -> None:
        raise NotSupported("Opening apps is not supported on X11 in this adapter; start the app yourself.")

    def focus(self, app: str) -> None:
        for w, a, _, _ in self._top():
            if a.lower() == app.lower():
                w.set_input_focus(self.X.RevertToParent, self.X.CurrentTime)
                w.configure(stack_mode=self.X.Above)
                self.d.sync()
                return
        raise RuntimeError(f"No window for {app}.")

    def click_at(self, x: int, y: int) -> None:
        self.xtest.fake_input(self.d, self.X.MotionNotify, x=int(x), y=int(y))
        self.d.sync()
        self.xtest.fake_input(self.d, self.X.ButtonPress, 1)
        self.d.sync()
        time.sleep(0.03)
        self.xtest.fake_input(self.d, self.X.ButtonRelease, 1)
        self.d.sync()

    def _code(self, name: str) -> int:
        ks = self.XK.string_to_keysym(name)
        code = self.d.keysym_to_keycode(ks)
        if not code:
            raise RuntimeError(f"No key for {name!r} on this keyboard map.")
        return code

    def _tap(self, codes: list[int]) -> None:
        for c in codes:
            self.xtest.fake_input(self.d, self.X.KeyPress, c)
        for c in reversed(codes):
            self.xtest.fake_input(self.d, self.X.KeyRelease, c)
        self.d.sync()
        time.sleep(0.01)

    def type_text(self, text: str) -> None:
        shift = self._code("Shift_L")
        for ch in text:
            name = CHARS.get(ch, ch)
            ks = self.XK.string_to_keysym(name)
            code = self.d.keysym_to_keycode(ks)
            if not code:
                raise RuntimeError(f"Cannot type {ch!r} on this keyboard map.")
            needs_shift = self.d.keycode_to_keysym(code, 0) != ks
            self._tap([shift, code] if needs_shift else [code])

    def key(self, combo: str) -> None:
        parts = combo.lower().split("+")
        codes = [self._code(MODS[p]) for p in parts[:-1]]
        last = parts[-1]
        codes.append(self._code(KEYS.get(last, last)))
        self._tap(codes)

    def scroll(self, direction: str, amount: int) -> None:
        button = 4 if direction == "up" else 5
        for _ in range(amount):
            self.xtest.fake_input(self.d, self.X.ButtonPress, button)
            self.xtest.fake_input(self.d, self.X.ButtonRelease, button)
        self.d.sync()
