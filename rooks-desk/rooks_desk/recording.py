"""Light recording for Workbench's "Show me how" on the desktop.

Rooks Desk does not hook the keyboard or mouse (that would capture passwords and needs extra OS permissions). Instead it
notes which app and window the person is working in, every time that changes, so the playbook knows the apps and the
order; the person's own narration ("then I export it as PDF") fills in the clicks. Window titles that look like a
password manager or a sign-in prompt are kept out.

  POST /api/recordings {"start_url"?}     -> {"id", "status": "recording", "events": [...], "captures": "apps_and_windows"}
  GET  /api/recordings/{id}               -> same, live
  POST /api/recordings/{id}/stop          -> same, final
"""

from __future__ import annotations

import re
import threading
import time
import uuid

SENSITIVE = re.compile(r"1password|bitwarden|keepass|lastpass|dashlane|keychain|password|sign[ -]?in|log[ -]?in|credential", re.I)
MAX_EVENTS = 2000


class Recording:
    def __init__(self, backend, interval: float = 0.7, start_url: str = ""):
        self.id = uuid.uuid4().hex[:12]
        self.backend, self.interval, self.start_url = backend, interval, start_url
        self.events: list[dict] = []
        self.status = "recording"
        self.error = ""
        self.t0 = time.time()
        self._stop = threading.Event()
        self._last: tuple[str, str] | None = None
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"desk-rec-{self.id}")
        self._thread.start()

    def _sample(self) -> None:
        try:
            w = self.backend.focused_window()
        except Exception as e:  # noqa: BLE001 - permission missing or no window system: report once, keep going
            if not self.error:
                self.error = str(e).splitlines()[0][:200] if str(e) else e.__class__.__name__
            return
        if not w:
            return
        app, title = (w.app or "").strip(), (w.title or "").strip()
        if (app, title) == self._last:
            return
        self._last = (app, title)
        if SENSITIVE.search(f"{app} {title}"):
            title = ""
        ev = {"kind": "open_app", "app": app[:200], "title": title[:300], "t": round(time.time() - self.t0, 2),
              "target": {"name": app[:120]}}
        if len(self.events) < MAX_EVENTS:
            self.events.append(ev)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self.interval)

    def stop(self) -> None:
        if self.status == "recording":
            self._stop.set()
            self._thread.join(5)
            self._sample()
            self.status = "stopped"

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "error": self.error, "start_url": self.start_url,
                "captures": "apps_and_windows", "events": list(self.events)}


class Recordings:
    def __init__(self, backend_getter, interval: float = 0.7):
        self.backend_getter, self.interval = backend_getter, interval
        self.items: dict[str, Recording] = {}

    def start(self, start_url: str = "") -> dict:
        for r in self.items.values():  # one at a time: a forgotten recording should not run forever
            r.stop()
        rec = Recording(self.backend_getter(), self.interval, start_url)
        self.items[rec.id] = rec
        return rec.to_dict()

    def get(self, rid: str) -> dict:
        return self.items[rid].to_dict()

    def stop(self, rid: str) -> dict:
        rec = self.items[rid]
        rec.stop()
        return rec.to_dict()
