"""macOS adapter, built only from what ships with macOS: no extra packages.

  * Accessibility (windows, elements, pressing, typing, menus, dialogs): JavaScript for Automation through osascript,
    driving System Events. Parameters are passed as JSON in argv, never pasted into script text.
  * Window list, window ids, bounds and displays (for Retina and multiple monitors): CoreGraphics
    CGWindowListCopyWindowInfo and AppKit NSScreen through the JXA Objective-C bridge.
  * Permission checks: AXIsProcessTrusted (ApplicationServices) and CGPreflightScreenCaptureAccess (CoreGraphics)
    through ctypes; Automation (Apple events to System Events) by a harmless probe. macOS asks the user; nothing here
    works around its prompts.
  * Screen capture: /usr/sbin/screencapture (a whole display, or one window with -l), downscaled with /usr/bin/sips.
  * Scrolling: CGEventCreateScrollWheelEvent2 + CGEventPost through ctypes.

Coordinates: accessibility and System Events use global points with the origin at the top left of the main display;
other displays can have negative coordinates. Screenshots are in pixels (2x on Retina). Visual targets are therefore
exchanged as positions normalised to the captured window (0..1), and turned into points with the window's bounds at
click time (see window_point), so the scale factor never matters and a moved window is detected.

The permissions belong to the app that runs the companion (Terminal, or Python itself), so macOS lists that app under
Privacy & Security.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import subprocess
import tempfile
import time

from .base import Adapter, NotSupported

AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
SCREEN_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
AUTOMATION_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"

KEY_CODES = {"enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "delete": 117, "esc": 53, "escape": 53,
             "left": 123, "right": 124, "down": 125, "up": 126, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
             **{f"f{i}": c for i, c in zip(range(1, 13), (122, 120, 99, 118, 96, 97, 98, 100, 101, 109, 103, 111))}}
MODS = {"cmd": "command down", "ctrl": "control down", "alt": "option down", "option": "option down", "shift": "shift down",
        "fn": "function down"}

JXA_WINDOWS = r"""
ObjC.import('CoreGraphics');
function run(argv) {
  const se = Application('System Events');
  const front = se.applicationProcesses.whose({frontmost: true})();
  const frontName = front.length ? front[0].name() : '';
  // CoreGraphics gives ids and bounds for on-screen windows (titles only with Screen Recording permission)
  const raw = ObjC.deepUnwrap(ObjC.castRefToObject($.CGWindowListCopyWindowInfo($.kCGWindowListOptionOnScreenOnly | $.kCGWindowListExcludeDesktopElements, $.kCGNullWindowID))) || [];
  const out = [];
  let firstOfFront = true;
  for (const w of raw) {
    if (w.kCGWindowLayer !== 0) continue;            // menus, the Dock and overlays are not windows to work in
    const b = w.kCGWindowBounds || {};
    const app = w.kCGWindowOwnerName || '';
    const focused = app === frontName && firstOfFront;
    if (app === frontName) firstOfFront = false;
    out.push({app: app, title: w.kCGWindowName || '', id: w.kCGWindowNumber, pid: w.kCGWindowOwnerPID,
              bbox: [b.X || 0, b.Y || 0, b.Width || 0, b.Height || 0], focused: focused});
  }
  return JSON.stringify({front: frontName, windows: out});
}"""

JXA_DISPLAYS = r"""
ObjC.import('AppKit');
function run(argv) {
  const screens = $.NSScreen.screens.js;
  const main = screens[0].frame;
  return JSON.stringify(screens.map((s, i) => {
    const f = s.frame;
    // NSScreen uses a bottom-left origin; convert to the top-left origin that accessibility and events use
    return {index: i, x: f.origin.x, y: main.size.height - (f.origin.y + f.size.height), width: f.size.width,
            height: f.size.height, scale: s.backingScaleFactor, main: i === 0};
  }));
}"""

JXA_ACTIVE = r"""
function run(argv) {
  const p = Application('System Events').applicationProcesses.whose({frontmost: true})();
  return p.length ? p[0].name() : '';
}"""

JXA_PROBE = r"""
function run(argv) { return String(Application('System Events').applicationProcesses.whose({frontmost: true})().length); }"""

# Breadth-first walk of the front window (sheets and dialogs included), bounded in depth and size so it stays fast.
JXA_ELEMENTS = r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const se = Application('System Events');
  const procs = a.app ? se.applicationProcesses.whose({name: a.app})() : se.applicationProcesses.whose({frontmost: true})();
  if (!procs.length) return JSON.stringify({error: 'not_running'});
  const p = procs[0];
  let wins = []; try { wins = p.windows(); } catch (e) {}
  if (!wins.length) return JSON.stringify({window: null, elements: []});
  const w = wins[0];
  let wsub = '', wtitle = '', wpos = [0, 0], wsz = [0, 0];
  try { wsub = w.subrole() || ''; } catch (e) {}
  try { wtitle = w.name() || ''; } catch (e) {}
  try { wpos = w.position(); wsz = w.size(); } catch (e) {}
  const dialogWin = /Dialog|SystemDialog|FloatingWindow/.test(wsub);
  const out = [];
  const queue = [[w, [0], 0, dialogWin]];
  while (queue.length && out.length < a.max) {
    const [el, path, depth, inDialog] = queue.shift();
    let kids = []; try { kids = el.uiElements(); } catch (e) {}
    for (let i = 0; i < kids.length && out.length < a.max; i++) {
      const k = kids[i], kp = path.concat([i]);
      let role = '', name = '', desc = '', val = '', pos = [0, 0], sz = [0, 0], enabled = true;
      try { role = k.role(); } catch (e) {}
      try { name = k.name() || ''; } catch (e) {}
      try { desc = k.description() || ''; } catch (e) {}
      try { enabled = k.enabled(); } catch (e) {}
      const secure = role === 'AXSecureTextField';
      if (!secure) { try { const v = k.value(); val = (v === null || v === undefined) ? '' : String(v).slice(0, 200); } catch (e) {} }
      try { pos = k.position(); sz = k.size(); } catch (e) {}
      const sheet = role === 'AXSheet';
      const interesting = /Button|TextField|TextArea|CheckBox|RadioButton|PopUpButton|MenuButton|ComboBox|Link|Cell|MenuItem|Tab|Slider|StaticText/.test(role);
      if (interesting && (name || desc || val || /Field|Area/.test(role))) {
        out.push({role: role.replace('AX', ''), label: name || desc, description: desc, value: val, secure: secure, enabled: enabled,
                  in_dialog: inDialog || sheet, bbox: [pos[0], pos[1], sz[0], sz[1]], path: kp});
      }
      if (depth < a.depth) queue.push([k, kp, depth + 1, inDialog || sheet]);
    }
  }
  return JSON.stringify({window: {title: wtitle, subrole: wsub, bbox: [wpos[0], wpos[1], wsz[0], wsz[1]], dialog: dialogWin}, elements: out});
}"""

# Find the element again by its path and check it is the same one (role, label, roughly the same place) before acting.
JXA_RESOLVE = r"""
function find(a) {
  const se = Application('System Events');
  const procs = se.applicationProcesses.whose({name: a.app})();
  if (!procs.length) return [null, {gone: 'app'}];
  let el = procs[0].windows()[0];
  try { for (const i of a.path.slice(1)) el = el.uiElements()[i]; } catch (e) { return [null, {gone: 'element'}]; }
  let role = '', name = '', pos = [0, 0];
  try { role = (el.role() || '').replace('AX', ''); } catch (e) { return [null, {gone: 'element'}]; }
  try { name = el.name() || el.description() || ''; } catch (e) {}
  try { pos = el.position(); } catch (e) {}
  const lab = (a.label || '').toLowerCase(), nm = name.toLowerCase();
  if (a.role && role !== a.role) return [null, {moved: true, found: role + ' ' + name}];
  if (lab && nm && nm.indexOf(lab) < 0 && lab.indexOf(nm) < 0) return [null, {moved: true, found: name}];
  if (a.bbox && (Math.abs(pos[0] - a.bbox[0]) > 40 || Math.abs(pos[1] - a.bbox[1]) > 40)) return [null, {moved: true, found: name + ' (moved)'}];
  return [el, {}];
}"""

JXA_PRESS = JXA_RESOLVE + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const [el, err] = find(a);
  if (!el) return JSON.stringify(err);
  try { el.actions.byName('AXPress').perform(); return JSON.stringify({pressed: true}); } catch (e) {}
  try { el.focused = true; return JSON.stringify({focused: true}); } catch (e) {}
  return JSON.stringify({pressed: false});
}"""

JXA_SET_VALUE = JXA_RESOLVE + r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const [el, err] = find(a);
  if (!el) return JSON.stringify(err);
  try { el.focused = true; } catch (e) {}
  try { el.value = a.text; } catch (e) { return JSON.stringify({set: false, error: String(e)}); }
  let now = ''; try { now = String(el.value()); } catch (e) {}
  return JSON.stringify({set: now === a.text, now_length: now.length});
}"""

JXA_MENU = r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const se = Application('System Events');
  const procs = se.applicationProcesses.whose({name: a.app})();
  if (!procs.length) return JSON.stringify({gone: 'app'});
  let item;
  try {
    let menu = procs[0].menuBars[0].menuBarItems.byName(a.path[0]).menus[0];
    for (let i = 1; i < a.path.length - 1; i++) menu = menu.menuItems.byName(a.path[i]).menus[0];
    item = a.path.length > 1 ? menu.menuItems.byName(a.path[a.path.length - 1]) : procs[0].menuBars[0].menuBarItems.byName(a.path[0]);
    if (a.path.length > 1 && !item.enabled()) return JSON.stringify({disabled: true});
    item.click();
  } catch (e) { return JSON.stringify({missing: String(e).slice(0, 160)}); }
  return JSON.stringify({clicked: true});
}"""

JXA_FOCUSED = r"""
function run(argv) {
  const p = Application('System Events').applicationProcesses.whose({frontmost: true})();
  if (!p.length) return '';
  try { const f = p[0].attributes.byName('AXFocusedUIElement').value(); return f ? f.role() : ''; } catch (e) { return ''; }
}"""

JXA_KEYS = r"""
function run(argv) {
  const a = JSON.parse(argv[0]);
  const se = Application('System Events');
  if (a.text !== undefined) { se.keystroke(a.text); return 'ok'; }
  if (a.code !== undefined) { se.keyCode(a.code, {using: a.using}); return 'ok'; }
  se.keystroke(a.char, {using: a.using}); return 'ok';
}"""

JXA_CLICK = r"""
function run(argv) { const a = JSON.parse(argv[0]); Application('System Events').click({at: [a.x, a.y]}); return 'ok'; }"""

JXA_ACTIVATE = r"""
function run(argv) { const a = JSON.parse(argv[0]); Application(a.app).activate(); return 'ok'; }"""


def window_point(bbox: list, nx: float, ny: float) -> tuple[int, int]:
    """A position normalised to a window (0..1) as global points. Works on any display and at any Retina scale."""
    x, y, w, h = bbox
    if not (0 <= nx <= 1 and 0 <= ny <= 1) or w <= 0 or h <= 0:
        raise ValueError("position outside the window")
    return int(round(x + nx * w)), int(round(y + ny * h))


def display_for(displays: list[dict], x: float, y: float) -> dict | None:
    for d in displays:
        if d["x"] <= x < d["x"] + d["width"] and d["y"] <= y < d["y"] + d["height"]:
            return d
    return None


class MacOSAdapter(Adapter):
    name = "macos"
    os = "macOS"

    def __init__(self, run=subprocess.run):
        self._run = run  # replaceable in tests; never a shell
        self._ax = self._cg = None
        try:
            self._ax = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
            self._cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            self._ax.AXIsProcessTrusted.restype = ctypes.c_bool
            self._cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
            self._cg.CGRequestScreenCaptureAccess.restype = ctypes.c_bool
        except (OSError, AttributeError):
            pass

    # Plumbing
    def _jxa(self, script: str, args: dict | None = None, timeout: float = 15) -> str:
        cmd = ["/usr/bin/osascript", "-l", "JavaScript", "-e", script]
        if args is not None:
            cmd += ["--", json.dumps(args)]
        try:
            r = self._run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(f"macOS did not answer within {timeout:.0f} seconds (the app may be busy or not responding).") from e
        if r.returncode != 0:
            raise self._explain(r.stderr.strip())
        return r.stdout.strip()

    @staticmethod
    def _explain(err: str) -> Exception:
        low = err.lower()
        if "-1743" in err or "not authorized to send apple events" in low:
            return NotSupported("macOS has not allowed the app running ROOKS Companion to control System Events. Open System Settings, "
                                "Privacy & Security, Automation, and turn on System Events for it.")
        if "-1719" in err or "-25211" in err or "assistive" in low or "not allowed assistive access" in low:
            return NotSupported("macOS has not given Accessibility access to the app running ROOKS Companion. Open System Settings, "
                                "Privacy & Security, Accessibility, and turn it on.")
        if "-1728" in err or "-600" in err or "can't get" in low or "isn't running" in low:
            return RuntimeError("The app or its window is no longer there (it may have closed or crashed). Observe again.")
        return RuntimeError(err[:300] or "osascript failed")

    # Permissions
    def permissions(self) -> dict:
        ax = bool(self._ax.AXIsProcessTrusted()) if self._ax else None
        sc = bool(self._cg.CGPreflightScreenCaptureAccess()) if self._cg else None
        automation = None
        try:
            self._jxa(JXA_PROBE, timeout=8)
            automation = True
        except NotSupported as e:
            automation = False if "Automation" in str(e) else automation
        except RuntimeError:
            pass
        notes = []
        if ax is False:
            notes.append("Accessibility is off: ROOKS cannot read or use apps. Turn it on in System Settings, Privacy & Security, Accessibility.")
        if automation is False:
            notes.append("Automation for System Events is off: turn it on in System Settings, Privacy & Security, Automation.")
        if sc is False:
            notes.append("Screen Recording is off: no screenshots or visual targeting (apps still work through Accessibility).")
        return {"accessibility": ax, "screen_recording": sc, "automation": automation, "notes": " ".join(notes)}

    def request_permissions(self) -> None:
        """Open the right System Settings panes and let macOS ask. Nothing is granted without the user."""
        if self._cg:
            self._cg.CGRequestScreenCaptureAccess()
        for pane in (AX_PANE, AUTOMATION_PANE):
            self._run(["/usr/bin/open", pane], check=False)

    # Looking
    def displays(self) -> list[dict]:
        return json.loads(self._jxa(JXA_DISPLAYS) or "[]")

    def windows(self) -> list[dict]:
        return json.loads(self._jxa(JXA_WINDOWS) or "{}").get("windows", [])

    def window(self, wid: int) -> dict | None:
        return next((w for w in self.windows() if w.get("id") == wid), None)

    def active_app(self) -> str:
        return self._jxa(JXA_ACTIVE)

    def elements(self, app: str = "") -> list[dict]:
        out = json.loads(self._jxa(JXA_ELEMENTS, {"app": app, "max": 200, "depth": 7}, timeout=25) or "{}")
        if out.get("error") == "not_running":
            raise RuntimeError(f"{app or 'The app'} is not running.")
        self.last_window = out.get("window")
        return out.get("elements", [])

    def screenshot(self) -> bytes | None:
        if self._cg and not self._cg.CGPreflightScreenCaptureAccess():
            return None
        return self._capture(["-x", "-t", "png", "-m"])  # -m: the main display

    def capture_window(self, wid: int) -> bytes | None:
        """One window, as it is on screen right now (Retina pixels, downscaled). None without Screen Recording."""
        if self._cg and not self._cg.CGPreflightScreenCaptureAccess():
            return None
        return self._capture(["-x", "-o", "-t", "png", f"-l{int(wid)}"])

    def _capture(self, args: list[str]) -> bytes:
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.png")
            self._run(["/usr/sbin/screencapture", *args, path], check=True, timeout=15)
            self._run(["/usr/bin/sips", "-Z", "1600", path], capture_output=True, check=False, timeout=15)
            with open(path, "rb") as f:
                return f.read()

    # Acting
    def open_app(self, name: str) -> None:
        r = self._run(["/usr/bin/open", "-a", name], capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            raise RuntimeError(f"Could not open {name}: {r.stderr.strip()[:200]}")
        self.wait_front(name)

    def focus(self, app: str) -> None:
        self._jxa(JXA_ACTIVATE, {"app": app})
        self.wait_front(app)

    def wait_front(self, app: str, seconds: float = 3.0) -> None:
        """Activation is asynchronous: confirm the app really is in front before any input is sent to it."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.active_app().lower() == app.lower():
                return
            time.sleep(0.15)
        raise RuntimeError(f"{app} did not come to the front (another window may be in the way, or it is not responding).")

    def _target(self, el: dict) -> dict:
        return {"app": el["app"], "path": el["path"], "label": el.get("label", ""), "role": el.get("role", ""), "bbox": el.get("bbox")}

    def press(self, el: dict) -> None:
        r = json.loads(self._jxa(JXA_PRESS, self._target(el)) or "{}")
        if r.get("gone"):
            raise RuntimeError("The element is gone (the window changed or closed). Observe again.")
        if r.get("moved"):
            raise RuntimeError(f"The element changed (now '{r.get('found', '')}'). Observe again.")
        if not r.get("pressed") and not r.get("focused"):
            super().press(el)  # no accessibility action: click its centre

    def set_value(self, el: dict, text: str) -> dict:
        """Replace a text field's content through accessibility: exact for any language, unlike simulated keys."""
        r = json.loads(self._jxa(JXA_SET_VALUE, {**self._target(el), "text": text}) or "{}")
        if r.get("gone") or r.get("moved"):
            raise RuntimeError("The field changed or is gone. Observe again.")
        if not r.get("set"):
            raise RuntimeError("macOS did not accept the new value for that field" + (f": {r['error'][:120]}" if r.get("error") else "."))
        return r

    def menu(self, app: str, path: list[str]) -> None:
        r = json.loads(self._jxa(JXA_MENU, {"app": app, "path": path}) or "{}")
        if r.get("gone"):
            raise RuntimeError(f"{app} is not running.")
        if r.get("disabled"):
            raise RuntimeError(f"The menu item {' > '.join(path)} is disabled right now.")
        if r.get("missing"):
            raise RuntimeError(f"No menu item {' > '.join(path)} in {app}.")

    def click_at(self, x: int, y: int) -> None:
        self._jxa(JXA_CLICK, {"x": int(x), "y": int(y)})

    def focused_secure(self) -> bool:
        """True when the focused element is a password field: typing is refused before any key is sent."""
        return self._jxa(JXA_FOCUSED) == "AXSecureTextField"

    def type_text(self, text: str) -> None:
        self._jxa(JXA_KEYS, {"text": text})

    def key(self, combo: str) -> None:
        parts = combo.lower().split("+")
        using = [MODS[p] for p in parts[:-1]]
        last = parts[-1]
        if last in KEY_CODES:
            self._jxa(JXA_KEYS, {"code": KEY_CODES[last], "using": using})
        else:
            self._jxa(JXA_KEYS, {"char": last, "using": using})

    def scroll(self, direction: str, amount: int) -> None:
        if not self._cg:
            raise NotSupported("Scrolling needs CoreGraphics.")
        cg = self._cg
        cg.CGEventCreateScrollWheelEvent2.restype = ctypes.c_void_p
        cg.CGEventCreateScrollWheelEvent2.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_int32, ctypes.c_int32, ctypes.c_int32]
        cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
        cf = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
        cf.CFRelease.argtypes = [ctypes.c_void_p]
        lines = amount if direction == "up" else -amount
        ev = cg.CGEventCreateScrollWheelEvent2(None, 1, 1, lines, 0, 0)  # 1 = kCGScrollEventUnitLine
        cg.CGEventPost(0, ev)  # 0 = kCGHIDEventTap
        cf.CFRelease(ev)
