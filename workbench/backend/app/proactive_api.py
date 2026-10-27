"""API for proactive check-ins (checkins.py) and ROOKS reaching out by voice (calls.py)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import attention, calls, checkins
from .assistant_api import require_user_or_cli

router = APIRouter(prefix="/api", dependencies=[Depends(require_user_or_cli)])


class TopicIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    cron: str = Field(..., max_length=100)
    sources: list[str] = Field(..., min_length=1, max_length=10)
    description: str = Field("", max_length=1000)
    project_id: int | None = None
    importance: str = Field("normal", pattern="^(low|normal|high|urgent)$")
    notify: str = Field("inbox", pattern="^(inbox|telegram|call|phone)$")
    quiet_start: str = Field("", max_length=5)
    quiet_end: str = Field("", max_length=5)
    expires_at: str | None = Field(None, max_length=40)
    budget_cents: int = Field(0, ge=0, le=100_000)


class TopicPatch(BaseModel):
    name: str | None = Field(None, max_length=100)
    cron: str | None = Field(None, max_length=100)
    sources: list[str] | None = Field(None, max_length=10)
    description: str | None = Field(None, max_length=1000)
    project_id: int | None = None
    importance: str | None = Field(None, pattern="^(low|normal|high|urgent)$")
    notify: str | None = Field(None, pattern="^(inbox|telegram|call|phone)$")
    quiet_start: str | None = Field(None, max_length=5)
    quiet_end: str | None = Field(None, max_length=5)
    expires_at: str | None = Field(None, max_length=40)
    enabled: bool | None = None
    budget_cents: int | None = Field(None, ge=0, le=100_000)


@router.get("/checkins")
def list_checkins():
    return {"topics": checkins.listing(), "presets": checkins.PRESETS, "limits": checkins.limits(), "sources": list(checkins.SOURCE_KINDS)}


@router.post("/checkins")
def create_checkin(data: TopicIn):
    return checkins.create(**data.model_dump())


@router.patch("/checkins/{tid}")
def edit_checkin(tid: int, data: TopicPatch):
    return checkins.update(tid, **data.model_dump(exclude_unset=True))


@router.delete("/checkins/{tid}")
def delete_checkin(tid: int):
    checkins.delete(tid)
    return {"ok": True}


@router.post("/checkins/{tid}/run")
async def run_checkin(tid: int):
    """Check now (a manual run: its own slot, so it never stands in for a scheduled one)."""
    return await checkins.check(tid)


@router.post("/checkins/{tid}/cancel")
def cancel_checkin(tid: int):
    from . import db

    db.run("UPDATE topics SET cancel = 1 WHERE id = ?", tid)
    return {"ok": True}


@router.get("/checkins/{tid}/runs")
def checkin_runs(tid: int):
    from . import db

    return {"runs": checkins.runs(tid), "deliveries": db.all("SELECT id, channel, title, status, deliver_after, error, created_at, sent_at "
                                                              "FROM deliveries WHERE topic_id = ? ORDER BY id DESC LIMIT 50", tid)}


class LimitsIn(BaseModel):
    per_topic_day: int | None = Field(None, ge=0, le=100)
    per_day: int | None = Field(None, ge=0, le=100)
    quiet_start: str | None = Field(None, max_length=5)
    quiet_end: str | None = Field(None, max_length=5)


@router.put("/checkins/limits")
def checkin_limits(data: LimitsIn):
    return checkins.set_limits(data.model_dump(exclude_none=True))


# Reaching out by voice
@router.get("/calls")
def calls_status():
    return {**calls.status(), "pending": calls.pending()}


@router.post("/calls/invites/{iid}/accept")
def accept_invite(iid: int):
    return calls.accept(iid)


@router.post("/calls/invites/{iid}/decline")
def decline_invite(iid: int):
    return calls.decline(iid)


class SnoozeIn(BaseModel):
    minutes: int = Field(30, ge=5, le=240)


@router.post("/calls/invites/{iid}/snooze")
def snooze_invite(iid: int, data: SnoozeIn):
    return calls.snooze(iid, data.minutes)


class PolicyIn(BaseModel):
    window_start: str | None = Field(None, max_length=5)
    window_end: str | None = Field(None, max_length=5)
    weekend_start: str | None = Field(None, max_length=5)
    weekend_end: str | None = Field(None, max_length=5)
    invites_per_day: int | None = Field(None, ge=0, le=50)
    invite_minutes: int | None = Field(None, ge=5, le=240)
    phone_per_day: int | None = Field(None, ge=0, le=10)
    phone_per_week: int | None = Field(None, ge=0, le=50)
    phone_month_cents: int | None = Field(None, ge=0, le=100_000)


@router.put("/calls/policy")
def contact_policy(data: PolicyIn):
    return calls.set_policy(data.model_dump(exclude_none=True))


class PhoneIn(BaseModel):
    password: str = Field(..., max_length=200)
    provider: str | None = Field(None, pattern="^(|test|twilio)$")
    number: str | None = Field(None, max_length=30)
    enabled: bool | None = None
    twilio_sid: str | None = Field(None, max_length=40)
    twilio_token: str | None = Field(None, max_length=100)
    twilio_from: str | None = Field(None, max_length=30)


@router.put("/calls/phone")
def phone_settings(data: PhoneIn):
    return calls.configure_phone(**data.model_dump())


class VerifyStart(BaseModel):
    password: str = Field(..., max_length=200)
    confirm_cost: bool = False


@router.post("/calls/phone/verify")
async def phone_verify(data: VerifyStart):
    return await calls.start_verification(data.password, data.confirm_cost)


class VerifyCode(BaseModel):
    code: str = Field(..., max_length=12)


@router.post("/calls/phone/confirm")
def phone_confirm(data: VerifyCode):
    return calls.confirm_verification(data.code)


# Needs You (attention.py)
@router.get("/needs-you")
def needs_you(include_deferred: bool = False):
    return attention.items(include_deferred)


@router.get("/needs-you/count")
def needs_you_count():
    return {"count": attention.count()}


class DeferIn(BaseModel):
    id: str = Field(..., max_length=80)
    hours: float = Field(..., gt=0, le=24 * 14)


@router.post("/needs-you/defer")
def needs_you_defer(data: DeferIn):
    return attention.defer(data.id, data.hours)


# What ROOKS can do (capabilities.py)
@router.get("/capabilities")
def capabilities_catalog():
    from . import capabilities

    return capabilities.catalog()
