"""HTTP routes for coding-agent hand-off (cli_agents.py): detection, jobs, live progress, approvals."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import cli_agents
from .security import require_user

router = APIRouter(prefix="/api/cli-agents", dependencies=[Depends(require_user)])
_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        cli_agents.load_saved()
        _loaded = True


def _run(run_id: str) -> cli_agents.Run:
    _ensure_loaded()
    r = cli_agents.RUNS.get(run_id)
    if not r:
        raise HTTPException(404, "No such job")
    return r


@router.get("")
async def status(fresh: bool = False):
    """Which coding CLIs are installed and signed in on this computer. Never returns identity or tokens."""
    import asyncio

    found = await asyncio.gather(*(asyncio.to_thread(cli_agents.detect, a, fresh) for a in cli_agents.AGENTS))
    return {"agents": found, "modes": {k: v["label"] for k, v in cli_agents.MODES.items()}}


class RunIn(BaseModel):
    agent: str = Field(..., pattern="^(claude|codex)$")
    task: str = Field(..., min_length=1, max_length=20000)
    folder: str = Field(..., min_length=1, max_length=1000)
    mode: str = Field("ask", pattern="^(ask|edits|plan)$")
    source: str = Field("app", max_length=40)


@router.post("/runs")
async def start(data: RunIn):
    _ensure_loaded()
    info = cli_agents.detect(data.agent)
    if not info["installed"]:
        raise HTTPException(409, f"{info['name']} isn't installed on this computer. Install it with: {info['install']}")
    if not info["signed_in"]:
        raise HTTPException(409, f"{info['name']} isn't signed in yet. {info['login_hint']}")
    try:
        run = cli_agents.start(data.agent, data.task, data.folder, data.mode, data.source)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return run.full()


@router.get("/runs")
def runs():
    _ensure_loaded()
    return sorted((r.summary() for r in cli_agents.RUNS.values()), key=lambda r: -r["created_at"])[:100]


@router.get("/runs/{run_id}")
def get_run(run_id: str, after: int = 0):
    return _run(run_id).full(max(0, after))


class ApproveIn(BaseModel):
    rules: list[str] | None = None   # None = everything it asked for
    note: str = Field("", max_length=2000)


@router.post("/runs/{run_id}/approve")
async def approve(run_id: str, data: ApproveIn):
    try:
        cli_agents.approve(_run(run_id), data.rules, data.note)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/runs/{run_id}/deny")
def deny(run_id: str):
    try:
        cli_agents.deny(_run(run_id))
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


class FollowIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=20000)


@router.post("/runs/{run_id}/message")
async def message(run_id: str, data: FollowIn):
    try:
        cli_agents.follow_up(_run(run_id), data.text)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True}


@router.post("/runs/{run_id}/stop")
def stop(run_id: str):
    cli_agents.stop(_run(run_id))
    return {"ok": True}


@router.delete("/runs/{run_id}")
def delete(run_id: str):
    r = _run(run_id)
    if r.status in ("queued", "running"):
        raise HTTPException(409, "Stop the job first.")
    cli_agents.RUNS.pop(run_id, None)
    (cli_agents._runs_dir() / f"{run_id}.json").unlink(missing_ok=True)
    return {"ok": True}
