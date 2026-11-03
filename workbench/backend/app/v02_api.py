"""HTTP API for automations, the jobs board and live documents (feat/v02-automations).

Owner routes under /api (signed-in user) and phone routes under /api/mobile (paired device token).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import automations, jobs, livedocs
from .mobile import require_device
from .security import require_user

router = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
phone = APIRouter(prefix="/api/mobile")


# --- automations -----------------------------------------------------------------------------------------------------------
class ParseIn(BaseModel):
    text: str = Field(..., max_length=2000)


class AutomationIn(BaseModel):
    name: str = Field("", max_length=120)
    request: str = Field("", max_length=2000)
    trigger_kind: str
    trigger: dict[str, Any] = {}
    action_kind: str = "task"
    action: dict[str, Any] = {}
    ask_first: bool = True


class AutomationPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    ask_first: bool | None = None
    trigger_kind: str | None = None
    trigger: dict[str, Any] | None = None
    action_kind: str | None = None
    action: dict[str, Any] | None = None


class ShareIn(BaseModel):
    text: str = Field("", max_length=20000)
    url: str = Field("", max_length=4000)
    title: str = Field("", max_length=300)
    file_id: int | None = None


@router.get("/automations")
def list_automations():
    return automations.listing()


@router.post("/automations/parse")
def parse_automation(body: ParseIn):
    return automations.parse(body.text)


@router.post("/automations")
def create_automation(body: AutomationIn):
    return automations.create(body.model_dump())


@router.patch("/automations/{aid}")
def patch_automation(aid: int, body: AutomationPatch):
    return automations.update(aid, body.model_dump(exclude_none=True))


@router.delete("/automations/{aid}")
def delete_automation(aid: int):
    return automations.delete(aid)


@router.post("/automations/{aid}/test")
async def test_automation(aid: int):
    return await automations.test_run(aid)


@router.post("/automations/check")
async def check_now():
    return {"fired": await automations.check_all(force=True)}


@router.post("/automations/share")
def share_desktop(body: ShareIn):
    return automations.share(body.text, body.url, body.title, body.file_id)


# --- jobs board ------------------------------------------------------------------------------------------------------------
class JobIn(BaseModel):
    kind: str = "chat"
    prompt: str = Field(..., max_length=20000)
    title: str = Field("", max_length=120)


@router.get("/jobs")
def board():
    return jobs.board()


@router.post("/jobs")
def new_job(body: JobIn):
    return jobs.create(body.kind, body.prompt, body.title)


@router.get("/jobs/notices")
def job_notices(after: int = 0):
    return jobs.notices(after)


@router.delete("/jobs/done")
def clear_done():
    return {"cleared": jobs.clear_done()}


@router.get("/jobs/{jid}")
def job(jid: int):
    return jobs.get(jid)


def _control(jid: int, action: str) -> dict:
    if action == "pause":
        return jobs.pause(jid)
    if action == "resume":
        return jobs.resume(jid)
    if action == "stop":
        return jobs.stop(jid)
    if action == "retry":
        return jobs.retry(jid)
    if action in ("start", "deny"):
        return jobs.approve_start(jid, action == "start")
    raise HTTPException(404, "Unknown job action")


@router.post("/jobs/{jid}/{action}")
def job_action(jid: int, action: str):
    return _control(jid, action)


# --- live documents --------------------------------------------------------------------------------------------------------
class ExportIn(BaseModel):
    kind: str
    format: str
    content: Any
    title: str = Field("", max_length=200)
    values: list[list[Any]] | None = None
    png: str = Field("", max_length=15_000_000)


class ReviseIn(BaseModel):
    kind: str
    content: Any
    instruction: str = Field(..., max_length=2000)
    model: str | None = None


@router.post("/live/export")
def live_export(body: ExportIn):
    data, name, mime = livedocs.export(body.kind, body.format, body.content, body.title, body.values, body.png)
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/live/revise")
async def live_revise(body: ReviseIn):
    return await livedocs.revise(body.kind, body.content, body.instruction, body.model)


# --- the phone -------------------------------------------------------------------------------------------------------------
@phone.get("/jobs")
def phone_board(_: dict = Depends(require_device)):
    return jobs.board(limit_done=15)


@phone.post("/jobs/{jid}/{action}")
def phone_job_action(jid: int, action: str, _: dict = Depends(require_device)):
    if action not in ("pause", "resume", "stop", "start", "deny"):
        raise HTTPException(404, "Unknown job action")
    return _control(jid, action)


@phone.post("/share")
def phone_share(body: ShareIn, _: dict = Depends(require_device)):
    """The phone's Share box: anything shared fires the matching 'when I share from my phone' automations."""
    return automations.share(body.text, body.url, body.title, body.file_id)
