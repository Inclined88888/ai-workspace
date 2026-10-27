"""Projects API: create, edit, archive, bin, restore, delete for good (with the name typed), search, overview, notes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import missions, projects
from .assistant_api import require_user_or_cli

router = APIRouter(prefix="/api/projects", dependencies=[Depends(require_user_or_cli)])


class ProjectIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=4000)
    goals: str = Field("", max_length=4000)
    instructions: str = Field("", max_length=8000)
    model: str = Field("", max_length=300)
    budget_cents: int = Field(0, ge=0, le=10_000_000)
    local_only: bool = False
    isolated: bool = False
    permissions: dict[str, str] = Field(default_factory=dict)


class ProjectPatch(BaseModel):
    name: str | None = Field(None, max_length=120)
    description: str | None = Field(None, max_length=4000)
    goals: str | None = Field(None, max_length=4000)
    instructions: str | None = Field(None, max_length=8000)
    model: str | None = Field(None, max_length=300)
    budget_cents: int | None = Field(None, ge=0, le=10_000_000)
    local_only: bool | None = None
    isolated: bool | None = None
    permissions: dict[str, str] | None = None


class NoteIn(BaseModel):
    kind: str = Field(..., pattern="^(decision|question)$")
    text: str = Field(..., min_length=1, max_length=2000)


class ResolveIn(BaseModel):
    answer: str = Field("", max_length=2000)


class PurgeIn(BaseModel):
    confirm: str = Field(..., max_length=120)


@router.get("")
def list_projects(q: str = "", archived: bool = False, bin: bool = False):
    return projects.listing(q, archived, bin)


@router.post("")
def create_project(data: ProjectIn):
    return projects.create(**data.model_dump())


@router.get("/{pid}")
def project(pid: int):
    return projects.overview(pid)


@router.patch("/{pid}")
def edit_project(pid: int, data: ProjectPatch):
    return projects.update(pid, **data.model_dump(exclude_none=True))


@router.post("/{pid}/archive")
def archive(pid: int):
    return projects.set_status(pid, "archived")


@router.post("/{pid}/restore")
def restore(pid: int):
    return projects.set_status(pid, "active")


@router.delete("/{pid}")
def to_bin(pid: int):
    """Moves the project to the bin. Everything linked stays as it is and comes back with Restore."""
    if any(m["status"] in missions.ACTIVE for m in missions.listing(pid)):
        raise HTTPException(409, "A mission in this project is still running. Pause or cancel it first.")
    return projects.set_status(pid, "deleted")


@router.post("/{pid}/purge")
def purge(pid: int, data: PurgeIn):
    return projects.purge(pid, data.confirm)


@router.post("/{pid}/notes")
def add_note(pid: int, data: NoteIn):
    return projects.note(pid, data.kind, data.text)


@router.post("/notes/{nid}/resolve")
def resolve_note(nid: int, data: ResolveIn):
    return projects.resolve(nid, data.answer)
