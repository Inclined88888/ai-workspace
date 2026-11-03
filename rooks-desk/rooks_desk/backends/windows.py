"""Windows: pynput (SendInput) for input, mss for screenshots, user32 for the window list, UI Automation (the
`uiautomation` package) for element trees.

The process is made per-monitor DPI aware first, so screenshots and mouse coordinates are the same physical pixels.
UAC: Windows does not let a normal program click or type into an elevated (Run as administrator) window or the UAC
prompt itself, which appears on the secure desktop. Rooks Desk says so and hands that step to you."""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
from ctypes import wintypes

from ._pynput import PynputInput
from .base import Backend, BackendUnavailable, Element, Window

UAC_NOTE = ("Windows blocks programs from clicking or typing into windows that run as administrator and into UAC "
            "prompts (they appear on a separate secure desktop). Approve those yourself; Rooks Desk waits.")


def _dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


class WindowsBackend(Backend):
    name = "windows"
    platform = "win32"

    def __init__(self):
        _dpi_aware()
        self.user32 = ctypes.windll.user32
        self.input = PynputInput()

    def permissions(self):
        return {"screen": {"ok": True, "how": ""}, "input": {"ok": True, "how": UAC_NOTE},
                "accessibility": {"ok": self.ax_available()[0], "how": "pip install uiautomation"}}

    # --- input
    def cursor(self):
        return self.input.cursor()

    def move(self, x, y):
        self.input.move(x, y)

    def click(self, x, y, button="left", count=1):
        self.input.click(x, y, button, count)

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4):
        self.input.drag(x1, y1, x2, y2, button, duration)

    def scroll(self, x, y, dx=0, dy=-3):
        self.input.scroll(x, y, dx, dy)

    def type_text(self, text, interval=0.0):
        self.input.type_text(text, interval)

    def hotkey(self, keys):
        self.input.hotkey(keys)

    # --- windows
    def windows(self):
        u = self.user32
        out: list[Window] = []
        fg = u.GetForegroundWindow()
        EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            if not u.IsWindowVisible(hwnd) or u.IsIconic(hwnd):
                return True
            n = u.GetWindowTextLengthW(hwnd)
            if n == 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            r = wintypes.RECT()
            u.GetWindowRect(hwnd, ctypes.byref(r))
            pid = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if r.right - r.left < 40 or r.bottom - r.top < 30:
                return True
            out.append(Window(str(int(hwnd)), buf.value, _exe_name(pid.value), pid.value, r.left, r.top, r.right - r.left,
                              r.bottom - r.top, hwnd == fg))
            return True

        u.EnumWindows(EnumProc(cb), 0)
        out.sort(key=lambda w: not w.focused)
        return out

    def focus_window(self, win):
        u = self.user32
        hwnd = int(win.id)
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, 9)  # SW_RESTORE
        # Windows only lets the foreground process change focus; an Alt tap makes us eligible
        u.keybd_event(0x12, 0, 0, 0)
        u.keybd_event(0x12, 0, 2, 0)
        if not u.SetForegroundWindow(hwnd):
            raise BackendUnavailable("Windows refused to bring that window forward (it may be running as administrator). " + UAC_NOTE)
        time.sleep(0.3)

    def open_app(self, name, args=None):
        try:
            if args:
                subprocess.Popen([name, *args], creationflags=0x00000008)  # DETACHED_PROCESS
            else:
                os.startfile(name)  # type: ignore[attr-defined]  # apps, documents, App Paths names (e.g. "notepad", "acad")
        except OSError:
            r = subprocess.run(["powershell", "-NoProfile", "-Command", f"Start-Process -FilePath '{name}'"], capture_output=True, text=True)
            if r.returncode != 0:
                raise ValueError(f"could not open {name!r}: {r.stderr.strip()[:200]}") from None
        time.sleep(1.0)
        return f"opened {name}"

    # --- accessibility
    def ax_available(self):
        try:
            import uiautomation  # noqa: F401
            return True, "Windows UI Automation"
        except Exception:  # noqa: BLE001
            return False, "uiautomation is not installed; element lists are off, screenshots still work"

    def accessibility(self, win=None, max_nodes=400):
        try:
            import uiautomation as uia
        except Exception:  # noqa: BLE001
            return []
        win = win or self.focused_window()
        if not win:
            return []
        out: list[Element] = []
        try:
            root = uia.ControlFromHandle(int(win.id))
        except Exception:  # noqa: BLE001
            return []
        _walk_uia(root, out, 0, max_nodes)
        return out


_UIA_KEEP = {"ButtonControl", "EditControl", "CheckBoxControl", "RadioButtonControl", "ComboBoxControl", "MenuItemControl",
             "HyperlinkControl", "TabItemControl", "ListItemControl", "TreeItemControl", "DataItemControl", "TextControl",
             "DocumentControl", "SplitButtonControl", "SliderControl", "SpinnerControl", "MenuBarControl", "ToolBarControl",
             "HeaderItemControl", "ImageControl"}


def _walk_uia(ctrl, out, depth, limit):
    if len(out) >= limit or depth > 30:
        return
    try:
        kind = ctrl.ControlTypeName
        if kind in _UIA_KEEP and not ctrl.IsOffscreen:
            r = ctrl.BoundingRectangle
            if r.width() > 0 and r.height() > 0:
                secret = bool(getattr(ctrl, "IsPassword", False))
                value = ""
                if kind in ("EditControl", "DocumentControl", "ComboBoxControl") and not secret:
                    try:
                        value = ctrl.GetValuePattern().Value or ""
                    except Exception:  # noqa: BLE001
                        value = ""
                out.append(Element(kind.replace("Control", "").lower(), (ctrl.Name or "")[:120], r.left, r.top, r.width(), r.height(),
                                   value=value[:300], enabled=bool(ctrl.IsEnabled), secret=secret, depth=depth,
                                   extra={"focused": True} if getattr(ctrl, "HasKeyboardFocus", False) else {}))
        children = ctrl.GetChildren()
    except Exception:  # noqa: BLE001
        return
    for c in children[:300]:
        _walk_uia(c, out, depth + 1, limit)


def _exe_name(pid: int) -> str:
    try:
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(520)
        size = wintypes.DWORD(520)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        return os.path.splitext(os.path.basename(buf.value))[0] if ok else ""
    except Exception:  # noqa: BLE001
        return ""
