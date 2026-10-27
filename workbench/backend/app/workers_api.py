"""API for workers (workers.py): bounded, role-scoped pieces of a mission."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from . import workers
from .assistant_api import require_user_or_cli

router = APIRouter(prefix="/api", dependencies=[Depends(require_user_or_cli)])


class WorkerIn(BaseModel):
    role: str = Field(..., max_length=20)
    goal: str = Field(..., min_length=5, max_length=2000)
    inputs: str = Field("", max_length=20000)
    expected: str = Field("", max_length=2000)
    acceptance: str = Field("", max_length=2000)
    tools: list[str] | None = Field(None, max_length=40)
    scope: str | None = Field(None, pattern="^(read|draft|write)$")
    model: str = Field("", max_length=300)
    budget_cents: float = Field(0, ge=0, le=100_000)
    budget_minutes: int = Field(10, ge=1, le=120)
    max_attempts: int = Field(2, ge=1, le=5)
    after: list[int] = Field(default_factory=list, max_length=20)
    needs_review: bool | None = None


@router.get("/workers/roles")
def worker_roles():
    return {"roles": workers.roles(), "parallel": workers.parallel(), "max_parallel": workers.MAX_PARALLEL}


class ParallelIn(BaseModel):
    parallel: int = Field(..., ge=1, le=workers.MAX_PARALLEL)


@router.put("/workers/settings")
def worker_settings(data: ParallelIn):
    return {"parallel": workers.set_parallel(data.parallel)}


@router.get("/missions/{mid}/workers")
def mission_workers(mid: int):
    return {"workers": workers.listing(mid), "budget": workers.budget(mid)}


@router.post("/missions/{mid}/workers")
def add_worker(mid: int, data: WorkerIn):
    return workers.create(mid, **data.model_dump())


@router.get("/workers/{tid}")
def worker(tid: int):
    return workers.view(tid)


class NoteIn(BaseModel):
    note: str = Field("", max_length=1000)


@router.post("/workers/{tid}/cancel")
def worker_cancel(tid: int):
    return workers.cancel(tid)


@router.post("/workers/{tid}/retry")
def worker_retry(tid: int, data: NoteIn):
    return workers.retry(tid, data.note)


@router.post("/workers/{tid}/accept")
def worker_accept(tid: int, data: NoteIn):
    return workers.accept(tid, data.note)


@router.post("/workers/{tid}/start")
def worker_start(tid: int):
    return workers.start(tid)


class LimitsIn(BaseModel):
    budget_cents: float | None = Field(None, ge=0, le=100_000)
    budget_minutes: int | None = Field(None, ge=1, le=120)


@router.patch("/workers/{tid}")
def worker_limits(tid: int, data: LimitsIn):
    return workers.update(tid, data.budget_cents, data.budget_minutes)
