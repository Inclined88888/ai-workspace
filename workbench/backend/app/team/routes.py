"""HTTP API for the team lead (feat/team-lead): /api/team/* for the desktop, /api/mobile/team/* for the paired phone."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import features_v02
from ..mobile import require_device
from ..security import require_user
from . import connect, engine, outbox, parse, store

router = APIRouter(prefix="/api/team", dependencies=[Depends(require_user)])
phone = APIRouter(prefix="/api/mobile/team", dependencies=[Depends(require_device)])
PRIVACY = ("Your team's details stay on this computer. Members only ever see their own tasks. Nothing on their phones or "
           "computers is tracked: no location, no screens, no apps. Updates come only from what they send you.")


def _lead() -> None:
    """Managing other people's tasks is a Team/Business feature; your own 'My tasks' list is free."""
    features_v02.require("team_tasks")


class MemberIn(BaseModel):
    name: str | None = Field(None, max_length=120)
    role: str | None = Field(None, max_length=120)
    skills: Any = None
    contact: dict[str, str] | None = None
    channel: str | None = None
    hours_per_week: float | None = None
    work_days: list[str] | None = None
    away: list[dict] | None = None
    has_app: bool | None = None
    notes: str | None = Field(None, max_length=2000)
    active: bool | None = None


class TaskIn(BaseModel):
    title: str | None = Field(None, max_length=300)
    notes: str | None = Field(None, max_length=10000)
    assignee_id: int | None = None
    project_id: int | None = None
    project: str | None = None
    status: str | None = None
    percent: int | None = None
    priority: int | None = None
    start: str | None = None
    due: str | None = None
    estimate_hours: float | None = None
    checklist: list[dict] | None = None
    files: list[int] | None = None
    skills: Any = None
    mine: bool | None = None
    notify: bool = True


class TextIn(BaseModel):
    text: str = Field(..., max_length=4000)


class InboundIn(BaseModel):
    sender: str = Field(..., max_length=320)
    text: str = Field(..., max_length=10000)
    source: str = "email"


class SettingsIn(BaseModel):
    auto_notify: bool | None = None
    auto_nudge: bool | None = None
    auto_assign: bool | None = None
    digest: str | None = None
    stuck_days: int | None = None


def _settings() -> dict:
    return {"auto_notify": store.setting("auto_notify") == "1", "auto_nudge": store.setting("auto_nudge") == "1",
            "auto_assign": store.setting("auto_assign") == "1", "digest": store.setting("digest", "daily"),
            "stuck_days": int(store.setting("stuck_days", "3")), "privacy": PRIVACY,
            "team_unlocked": features_v02.allowed("team_tasks")}


@router.get("")
def overview():
    return {**engine.board(), "settings": _settings(), "suggestions": engine.suggestions(), "outbox": outbox.listing()}


@router.get("/settings")
def get_settings():
    return _settings()


@router.put("/settings")
def put_settings(body: SettingsIn):
    for k in ("auto_notify", "auto_nudge", "auto_assign"):
        v = getattr(body, k)
        if v is not None:
            if v:
                _lead()
            store.set_setting(k, "1" if v else "0")
    if body.digest in ("daily", "weekly", "off"):
        store.set_setting("digest", body.digest)
    if body.stuck_days:
        store.set_setting("stuck_days", str(max(1, min(30, body.stuck_days))))
    return _settings()


# members
@router.get("/members")
def list_members():
    return [{**m, "load": engine.workload(m)} for m in store.members()]


@router.post("/members")
def add_member(body: MemberIn):
    _lead()
    return store.add_member(body.model_dump(exclude_none=True))


@router.get("/members/{mid}")
def person(mid: int):
    m = store.member(mid)
    return {**m, "load": engine.workload(m), "tasks": store.tasks(assignee_id=mid), "updates": store.updates(member_id=mid, limit=30),
            "working_today": engine.works_on(m, store.today())}


@router.patch("/members/{mid}")
def patch_member(mid: int, body: MemberIn):
    return store.update_member(mid, body.model_dump(exclude_none=True))


@router.delete("/members/{mid}")
def delete_member(mid: int):
    return store.remove_member(mid)


@router.post("/members/{mid}/link")
def make_link(mid: int):
    _lead()
    return connect.member_link(mid)


# tasks
@router.get("/tasks")
def list_tasks(member_id: int | None = None, project_id: int | None = None, mine: bool | None = None, include_done: bool = True):
    return [{**t, "risk": engine.risk(t)} for t in store.tasks(assignee_id=member_id, project_id=project_id, mine=mine,
                                                                 include_done=include_done)]


@router.post("/tasks")
def add_task(body: TaskIn):
    data = body.model_dump(exclude_none=True)
    notify = data.pop("notify", True)
    if data.get("assignee_id"):
        _lead()
    t = store.add_task(data)
    out: dict = {"task": t}
    if t["assignee_id"] and notify:
        out["message"] = outbox.assignment(t["id"])
    elif not t["mine"] and store.members():
        s = engine.suggest_assignee(t)
        if s:
            out["suggestion"] = engine.add_suggestion("assign", t["id"], s["member"]["id"], s["reason"])
    return out


@router.post("/tasks/parse")
def parse_task(body: TextIn):
    p = parse.assignment(body.text)
    m = p.pop("member")
    p["member_id"], p["member_name"] = (m["id"], m["name"]) if m else (None, "")
    if not m and store.members():
        s = engine.suggest_assignee({"title": p["title"], "notes": "", "project": p["project"], "skills": [], "due": p["due"]})
        p["suggested"] = {"member_id": s["member"]["id"], "name": s["member"]["name"], "reason": s["reason"]} if s else None
    return p


@router.get("/tasks/{tid}")
def get_task(tid: int):
    t = store.task(tid)
    return {**t, "risk": engine.risk(t), "updates": store.updates(tid)}


@router.patch("/tasks/{tid}")
def patch_task(tid: int, body: TaskIn):
    data = body.model_dump(exclude_none=True)
    notify = data.pop("notify", True)
    old = store.task(tid)
    if data.get("assignee_id"):
        _lead()
    t = store.update_task(tid, data)
    out: dict = {"task": t}
    if t["assignee_id"] and t["assignee_id"] != old["assignee_id"] and notify:
        out["message"] = outbox.assignment(tid)
    if any(k in data for k in ("status", "percent")):
        store.add_update(tid, "leader", status=data.get("status", ""), percent=data.get("percent"))
        if t["status"] == "done" and old["status"] != "done" and t["assignee_id"]:
            out["next"] = engine.next_for(t["assignee_id"])
    return out


@router.delete("/tasks/{tid}")
def delete_task(tid: int):
    return store.delete_task(tid)


@router.get("/tasks/{tid}/suggest")
def suggest(tid: int):
    s = engine.suggest_assignee(store.task(tid))
    if not s:
        raise HTTPException(404, "Add your team first.")
    return {"member_id": s["member"]["id"], "name": s["member"]["name"], "reason": s["reason"], "others": s["others"]}


# suggestions, outbox, tracking
@router.post("/suggestions/{sid}/{decision}")
def decide(sid: int, decision: str):
    if decision not in ("approve", "dismiss"):
        raise HTTPException(404, "Unknown decision")
    if decision == "approve":
        _lead()
    return engine.decide(sid, decision == "approve")


@router.get("/outbox")
def list_outbox(status: str = "waiting"):
    return outbox.listing(status)


class ApproveIn(BaseModel):
    body: str | None = Field(None, max_length=5000)


@router.post("/outbox/{oid}/approve")
def approve(oid: int, body: ApproveIn | None = None):
    _lead()
    return outbox.approve(oid, body.body if body else None)


@router.post("/outbox/{oid}/cancel")
def cancel(oid: int):
    return outbox.cancel(oid)


@router.post("/check")
def check_now():
    return engine.check()


@router.post("/inbound")
def inbound(body: InboundIn):
    return connect.inbound(body.sender, body.text, body.source if body.source in ("email", "sms", "telegram", "slack") else "email")


@router.get("/plan")
def plan():
    return engine.today_plan()


@router.post("/plan/send")
def send_plan():
    """Queue each working person's plan for today. Plans always wait for the lead's OK in the outbox."""
    _lead()
    out = []
    for p in engine.today_plan()["people"]:
        if p["message"]:
            o = outbox.queue(p["member"]["id"], "plan", "Your plan for today", p["message"])
            out.append(o)
    return {"queued": out}


@router.get("/digest")
def digest(period: str = "daily"):
    return engine.digest("weekly" if period == "weekly" else "daily")


@router.get("/notices")
def notices(after: int = 0, unseen: bool = False):
    return store.notices(after, unseen)


@router.post("/notices/seen")
def seen():
    store.mark_seen()
    return {"ok": True}


@router.post("/sync")
def sync():
    return {**connect.push_tasks(), "pulled": connect.pull_updates()}


# phone: the lead's board and approvals, and the lead's own tasks
@phone.get("")
def phone_overview():
    return overview()


@phone.post("/suggestions/{sid}/{decision}")
def phone_decide(sid: int, decision: str):
    return decide(sid, decision)


@phone.post("/outbox/{oid}/approve")
def phone_approve(oid: int):
    return approve(oid)


@phone.patch("/tasks/{tid}")
def phone_patch(tid: int, body: TaskIn):
    return patch_task(tid, body)


@phone.get("/notices")
def phone_notices(after: int = 0):
    return store.notices(after)
