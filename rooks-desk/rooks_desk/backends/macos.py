"""macOS: pynput (Quartz events) for input, mss for screenshots, Quartz for the window list and the Accessibility API
(AXUIElement, via pyobjc) for element trees.

macOS asks the person for two permissions, granted to the app that starts Rooks Desk (Terminal, iTerm, or AI
Workspace): Screen Recording (System Settings > Privacy & Security > Screen & System Audio Recording) to see the screen,
and Accessibility (Privacy & Security > Accessibility) to click, type and read element trees. `rooks-desk setup` shows
the prompts and opens the right panes."""

from __future__ import annotations

import subprocess
import time

from ._pynput import PynputInput
from .base import Backend, BackendUnavailable, Element, Window

SCREEN_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"
AX_PANE = "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"


class MacBackend(Backend):
    name = "macos"
    platform = "darwin"

    def __init__(self):
        try:
            import ApplicationServices  # noqa: F401
            import Quartz  # noqa: F401
        except ImportError as e:
            raise BackendUnavailable("pyobjc is missing: pip install pyobjc-framework-Quartz pyobjc-framework-ApplicationServices") from e
        self._input = None

    @property
    def input(self) -> PynputInput:
        if self._input is None:
            self._input = PynputInput()
        return self._input

    # --- permissions
    def permissions(self):
        import ApplicationServices as AS
        import Quartz
        screen = bool(Quartz.CGPreflightScreenCaptureAccess()) if hasattr(Quartz, "CGPreflightScreenCaptureAccess") else True
        ax = bool(AS.AXIsProcessTrusted())
        return {
            "screen": {"ok": screen, "how": "System Settings > Privacy & Security > Screen & System Audio Recording: turn on the app "
                                            "you started Rooks Desk from (Terminal or AI Workspace), then quit and reopen it."},
            "input": {"ok": ax, "how": "System Settings > Privacy & Security > Accessibility: turn on the same app."},
            "accessibility": {"ok": ax, "how": "Same switch as input (Accessibility)."},
        }

    def request_permissions(self):
        import ApplicationServices as AS
        import Quartz
        if hasattr(Quartz, "CGRequestScreenCaptureAccess") and not Quartz.CGPreflightScreenCaptureAccess():
            Quartz.CGRequestScreenCaptureAccess()
        if not AS.AXIsProcessTrusted():
            opts = {AS.kAXTrustedCheckOptionPrompt: True}
            AS.AXIsProcessTrustedWithOptions(opts)

    def open_settings(self, which: str):
        subprocess.run(["open", SCREEN_PANE if which == "screen" else AX_PANE], check=False)

    def permission_hint(self, what):
        p = self.permissions()
        return "" if p.get(what, {}).get("ok") else p.get(what, {}).get("how", "")

    def _need_ax(self):
        import ApplicationServices as AS
        if not AS.AXIsProcessTrusted():
            raise BackendUnavailable("macOS has not allowed this app to control the computer yet. " + self.permissions()["input"]["how"])

    def screenshot(self, monitor=1):
        import Quartz
        if hasattr(Quartz, "CGPreflightScreenCaptureAccess") and not Quartz.CGPreflightScreenCaptureAccess():
            raise BackendUnavailable("macOS has not allowed screen recording yet. " + self.permissions()["screen"]["how"])
        return super().screenshot(monitor)

    # --- input
    def cursor(self):
        return self.input.cursor()

    def move(self, x, y):
        self._need_ax()
        self.input.move(x, y)

    def click(self, x, y, button="left", count=1):
        self._need_ax()
        self.input.click(x, y, button, count)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4):
        self._need_ax()
        self.input.drag(x1, y1, x2, y2, button, duration)

    def scroll(self, x, y, dx=0, dy=-3):
        self._need_ax()
        self.input.scroll(x, y, dx, dy)

    def type_text(self, text, interval=0.0):
        self._need_ax()
        self.input.type_text(text, interval)

    def hotkey(self, keys):
        self._need_ax()
        self.input.hotkey(keys)

    # --- windows
    def windows(self):
        import Quartz
        opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
        infos = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
        front_pid = self._frontmost_pid()
        out, seen_front = [], False
        for w in infos:
            if int(w.get("kCGWindowLayer", 0)) != 0:
                continue  # menu bar, dock, overlays
            b = w.get("kCGWindowBounds") or {}
            pid = int(w.get("kCGWindowOwnerPID", 0))
            focused = pid == front_pid and not seen_front
            seen_front = seen_front or focused
            out.append(Window(str(w.get("kCGWindowNumber")), str(w.get("kCGWindowName") or ""), str(w.get("kCGWindowOwnerName") or ""),
                              pid, int(b.get("X", 0)), int(b.get("Y", 0)), int(b.get("Width", 0)), int(b.get("Height", 0)), focused))
        out.sort(key=lambda w: not w.focused)
        return out

    def _frontmost_pid(self) -> int:
        try:
            from AppKit import NSWorkspace
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            return int(app.processIdentifier()) if app else 0
        except Exception:  # noqa: BLE001
            return 0

    def focus_window(self, win):
        try:
            from AppKit import NSApplicationActivateIgnoringOtherApps, NSRunningApplication
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(win.pid)
            if app:
                app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        except Exception:  # noqa: BLE001
            subprocess.run(["osascript", "-e", f'tell application "{win.app}" to activate'], check=False, capture_output=True)
        # raise the exact window through AX when allowed
        try:
            import ApplicationServices as AS
            appref = AS.AXUIElementCreateApplication(win.pid)
            err, wins = AS.AXUIElementCopyAttributeValue(appref, "AXWindows", None)
            for w in wins or []:
                e2, title = AS.AXUIElementCopyAttributeValue(w, "AXTitle", None)
                if (title or "") == win.title:
                    AS.AXUIElementPerformAction(w, "AXRaise")
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.3)

    def open_app(self, name, args=None):
        cmd = ["open", "-a", name] + (["--args", *args] if args else [])
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            if name.startswith("/") or name.endswith((".app", ".dwg", ".pdf", ".txt")):
                r = subprocess.run(["open", name], capture_output=True, text=True)
            if r.returncode != 0:
                raise ValueError(f"could not open {name!r}: {r.stderr.strip()[:200]}")
        time.sleep(1.0)
        return f"opened {name}"

    # --- accessibility
    def ax_available(self):
        import ApplicationServices as AS
        return (True, "macOS Accessibility (AX)") if AS.AXIsProcessTrusted() else (False, self.permissions()["accessibility"]["how"])

    def accessibility(self, win=None, max_nodes=400):
        import ApplicationServices as AS
        if not AS.AXIsProcessTrusted():
            return []
        win = win or self.focused_window()
        if not win:
            return []
        appref = AS.AXUIElementCreateApplication(win.pid)
        err, target = AS.AXUIElementCopyAttributeValue(appref, "AXFocusedWindow", None)
        if err or target is None:
            err, wins = AS.AXUIElementCopyAttributeValue(appref, "AXWindows", None)
            target = (wins or [None])[0]
        out: list[Element] = []
        if target is not None:
            _walk_ax(AS, target, out, 0, max_nodes)
        # the menu bar belongs to the app, not the window
        err, bar = AS.AXUIElementCopyAttributeValue(appref, "AXMenuBar", None)
        if not err and bar is not None and len(out) < max_nodes:
            _walk_ax(AS, bar, out, 0, max_nodes, max_depth=1)
        return out


_AX_ROLES = {"AXButton": "button", "AXTextField": "text field", "AXTextArea": "text area", "AXSecureTextField": "password field",
             "AXCheckBox": "check box", "AXRadioButton": "radio button", "AXPopUpButton": "pop-up button", "AXComboBox": "combo box",
             "AXMenuItem": "menu item", "AXMenuBarItem": "menu", "AXLink": "link", "AXTab": "tab", "AXRow": "row", "AXCell": "cell",
             "AXStaticText": "text", "AXImage": "image", "AXSlider": "slider", "AXMenuButton": "menu button",
             "AXDisclosureTriangle": "disclosure", "AXIncrementor": "stepper", "AXHeading": "heading", "AXToolbar": "toolbar",
             "AXSheet": "sheet", "AXDialog": "dialog", "AXOutline": "outline", "AXList": "list", "AXWebArea": "web area"}


def _ax_get(AS, el, attr):
    err, v = AS.AXUIElementCopyAttributeValue(el, attr, None)
    return None if err else v


def _ax_point(AS, v, kind):
    if v is None:
        return None
    ok, p = AS.AXValueGetValue(v, kind, None)
    return p if ok else None


def _walk_ax(AS, el, out, depth, limit, max_depth=30):
    if len(out) >= limit or depth > max_depth:
        return
    role = _ax_get(AS, el, "AXRole") or ""
    nice = _AX_ROLES.get(role)
    if nice:
        pos = _ax_point(AS, _ax_get(AS, el, "AXPosition"), AS.kAXValueCGPointType)
        size = _ax_point(AS, _ax_get(AS, el, "AXSize"), AS.kAXValueCGSizeType)
        if pos is not None and size is not None and size.width > 0 and size.height > 0:
            name = _ax_get(AS, el, "AXTitle") or _ax_get(AS, el, "AXDescription") or _ax_get(AS, el, "AXLabel") or ""
            secret = role == "AXSecureTextField"
            value = "" if secret else _ax_get(AS, el, "AXValue")
            value = value if isinstance(value, str) else ("" if value is None else str(value))
            if not name and role in ("AXStaticText",):
                name, value = value, ""
            enabled = _ax_get(AS, el, "AXEnabled")
            out.append(Element(nice, str(name)[:120], int(pos.x), int(pos.y), int(size.width), int(size.height),
                               value=value[:300], enabled=enabled is not False, secret=secret, depth=depth,
                               extra={"focused": True} if _ax_get(AS, el, "AXFocused") else {}))
    for c in (_ax_get(AS, el, "AXChildren") or [])[:300]:
        _walk_ax(AS, c, out, depth + 1, limit, max_depth)
