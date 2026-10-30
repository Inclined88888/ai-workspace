"""One recorder interface for "Show me how", whatever the person works in.

A recorder starts a session, hands back what the person did as events, and stops. Events are normalised to:

  {"kind": "navigate|click|type|select|check|submit|key|open_app|note", "url": "...", "app": "...",
   "target": {"role", "name", "label", "placeholder", "text", "tag"}, "value": "...", "secret": bool, "t": seconds}

Targets describe things by meaning (role and accessible name), never by screen position, so a replay still works after
the page or app moves things around. Password fields arrive with secret=true and no value.

Adapters:
  BrowserRecorder   Rooks Browser's recording API (POST/GET /api/recordings[/id[/stop]]), a visible browser window
  DeskRecorder      Rooks Desk. It does not expose a recording stream yet; the adapter speaks the same API so it works
                    the day /api/recordings lands there, and reports "not available yet" until then
  NarrationRecorder no capture: the person types or says the steps; always available
Any other source (a browser extension, the phone) can push events with POST /api/learn/recordings/{id}/events.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from .. import config

KINDS = {"navigate", "click", "type", "select", "check", "submit", "key", "open_app", "note"}
TARGET_KEYS = ("role", "name", "label", "placeholder", "text", "tag", "href", "type")


class NotAvailable(Exception):
    """The recorder cannot run here (add-on off, older version)."""


class Recorder(Protocol):
    surface: str

    async def start(self, start_url: str = "") -> str: ...
    async def events(self, remote_id: str) -> list[dict]: ...
    async def stop(self, remote_id: str) -> list[dict]: ...


def normalise(ev: dict, surface: str = "browser") -> dict | None:
    if not isinstance(ev, dict):
        return None
    kind = str(ev.get("kind") or ev.get("type") or "").lower()
    kind = {"input": "type", "change": "type", "fill": "type", "navigation": "navigate", "goto": "navigate",
            "launch": "open_app", "keypress": "key", "narration": "note"}.get(kind, kind)
    if kind not in KINDS:
        return None
    t = ev.get("target") if isinstance(ev.get("target"), dict) else {}
    target = {k: " ".join(str(t.get(k) or "").split())[:120] for k in TARGET_KEYS if t.get(k)}
    secret = bool(ev.get("secret") or str(t.get("type") or "").lower() == "password")
    value = "" if secret else " ".join(str(ev.get("value") or "").split())[:500]
    out = {"kind": kind, "target": target, "value": value, "secret": secret, "surface": surface,
           "t": round(float(ev.get("t") or 0), 2)}
    for k in ("url", "app", "title", "key", "text"):
        if ev.get(k):
            out[k] = str(ev[k])[:500]
    return out


def tidy(events: list[dict]) -> list[dict]:
    """Merge keystroke noise: consecutive typing into the same field keeps the last value; repeated navigations collapse."""
    out: list[dict] = []
    for e in events:
        if not e:
            continue
        last = out[-1] if out else None
        if last and e["kind"] == "type" and last["kind"] == "type" and last["target"] == e["target"]:
            out[-1] = e
            continue
        if last and e["kind"] == "navigate" and last["kind"] == "navigate":
            out[-1] = e
            continue
        if last and e["kind"] == "navigate" and last["kind"] in ("click", "submit") and e.get("url") == last.get("url"):
            continue
        out.append(e)
    return out


class _Http:
    surface = "browser"
    base = ""
    key = ""
    label = ""

    async def _call(self, method: str, path: str, json: dict | None = None) -> dict:
        if not self.base:
            raise NotAvailable(f"{self.label} is not set up on this computer.")
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.request(method, f"{self.base}{path}", json=json,
                                    headers={"Authorization": f"Bearer {self.key}"} if self.key else {})
        except httpx.HTTPError as e:
            raise NotAvailable(f"{self.label} is not running ({e.__class__.__name__}).") from e
        if r.status_code == 404:
            raise NotAvailable(f"This version of {self.label} cannot record yet. Update it, or describe the steps instead.")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail") or r.json().get("error") or r.text
            except ValueError:
                detail = r.text
            raise NotAvailable(f"{self.label}: {str(detail)[:300]}")
        return r.json()

    async def start(self, start_url: str = "") -> str:
        return str((await self._call("POST", "/api/recordings", {"start_url": start_url}))["id"])

    async def events(self, remote_id: str) -> list[dict]:
        return list((await self._call("GET", f"/api/recordings/{remote_id}")).get("events") or [])

    async def stop(self, remote_id: str) -> list[dict]:
        return list((await self._call("POST", f"/api/recordings/{remote_id}/stop", {})).get("events") or [])


class BrowserRecorder(_Http):
    surface = "browser"
    label = "Rooks Browser"

    def __init__(self):
        self.base, self.key = (config.BROWSER_URL or "").rstrip("/"), config.BROWSER_API_KEY or ""


class DeskRecorder(_Http):
    surface = "desktop"
    label = "Computer use (Rooks Desk)"

    def __init__(self):
        # config.DESK_URL arrives with feat/computer-use; until then this adapter is a stub that reports NotAvailable
        self.base, self.key = (getattr(config, "DESK_URL", "") or "").rstrip("/"), getattr(config, "DESK_API_KEY", "") or ""


class NarrationRecorder:
    surface = "narration"

    async def start(self, start_url: str = "") -> str:
        return ""

    async def events(self, remote_id: str) -> list[dict]:
        return []

    async def stop(self, remote_id: str) -> list[dict]:
        return []


FACTORIES = {"browser": BrowserRecorder, "desktop": DeskRecorder, "narration": NarrationRecorder}


def get(surface: str) -> Recorder:
    """Tests replace entries in FACTORIES with fakes."""
    f = FACTORIES.get(surface)
    if not f:
        raise NotAvailable(f"Unknown place to record: {surface}")
    return f()


def availability() -> dict:
    return {"browser": bool(config.BROWSER_URL), "desktop": bool(getattr(config, "DESK_URL", "")), "narration": True}
