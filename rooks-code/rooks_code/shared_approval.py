"""One approval, two places: the terminal prompt and the AI Workspace app's Approvals list.

When rooks is linked to an app conversation (provider workbench), every change it asks about is also posted to the app
(/v1/rooks/approvals) with its diff or command. The first answer wins: typed here, or clicked in the app (browser, app
window or phone). The other side is told, so the app's card closes or the terminal goes ahead. Each request has its
own id, so an earlier answer can never approve a later change.

Not linked, or the app unreachable: the plain local prompt, exactly as before.
"""

from __future__ import annotations

import json
import os
import select
import sys
import time
import urllib.error
import urllib.request

ANSWERS = {"y": "y", "yes": "y", "n": "n", "no": "n", "a": "a", "always": "a"}
POLL_S = 1.0
REMOTE_WAIT_S = 900  # no keyboard (piped input ran out): wait this long for an answer from the app, then deny


class Link:
    def __init__(self, base_url: str, api_key: str, conversation_id: int):
        self.base = base_url.rstrip("/")  # ends in /v1
        self.hdr = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "X-Workbench": "1"}
        self.cid = conversation_id

    @classmethod
    def of(cls, ui) -> "Link | None":
        if os.environ.get("ROOKS_SHARED_APPROVALS", "1") == "0":
            return None
        if getattr(ui, "conversation_id", None) and getattr(ui, "base_url", None) and getattr(ui, "api_key", None):
            return cls(ui.base_url, ui.api_key, ui.conversation_id)
        return None

    def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(f"{self.base}/rooks/approvals{path}", method=method, headers=self.hdr,
                                     data=json.dumps(body).encode() if body is not None else None)
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())

    def create(self, plan) -> int | None:
        try:
            return int(self._call("POST", "", {"conversation_id": self.cid, "tool": getattr(plan, "tool", "") or plan.effect, "effect": plan.effect,
                                                "summary": plan.summary, "preview": plan.preview or ""})["id"])
        except (OSError, ValueError, KeyError):
            return None

    def status(self, aid: int) -> str:
        try:
            return self._call("GET", f"/{aid}")["status"]
        except (OSError, ValueError, KeyError):
            return "pending"

    def answer(self, aid: int, approve: bool) -> str:
        """Record the local answer. Returns the status that stands (the app's, if it answered first)."""
        try:
            return self._call("POST", f"/{aid}", {"approve": approve})["status"]
        except urllib.error.HTTPError as e:
            if e.code == 409:
                return self.status(aid)
            return "approved" if approve else "denied"
        except (OSError, ValueError, KeyError):
            return "approved" if approve else "denied"


def _ready(timeout: float) -> bool:
    """A line can be read from stdin without blocking (POSIX). Elsewhere: always try to read."""
    if os.name != "posix":
        return True
    try:
        return bool(select.select([sys.stdin], [], [], timeout)[0])
    except (ValueError, OSError):  # stdin closed or not selectable
        return True


def ask(ui, plan, show_prompt, read_line) -> str:
    """Return 'y', 'n' or 'a'. show_prompt() prints the question; read_line() reads one line ('' at end of input)."""
    link = Link.of(ui)
    aid = link.create(plan) if link else None
    if aid:
        ui.p(ui.c("dim", f"  Also waiting in the app (Approvals #{aid}). Answer here or there."))
    show_prompt()
    if not aid or link is None:
        return _local(ui, show_prompt, read_line)
    keyboard = True
    deadline = None
    while True:
        if keyboard and _ready(POLL_S):
            line = read_line()
            if line == "":  # end of input: with the app linked, its answer still counts
                keyboard = False
                deadline = time.monotonic() + REMOTE_WAIT_S
                ui.p(ui.c("dim", "  No keyboard input: waiting for an answer in the app."))
                continue
            if line is None:
                continue
            a = ANSWERS.get(line.strip().lower())
            if not a:
                ui.p(ui.c("dim", "  Type y, n or a, then Enter."))
                show_prompt()
                continue
            stood = link.answer(aid, a in ("y", "a"))
            if stood == "denied" and a != "n":
                ui.p(ui.c("error", "  Already denied in the app."))
                return "n"
            if stood == "approved" and a == "n":
                ui.p(ui.c("ok", "  Already approved in the app."))
                return "y"
            return a
        if not keyboard:
            time.sleep(POLL_S)
            if deadline and time.monotonic() > deadline:
                link.answer(aid, False)
                ui.p(ui.c("error", "  No answer in time: denied."))
                return "n"
        st = link.status(aid)
        if st == "approved":
            ui.p(ui.c("ok", "  Approved in the app."))
            return "y"
        if st == "denied":
            ui.p(ui.c("error", "  Denied in the app."))
            return "n"


def _local(ui, show_prompt, read_line) -> str:
    while True:
        line = read_line()
        if line == "":
            ui.p("n (no input: denied)")
            return "n"
        a = ANSWERS.get(line.strip().lower())
        if a:
            return a
        ui.p(ui.c("dim", "  Type y, n or a, then Enter."))
        show_prompt()
