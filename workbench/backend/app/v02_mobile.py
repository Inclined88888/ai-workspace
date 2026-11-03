"""feat/v02-core on the phone: projects, history with undo, and checked answers over the device-token API (/api/mobile/*).

Thin wrappers around the desktop routes in projects.py, history.py and checked.py, so the phone gets the same rules:
the free-plan limit, Pro gates (402), and the extra confirmation before undoing anything outside the app (409).
"""
from fastapi import APIRouter, Depends, Query

from . import checked, history, projects
from .mobile import require_device

device = APIRouter(prefix="/api/mobile", dependencies=[Depends(require_device), Depends(projects.ensure), Depends(history.ensure),
                                                       Depends(checked.ensure)])


@device.get("/projects")
def m_projects():
    return projects.api_list(False)


@device.get("/projects/{pid}")
def m_project(pid: int):
    return projects.api_detail(pid)


@device.put("/projects/active")
def m_active(data: projects.ActiveIn):
    return projects.set_active(data.project_id)


@device.get("/projects/for-conversation/{cid}")
def m_for_conv(cid: int):
    return {"project": projects.project_for_conversation(cid)}


@device.put("/projects/for-conversation/{cid}")
def m_move_conv(cid: int, data: projects.ActiveIn):
    return projects.move_conversation(cid, data.project_id)


@device.get("/history")
def m_history(limit: int = Query(60, ge=1, le=200), before: str | None = None, category: str = "", q: str = ""):
    return history.api_timeline(limit, before, category, q, False, None)


@device.post("/history/{entry_id}/undo")
def m_undo(entry_id: str, data: history.UndoIn):
    return history.api_undo(entry_id, data)


@device.get("/checked/mode")
def m_mode(conversation_id: int | None = None):
    return checked.api_mode(conversation_id)


@device.put("/checked/mode")
def m_set_mode(data: checked.ModeIn):
    return checked.api_set_mode(data)


@device.post("/checked/check")
async def m_check(data: checked.CheckIn):
    return await checked.api_check(data)
