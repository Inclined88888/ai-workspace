"""An in-memory desktop for tests, CI and `--dry-run` demos: a text editor, a mail app with a Send button, a shop with
a Buy button and a password manager, drawn into real screenshots so the whole loop (screenshot -> model -> action ->
verify) runs without a display. Every input event is recorded in `events`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .base import Backend, Element, Monitor, Window, normalise_keys


@dataclass
class FakeWidget:
    role: str
    name: str
    x: int
    y: int
    w: int
    h: int
    value: str = ""
    secret: bool = False
    on_click: str = ""  # action name handled by FakeBackend._press


@dataclass
class FakeApp:
    name: str
    title: str
    x: int
    y: int
    w: int
    h: int
    widgets: list[FakeWidget] = field(default_factory=list)
    pid: int = 0


class FakeBackend(Backend):
    name = "fake"
    platform = "fake"

    def __init__(self, width: int = 1600, height: int = 1000, save_dir: Path | None = None):
        self.width, self.height = width, height
        self.events: list[tuple] = []
        self.apps: dict[str, FakeApp] = {}
        self.order: list[str] = []  # z-order, last is focused
        self.focus_widget: FakeWidget | None = None
        self.pos = (0, 0)
        self.save_dir = save_dir
        self.saved: dict[str, str] = {}
        self.sent: list[dict] = []
        self.bought = 0
        self._next_pid = 100
        self.installed = {"Text Editor", "Mail", "Shop", "Passwords", "Terminal"}

    # --- screen
    def monitors(self):
        return [Monitor(0, 0, 0, self.width, self.height), Monitor(1, 0, 0, self.width, self.height, True)]

    def screenshot(self, monitor: int = 1):
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (self.width, self.height), (236, 232, 224))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, self.width, 28], fill=(30, 30, 30))
        d.text((10, 8), "Fake desktop", fill=(255, 255, 255))
        for name in self.order:
            a = self.apps[name]
            focused = name == self.order[-1]
            d.rectangle([a.x, a.y, a.x + a.w, a.y + a.h], fill=(255, 255, 255), outline=(180, 30, 50) if focused else (120, 120, 120), width=2)
            d.rectangle([a.x, a.y, a.x + a.w, a.y + 26], fill=(60, 60, 70))
            d.text((a.x + 8, a.y + 7), a.title, fill=(255, 255, 255))
            for wd in a.widgets:
                fill = (245, 245, 245) if wd.role in ("text field", "text area") else (210, 220, 240)
                d.rectangle([wd.x, wd.y, wd.x + wd.w, wd.y + wd.h], fill=fill, outline=(90, 90, 90))
                label = ("•" * len(wd.value)) if wd.secret else (wd.value or wd.name)
                for i, line in enumerate(str(label).splitlines()[:12] or [""]):
                    d.text((wd.x + 6, wd.y + 6 + 14 * i), line[:90], fill=(20, 20, 20))
        return img

    def cursor(self):
        return self.pos

    # --- input
    def move(self, x, y):
        self.pos = (x, y)
        self.events.append(("move", x, y))

    def click(self, x, y, button="left", count=1):
        self.pos = (x, y)
        self.events.append(("click", x, y, button, count))
        for name in reversed(self.order):
            a = self.apps[name]
            if a.x <= x <= a.x + a.w and a.y <= y <= a.y + a.h:
                if name != self.order[-1]:
                    self.order.remove(name)
                    self.order.append(name)
                for wd in a.widgets:
                    if wd.x <= x <= wd.x + wd.w and wd.y <= y <= wd.y + wd.h:
                        self.focus_widget = wd if wd.role in ("text field", "text area") else self.focus_widget
                        if wd.on_click:
                            self._press(a, wd)
                        return
                return

    def drag(self, x1, y1, x2, y2, button="left", duration=0.4):
        self.events.append(("drag", x1, y1, x2, y2))
        self.pos = (x2, y2)

    def scroll(self, x, y, dx=0, dy=-3):
        self.events.append(("scroll", x, y, dx, dy))

    def type_text(self, text, interval=0.0):
        self.events.append(("type", text))
        if self.focus_widget is not None:
            self.focus_widget.value += text

    def hotkey(self, keys):
        keys = normalise_keys(keys)
        self.events.append(("hotkey", "+".join(keys)))
        if keys[-1] == "s" and ("ctrl" in keys or "cmd" in keys) and self.order and self.order[-1] == "Text Editor":
            self._save(self.apps["Text Editor"])
        if keys == ["enter"] and self.focus_widget is not None and self.focus_widget.role == "text area":
            self.focus_widget.value += "\n"

    # --- apps
    def windows(self):
        out = []
        for name in self.order:
            a = self.apps[name]
            out.append(Window(f"w{a.pid}", a.title, a.name, a.pid, a.x, a.y, a.w, a.h, name == self.order[-1]))
        return list(reversed(out))

    def focus_window(self, win):
        self.events.append(("focus", win.app))
        if win.app in self.order:
            self.order.remove(win.app)
            self.order.append(win.app)

    def open_app(self, name, args=None):
        match = next((n for n in self.installed if n.lower() == name.strip().lower() or name.strip().lower() in n.lower()), None)
        if not match:
            raise ValueError(f"no app called {name!r} (installed: {', '.join(sorted(self.installed))})")
        self.events.append(("open", match))
        if match not in self.apps:
            self._next_pid += 1
            self.apps[match] = self._build(match, self._next_pid)
        if match in self.order:
            self.order.remove(match)
        self.order.append(match)
        self.focus_widget = next((w for w in self.apps[match].widgets if w.role == "text area"), None)
        return f"opened {match}"

    def _build(self, name, pid):
        x, y = 120 + 40 * len(self.apps), 80 + 30 * len(self.apps)
        if name == "Text Editor":
            return FakeApp(name, "Untitled - Text Editor", x, y, 900, 600, [
                FakeWidget("text area", "Document", x + 10, y + 40, 880, 460),
                FakeWidget("text field", "File name", x + 10, y + 520, 600, 30, value="untitled.txt"),
                FakeWidget("button", "Save", x + 630, y + 520, 120, 30, on_click="save")], pid)
        if name == "Mail":
            return FakeApp(name, "New message - Mail", x, y, 800, 500, [
                FakeWidget("text field", "To", x + 10, y + 40, 600, 30),
                FakeWidget("text field", "Subject", x + 10, y + 80, 600, 30),
                FakeWidget("text area", "Message", x + 10, y + 120, 780, 300),
                FakeWidget("button", "Send", x + 10, y + 440, 120, 34, on_click="send")], pid)
        if name == "Shop":
            return FakeApp(name, "Cart - Shop", x, y, 700, 400, [
                FakeWidget("static text", "USB-C hub  $24.99", x + 10, y + 40, 400, 30),
                FakeWidget("button", "Place order", x + 10, y + 100, 160, 34, on_click="buy")], pid)
        if name == "Passwords":
            return FakeApp(name, "Sign in - Passwords", x, y, 600, 300, [
                FakeWidget("text field", "Password", x + 10, y + 60, 400, 30, secret=True),
                FakeWidget("button", "Sign in", x + 10, y + 110, 120, 34, on_click="login")], pid)
        return FakeApp(name, f"{name}", x, y, 800, 500, [FakeWidget("text area", "Terminal", x + 10, y + 40, 780, 440)], pid)

    def _press(self, app, wd):
        self.events.append(("press", app.name, wd.name))
        if wd.on_click == "save":
            self._save(app)
        elif wd.on_click == "send":
            vals = {w.name: w.value for w in app.widgets}
            self.sent.append({"to": vals.get("To", ""), "subject": vals.get("Subject", ""), "body": vals.get("Message", "")})
        elif wd.on_click == "buy":
            self.bought += 1

    def _save(self, app):
        vals = {w.name: w.value for w in app.widgets}
        fname = vals.get("File name") or "untitled.txt"
        self.saved[fname] = vals.get("Document", "")
        app.title = f"{fname} - Text Editor"
        if self.save_dir:
            Path(self.save_dir).mkdir(parents=True, exist_ok=True)
            (Path(self.save_dir) / fname).write_text(vals.get("Document", ""), encoding="utf-8")

    # --- accessibility
    def accessibility(self, win=None, max_nodes=400):
        if not self.order:
            return []
        name = win.app if win else self.order[-1]
        a = self.apps.get(name)
        if not a:
            return []
        return [Element(w.role, w.name, w.x, w.y, w.w, w.h, value="" if w.secret else w.value, secret=w.secret,
                        extra={"focused": True} if w is self.focus_widget else {}) for w in a.widgets]

    def ax_available(self):
        return True, "fake accessibility tree"

    def permissions(self):
        return {"screen": {"ok": True, "how": ""}, "input": {"ok": True, "how": ""}, "accessibility": {"ok": True, "how": ""}}
