"""A tiny X11 app for testing ROOKS Companion against a real display (not part of the product).

It draws real buttons, reacts to real mouse and keyboard events from the X server, shows its state in its window title,
saves its document with Ctrl+S, and publishes its controls in a _ROOKS_ELEMENTS window property, the role an
accessibility API plays on macOS. usage: fixture_app.py SAVE_PATH
"""

import json
import sys

from Xlib import X, XK, display

SAVE = sys.argv[1]
d = display.Display()
s = d.screen()
w = s.root.create_window(40, 40, 420, 260, 0, s.root_depth, X.InputOutput, X.CopyFromParent, background_pixel=s.white_pixel,
                         event_mask=X.ExposureMask | X.ButtonPressMask | X.KeyPressMask | X.StructureNotifyMask)
w.set_wm_class("fixture", "ROOKS Fixture")
gc = w.create_gc(foreground=s.black_pixel, background=s.white_pixel)
ELEMENTS = [
    {"role": "Button", "label": "Add one", "bbox": [20, 20, 120, 40]},
    {"role": "Button", "label": "Delete all", "bbox": [160, 20, 120, 40]},
    {"role": "TextArea", "label": "Document", "bbox": [20, 80, 380, 100]},
    {"role": "TextField", "label": "Password", "secure": True, "bbox": [20, 200, 200, 30]},
]
state = {"count": 0, "text": "", "focus": "Document", "deleted": False, "saved": 0, "hidden": 0}
HIDDEN = [300, 190, 80, 40]  # a red button drawn on the canvas and NOT published as an element: only visible on screen
red = s.default_colormap.alloc_color(65535, 0, 0).pixel
gc_red = w.create_gc(foreground=red, background=s.white_pixel)
gc_green = w.create_gc(foreground=s.default_colormap.alloc_color(0, 50000, 0).pixel, background=s.white_pixel)
A_EL, A_UTF8, A_NAME = d.intern_atom("_ROOKS_ELEMENTS"), d.intern_atom("UTF8_STRING"), d.intern_atom("_NET_WM_NAME")


def publish():
    els = [dict(e, value=state["text"] if e["label"] == "Document" else "") for e in ELEMENTS]
    w.change_property(A_EL, A_UTF8, 8, json.dumps(els).encode())
    title = f"ROOKS Fixture count={state['count']} saved={state['saved']} deleted={state['deleted']} hidden={state['hidden']} text={state['text']}"
    w.set_wm_name(title)
    w.change_property(A_NAME, A_UTF8, 8, title.encode())
    d.flush()


def draw():
    for e in ELEMENTS:
        x, y, ww, hh = e["bbox"]
        w.rectangle(gc, x, y, ww, hh)
    w.fill_rectangle(gc, 25, 85, min(370, 6 * len(state["text"])), 6)
    w.fill_rectangle(gc_red, *HIDDEN)
    w.fill_rectangle(gc_green, 20, 200, 200, 30)  # the password field is solid green, so its redaction to black can be checked


w.map()
publish()
while True:
    ev = d.next_event()
    if ev.type == X.Expose:
        draw()
    elif ev.type == X.ButtonPress:
        hx, hy, hw, hh = HIDDEN
        if hx <= ev.event_x <= hx + hw and hy <= ev.event_y <= hy + hh:
            state["hidden"] += 1
        for e in ELEMENTS:
            x, y, ww, hh = e["bbox"]
            if x <= ev.event_x <= x + ww and y <= ev.event_y <= y + hh:
                if e["label"] == "Add one":
                    state["count"] += 1
                elif e["label"] == "Delete all":
                    state["deleted"] = True
                state["focus"] = e["label"]
        publish()
    elif ev.type == X.KeyPress:
        shift = bool(ev.state & X.ShiftMask)
        ks = d.keycode_to_keysym(ev.detail, 1 if shift else 0)
        if ev.state & X.ControlMask and ks in (XK.XK_s, XK.XK_S):
            with open(SAVE, "w") as f:
                f.write(state["text"])
            state["saved"] += 1
        elif state["focus"] == "Document":
            ch = XK.keysym_to_string(ks)
            if ks == XK.XK_BackSpace:
                state["text"] = state["text"][:-1]
            elif ks == XK.XK_Return:
                state["text"] += "\n"
            elif ch and len(ch) == 1 and ch.isprintable():
                state["text"] += ch
        publish()
        draw()
