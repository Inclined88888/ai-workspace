"""What every desktop backend offers. Coordinates are global screen coordinates in the units the OS uses for input
(points on macOS, physical pixels on Windows once the process is DPI aware, pixels on X11); screenshots may be larger
(Retina), so callers map image pixels to screen units through the monitor's size, never assume 1:1."""

from __future__ import annotations

import io
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from PIL import Image


class BackendUnavailable(RuntimeError):
    """The OS refused (missing permission, no display) or a library is missing. The message says how to fix it."""


@dataclass
class Monitor:
    index: int  # 1..N like mss; 0 is the whole virtual screen
    left: int
    top: int
    width: int
    height: int
    primary: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Window:
    id: str
    title: str
    app: str
    pid: int = 0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    focused: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Element:
    """One accessibility element (button, text field, menu item, ...), in global screen coordinates."""
    role: str
    name: str
    x: int
    y: int
    width: int
    height: int
    value: str = ""
    enabled: bool = True
    secret: bool = False  # password field: the agent never types into it
    depth: int = 0
    extra: dict = field(default_factory=dict)
    id: int = 0  # assigned per snapshot by the agent

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.width // 2, self.y + self.height // 2

    def contains(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        return d


KEY_ALIASES = {
    "control": "ctrl", "ctl": "ctrl", "option": "alt", "opt": "alt", "command": "cmd", "meta": "cmd", "super": "cmd",
    "win": "cmd", "windows": "cmd", "return": "enter", "escape": "esc", "del": "delete", "bksp": "backspace",
    "pgup": "pageup", "pgdn": "pagedown", "page_up": "pageup", "page_down": "pagedown", "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right", "spacebar": "space", "ins": "insert",
}
NAMED_KEYS = {"ctrl", "alt", "shift", "cmd", "enter", "tab", "esc", "backspace", "delete", "up", "down", "left", "right",
              "home", "end", "pageup", "pagedown", "space", "insert", "capslock", "printscreen",
              *(f"f{i}" for i in range(1, 25))}


def normalise_keys(keys) -> list[str]:
    """'ctrl+s', ['Ctrl','S'], 'cmd shift t' -> ['ctrl', 's'], ... Unknown names longer than one character raise."""
    if isinstance(keys, str):
        parts = [p for p in keys.replace("+", " ").split() if p] if len(keys) > 1 else [keys]
    else:
        parts = [str(k) for k in keys]
    out = []
    for p in parts:
        k = p.strip().lower()
        k = KEY_ALIASES.get(k, k)
        if len(k) > 1 and k not in NAMED_KEYS:
            raise ValueError(f"unknown key {p!r}")
        out.append(k)
    if not out:
        raise ValueError("no keys given")
    return out


class Backend:
    """Override what the OS supports; the defaults raise BackendUnavailable with a clear message."""

    name = "base"
    platform = ""

    # --- screen -------------------------------------------------------------------------------------------------
    def monitors(self) -> list[Monitor]:
        import mss
        with mss.mss() as s:
            mons = s.monitors
            return [Monitor(i, m["left"], m["top"], m["width"], m["height"], i == 1) for i, m in enumerate(mons)]

    def screenshot(self, monitor: int = 1) -> "Image.Image":
        import mss
        from PIL import Image
        try:
            with mss.mss() as s:
                mons = s.monitors
                if monitor >= len(mons):
                    raise ValueError(f"there is no monitor {monitor} (this computer has {len(mons) - 1})")
                raw = s.grab(mons[monitor])
                return Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        except mss.exception.ScreenShotError as e:  # type: ignore[attr-defined]
            raise BackendUnavailable(f"could not take a screenshot: {e}. {self.permission_hint('screen')}") from e

    def cursor(self) -> tuple[int, int]:
        raise BackendUnavailable("cursor position is not available on this backend")

    # --- input --------------------------------------------------------------------------------------------------
    def move(self, x: int, y: int) -> None:
        raise BackendUnavailable("mouse control is not available on this backend")

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        raise BackendUnavailable("mouse control is not available on this backend")

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left", duration: float = 0.4) -> None:
        raise BackendUnavailable("mouse control is not available on this backend")

    def scroll(self, x: int, y: int, dx: int = 0, dy: int = -3) -> None:
        """dy < 0 scrolls down (content moves up), in wheel notches."""
        raise BackendUnavailable("scrolling is not available on this backend")

    def type_text(self, text: str, interval: float = 0.0) -> None:
        raise BackendUnavailable("typing is not available on this backend")

    def hotkey(self, keys: list[str]) -> None:
        raise BackendUnavailable("keyboard shortcuts are not available on this backend")

    # --- apps and windows ---------------------------------------------------------------------------------------
    def windows(self) -> list[Window]:
        return []

    def focus_window(self, win: Window) -> None:
        raise BackendUnavailable("focusing windows is not available on this backend")

    def open_app(self, name: str, args: list[str] | None = None) -> str:
        raise BackendUnavailable("opening apps is not available on this backend")

    def focused_window(self) -> Window | None:
        return next((w for w in self.windows() if w.focused), None)

    # --- accessibility ------------------------------------------------------------------------------------------
    def accessibility(self, win: Window | None = None, max_nodes: int = 400) -> list[Element]:
        """Elements of one window (the focused one by default). Empty when the platform or app has no tree."""
        return []

    def ax_available(self) -> tuple[bool, str]:
        return False, "no accessibility tree on this backend"

    # --- permissions --------------------------------------------------------------------------------------------
    def permissions(self) -> dict[str, dict]:
        """{"screen": {"ok": bool, "how": "..."}, "input": {...}, "accessibility": {...}}"""
        return {}

    def request_permissions(self) -> None:
        """Trigger the OS prompts where the OS has them (macOS)."""

    def permission_hint(self, what: str) -> str:
        return ""


def to_png(img: "Image.Image") -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=False)
    return buf.getvalue()
