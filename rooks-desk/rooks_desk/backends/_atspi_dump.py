"""Dump the AT-SPI accessibility tree of one app's window as JSON. Imported in-process when this Python has pyatspi,
or run by the system Python (`python3 _atspi_dump.py PID TITLE [MAX]`) when Rooks Desk runs in its own environment.
Standalone on purpose: no imports from rooks_desk."""

from __future__ import annotations

import json
import sys

# containers and decoration; everything else (buttons, fields, labels, menus, cells, ... in GTK 3, GTK 4, Qt) is listed
SKIP = {"filler", "panel", "frame", "scroll pane", "section", "viewport", "application", "unknown", "separator", "window",
        "layered pane", "split pane", "grouping", "generic", "invalid", "redundant object", "glass pane", "root pane",
        "option pane", "internal frame", "desktop frame", "canvas", "embedded", "scroll bar", "table column header",
        "table row header", "image map", "block quote", "landmark"}


def dump(pid: int, title: str, max_nodes: int = 400) -> list[dict]:
    import pyatspi
    desktop = pyatspi.Registry.getDesktop(0)
    app = None
    for i in range(desktop.childCount):
        a = desktop.getChildAtIndex(i)
        try:
            if a and pid and a.get_process_id() == pid:
                app = a
                break
        except Exception:  # noqa: BLE001
            continue
    if app is None:
        return []
    frames = [app.getChildAtIndex(i) for i in range(app.childCount)]
    root = next((f for f in frames if f and f.name and f.name == title), None) or \
        next((f for f in frames if f and f.getState().contains(pyatspi.STATE_ACTIVE)), None) or (frames[0] if frames else None)
    out: list[dict] = []
    if root is not None:
        _walk(pyatspi, root, out, 0, max_nodes)
    return out


def _walk(pyatspi, node, out, depth, limit):
    if len(out) >= limit or depth > 40:
        return
    try:
        role = node.getRoleName()
        states = node.getState()
        showing = states.contains(pyatspi.STATE_SHOWING)
    except Exception:  # noqa: BLE001
        return
    if not showing and depth > 1:
        return
    if role not in SKIP:
        try:
            ext = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
            if ext.width > 0 and ext.height > 0:
                value = ""
                if role != "password text":
                    try:
                        t = node.queryText()
                        value = t.getText(0, min(t.characterCount, 300))
                    except Exception:  # noqa: BLE001
                        pass
                out.append({"role": role, "name": (node.name or "")[:120], "x": ext.x, "y": ext.y, "width": ext.width,
                            "height": ext.height, "value": value, "enabled": states.contains(pyatspi.STATE_ENABLED),
                            "secret": role == "password text", "depth": depth, "focused": states.contains(pyatspi.STATE_FOCUSED)})
        except Exception:  # noqa: BLE001
            pass
    try:
        n = node.childCount
    except Exception:  # noqa: BLE001
        return
    for i in range(min(n, 300)):
        try:
            c = node.getChildAtIndex(i)
        except Exception:  # noqa: BLE001
            continue
        if c is not None:
            _walk(pyatspi, c, out, depth + 1, limit)


if __name__ == "__main__":
    print(json.dumps(dump(int(sys.argv[1]), sys.argv[2] if len(sys.argv) > 2 else "", int(sys.argv[3]) if len(sys.argv) > 3 else 400)))
