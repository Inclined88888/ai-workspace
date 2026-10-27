"""HTTP routes for Computer Use.

/api/computer/*   for you, in the browser (signed-in session or CLI token)
/api/companion/*  for ROOKS Companion only, authenticated by the device token from pairing. The companion connects
                  out to ROOKS and asks for work; nothing on your computer listens for connections.
"""

import asyncio
import ipaddress

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import computer, db, security
from .assistant_api import require_user_or_cli

router = APIRouter(prefix="/api/computer", dependencies=[Depends(require_user_or_cli)])
companion = APIRouter(prefix="/api/companion")


# For you
@router.get("")
def status():
    from . import vision

    s = computer.settings()
    approvals = db.all("SELECT id, tool, args, reason, created_at FROM approvals WHERE status = 'pending' AND (tool LIKE 'computer_%' "
                       "OR tool LIKE 'browser_%' OR tool LIKE 'files_%') ORDER BY id")
    from .assistant import approval_view

    approvals = [approval_view({**a, "status": "pending"}) for a in approvals]  # you see what you are approving
    current = db.one("SELECT id, kind, status, created_at FROM computer_actions WHERE status IN ('queued', 'dispatched') ORDER BY id LIMIT 1")
    return {"settings": s, "devices": computer.devices(), "grants": computer.grants(), "approvals": approvals,
            "current": {**current, "summary": next((h["summary"] for h in computer.history(20) if h["id"] == current["id"]), "")} if current else None,
            "hard_blocked": sorted(computer.HARD_BLOCKED_APPS), "remote_allowed": db.get_setting("computer_remote", "0") == "1",
            "vision": vision.settings(), "screen_shared": vision.last_share()}


class SettingsIn(BaseModel):
    enabled: bool | None = None
    mode: str | None = Field(None, pattern="^(read_only|ask|allowed)$")
    app_rules: dict[str, str] | None = None
    screenshots: bool | None = None
    coordinates: bool | None = None


@router.put("/settings")
def put_settings(data: SettingsIn):
    return computer.update_settings(**data.model_dump())


@router.post("/pair")
def pair_start():
    return computer.start_pairing()


@router.delete("/devices/{did}")
def remove_device(did: int):
    computer.revoke(did)
    return {"ok": True}


@router.post("/stop")
def stop():
    return computer.stop()


@router.post("/allow")
def allow():
    return computer.allow_again()


@router.post("/take-control")
def take_control():
    return computer.take_control()


@router.post("/hand-back")
def hand_back():
    return computer.hand_back()


class GrantIn(BaseModel):
    app: str = Field(..., min_length=1, max_length=80)
    minutes: int = Field(30, ge=1, le=480)


@router.post("/grants")
def add_grant(data: GrantIn):
    return computer.grant(data.app, data.minutes)


@router.delete("/grants/{gid}")
def del_grant(gid: int):
    computer.revoke_grant(gid)
    return {"ok": True}


@router.get("/history")
def history(limit: int = Query(100, ge=1, le=500)):
    return computer.history(limit)


@router.get("/screenshot/{did}")
def screenshot(did: int):
    return {"image": computer.screenshot(did)}


# For the companion
def _local_only(request: Request) -> None:
    """Remote companions are off unless you turned them on: only this machine and private networks may connect."""
    if db.get_setting("computer_remote", "0") == "1":
        return
    host = request.client.host if request.client else ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if host != "testclient" and not (ip and (ip.is_loopback or ip.is_private)):
        raise HTTPException(403, "Companions may only connect from this computer or your private network.")


def device(request: Request) -> dict:
    _local_only(request)
    auth = request.headers.get("authorization", "")
    return computer.device_for(auth[7:] if auth.lower().startswith("bearer ") else "")


class PairIn(BaseModel):
    code: str = Field(..., min_length=6, max_length=40)
    name: str = Field("", max_length=80)
    info: dict = Field(default_factory=dict)


@companion.post("/pair")
def pair(data: PairIn, request: Request):
    _local_only(request)
    security.throttle("pair:" + (request.client.host if request.client else "?"))
    return computer.pair(data.code, data.name, data.info)


@companion.post("/hello")
def hello(info: dict, d: dict = Depends(device)):
    return computer.hello(d, info)


@companion.post("/next")
async def next_action(d: dict = Depends(device)):
    """Long poll: returns an action as soon as one is queued, or an empty answer after about 20 seconds."""
    for _ in range(80):
        a = computer.next_action(d)
        if a:
            return {"action": a, **computer.state(d)}
        st = computer.state(d)
        if st["stop"] or st["cancel"]:
            return st
        await asyncio.sleep(0.25)
    return computer.state(d)


@companion.post("/result")
def result(r: dict, d: dict = Depends(device)):
    if not isinstance(r.get("id"), int):
        raise HTTPException(400, "result needs the action id")
    computer.report(d, r)
    return computer.state(d)


@companion.get("/state")
def state(d: dict = Depends(device)):
    return computer.state(d)


# File changes ROOKS made on the paired computer (versions are kept on that computer)
@router.get("/files/history")
async def file_history(path: str = ""):
    r = await computer.request("files_history", {"path": path} if path else {}, timeout=20)
    if not r["ok"]:
        raise HTTPException(400, r["error"] or "Could not read the history")
    return r["result"]


class UndoIn(BaseModel):
    change: str = Field(..., min_length=2, max_length=40)


@router.post("/files/undo")
async def file_undo(data: UndoIn):
    """You pressed Undo: that is your authorization for this one change."""
    r = await computer.request("files_undo", {"change": data.change}, approved=True, timeout=30)
    if not r["ok"]:
        raise HTTPException(400, r["error"] or "Undo failed")
    return r["result"]


# Visual targeting (vision.py)
class VisionIn(BaseModel):
    model: str | None = Field(None, max_length=300)
    mode: str | None = Field(None, pattern="^(off|ask|session)$")
    local_only: bool | None = None
    consent: bool = False
    session_minutes: int | None = Field(None, ge=5, le=480)


@router.put("/vision")
def put_vision(data: VisionIn):
    from . import vision

    return vision.configure(data.model, data.mode, data.local_only, data.consent, data.session_minutes)


@router.post("/vision/check")
async def check_vision():
    from . import vision

    s = vision.settings()
    if not s["model"]:
        raise HTTPException(400, "Choose a vision model first.")
    return {"check": await vision.check(s["model"]), "settings": vision.settings()}
