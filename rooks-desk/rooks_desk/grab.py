"""Ask from anywhere (feat/v02-anywhere-voice): what is selected in the app in front, and paste an answer back.

``grab`` presses the OS copy shortcut in the focused app, reads the clipboard, then puts the user's own clipboard back,
so nothing they copied earlier is lost. A screenshot of just that window is optional. ``paste`` puts text on the
clipboard, brings the window back and presses paste. Both are started by the user pressing the ask shortcut or the
Paste button, never by a model.
"""

from __future__ import annotations

import base64
import sys
import time
import uuid

from . import clipboard
from .backends.base import Window, to_png

PASTE_KEYS = ["cmd", "v"] if sys.platform == "darwin" else ["ctrl", "v"]
COPY_KEYS = ["cmd", "c"] if sys.platform == "darwin" else ["ctrl", "c"]


def _clip_get() -> str | None:
    try:
        return clipboard.get()
    except Exception:  # noqa: BLE001
        return None


def _clip_set(text: str) -> bool:
    try:
        clipboard.set(text)
        return True
    except Exception:  # noqa: BLE001
        return False


def _window_shot(backend, win: Window | None, max_width: int = 1600) -> str:
    img = backend.screenshot(1)
    if win and win.width > 0 and win.height > 0:
        mons = backend.monitors()
        mon = mons[1] if len(mons) > 1 else mons[0]
        scale = img.width / mon.width if mon.width else 1  # Retina: pixels per point
        box = (max(0, round((win.x - mon.left) * scale)), max(0, round((win.y - mon.top) * scale)),
               min(img.width, round((win.x - mon.left + win.width) * scale)), min(img.height, round((win.y - mon.top + win.height) * scale)))
        if box[2] - box[0] > 20 and box[3] - box[1] > 20:
            img = img.crop(box)
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)))
    return "data:image/png;base64," + base64.b64encode(to_png(img)).decode()


def grab(backend, copy: bool = True, screenshot: bool = False, wait: float = 0.8,
         get=_clip_get, put=_clip_set) -> dict:
    try:
        win = backend.focused_window()
    except Exception:  # noqa: BLE001
        win = None
    before = get()
    text, selected = "", False
    if copy:
        sentinel = f"rooks-desk-grab-{uuid.uuid4().hex}"
        if put(sentinel):
            try:
                backend.hotkey(COPY_KEYS)
            except Exception:  # noqa: BLE001  # no keyboard control (permission not given): fall back to the clipboard
                pass
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                now = get()
                if now is not None and now != sentinel:
                    text, selected = now, True
                    break
                time.sleep(0.05)
            put(before or "")  # the user's clipboard goes back exactly as it was
    out = {"text": text, "selected": selected, "clipboard": before or "", "window": win.to_dict() if win else None, "image": ""}
    if screenshot:
        try:
            out["image"] = _window_shot(backend, win)
        except Exception as e:  # noqa: BLE001
            out["image_error"] = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
    return out


def paste(backend, text: str, window: dict | None = None, put=_clip_set) -> dict:
    if not put(text):
        return {"copied": False, "pasted": False, "detail": "Could not reach the clipboard"}
    if window and window.get("id"):
        try:
            match = next((w for w in backend.windows() if w.id == window["id"]), None)
            if match:
                backend.focus_window(match)
                time.sleep(0.2)
        except Exception:  # noqa: BLE001
            pass
    try:
        time.sleep(0.1)
        backend.hotkey(PASTE_KEYS)
        return {"copied": True, "pasted": True}
    except Exception as e:  # noqa: BLE001
        return {"copied": True, "pasted": False, "detail": str(e).splitlines()[0][:200] if str(e) else "no keyboard control"}
