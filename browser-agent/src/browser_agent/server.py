"""HTTP service around the browser agent: job queue, live steps and screenshots, an OpenAPI tool for
Open WebUI (/tools) and an MCP endpoint (/mcp) for coding agents (OpenCode, Claude Code, Paperclip employees).

Run:  BROWSER_API_KEY=... uvicorn browser_agent.server:app --host 0.0.0.0 --port 8100
Every route except /healthz needs `Authorization: Bearer $BROWSER_API_KEY`.
"""

from __future__ import annotations

import asyncio
import base64
import calendar
import hmac
import ipaddress
import json
import os
import socket
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import actions
from .agent import StepEvent, run_task
from .llm import PROVIDERS, available, resolve_provider, workbench_required

API_KEY = os.getenv("BROWSER_API_KEY", "")
DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
MAX_CONCURRENT = int(os.getenv("BROWSER_MAX_CONCURRENT", "2"))
MAX_STEPS_LIMIT = int(os.getenv("BROWSER_MAX_STEPS_LIMIT", "50"))
TOOL_TIMEOUT_S = int(os.getenv("BROWSER_TOOL_TIMEOUT_S", "600"))
# Spend guard: total agent steps allowed per UTC calendar month across all callers (0 = unlimited).
# Browser runs started by Chat or Paperclip employees bill this service's model key, not theirs.
MONTHLY_STEP_LIMIT = int(os.getenv("BROWSER_MONTHLY_STEP_LIMIT", "2000"))

if len(API_KEY) < 24:
    raise RuntimeError("BROWSER_API_KEY must be set (24+ characters)")

_sem = asyncio.Semaphore(MAX_CONCURRENT)
_running: dict[str, asyncio.Task] = {}


# Storage
def _db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DATA_DIR / "jobs.db", timeout=30, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute(
        "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, goal TEXT, provider TEXT, model TEXT, status TEXT, "
        "max_steps INTEGER, allowed_domains TEXT, steps TEXT DEFAULT '[]', result TEXT, error TEXT, source TEXT, "
        "created_at REAL, updated_at REAL)"
    )
    return c


def _row(job_id: str) -> dict:
    with _db() as c:
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "Job not found")
    d = dict(r)
    d["steps"] = json.loads(d["steps"] or "[]")
    d["result"] = json.loads(d["result"]) if d["result"] else None
    d["allowed_domains"] = json.loads(d["allowed_domains"] or "[]")
    d["has_screenshot"] = (DATA_DIR / "shots" / f"{job_id}.png").exists()
    return d


def _update(job_id: str, **fields) -> None:
    fields["updated_at"] = time.time()
    with _db() as c:
        c.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", (*fields.values(), job_id))


# Jobs
class JobIn(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000, description="What the agent should accomplish, in plain English")
    provider: str | None = Field(None, description="anthropic, gemini, openai, openrouter, ollama, openai-compatible or auto")
    model: str | None = None
    max_steps: int = Field(25, ge=1, le=200)
    allowed_domains: list[str] = Field(default_factory=list, description="Optional, e.g. ['*.pcpartpicker.com']")


async def _execute(job_id: str, data: JobIn) -> None:
    steps: list[dict] = []
    shots = DATA_DIR / "shots"
    shots.mkdir(parents=True, exist_ok=True)

    def on_step(e: StepEvent) -> None:
        if e.screenshot:
            try:
                (shots / f"{job_id}.png").write_bytes(base64.b64decode(e.screenshot))
            except ValueError:
                pass
        d = e.to_dict()
        d.pop("screenshot", None)
        steps.append(d)
        _update(job_id, steps=json.dumps(steps))

    async with _sem:
        _update(job_id, status="running")
        try:
            result = await run_task(
                data.goal, provider=data.provider, model=data.model, max_steps=min(data.max_steps, MAX_STEPS_LIMIT),
                allowed_domains=data.allowed_domains or None, on_step=on_step, include_screenshots=True,
            )
            _update(job_id, status="done", result=json.dumps(result.to_dict()))
        except asyncio.CancelledError:
            _update(job_id, status="cancelled")
            raise
        except Exception as e:  # noqa: BLE001 - surface every failure on the job
            _update(job_id, status="error", error=str(e) or e.__class__.__name__)
        finally:
            _running.pop(job_id, None)


def month_usage() -> dict:
    """Steps used this UTC month (finished jobs count their real steps, running ones their cap)."""
    start = calendar.timegm(time.strptime(time.strftime("%Y-%m-01", time.gmtime()), "%Y-%m-%d"))
    used = 0
    with _db() as c:
        for r in c.execute("SELECT status, max_steps, steps, result FROM jobs WHERE created_at >= ?", (start,)):
            if r["status"] in ("queued", "running"):
                used += r["max_steps"]
            elif r["result"]:
                used += int(json.loads(r["result"]).get("steps") or 0)
            else:
                used += len(json.loads(r["steps"] or "[]"))
    return {"used": used, "limit": MONTHLY_STEP_LIMIT}


def start_job(data: JobIn, source: str = "api") -> dict:
    if MONTHLY_STEP_LIMIT:
        usage = month_usage()
        if usage["used"] + min(data.max_steps, MAX_STEPS_LIMIT) > MONTHLY_STEP_LIMIT:
            raise HTTPException(429, f"Monthly browser step budget reached ({usage['used']}/{MONTHLY_STEP_LIMIT} steps). "
                                     "Raise BROWSER_MONTHLY_STEP_LIMIT or wait for next month.")
    if data.provider not in (None, "", "auto") and data.provider not in PROVIDERS:
        raise HTTPException(400, f"Unknown provider {data.provider}")
    try:
        provider = resolve_provider(data.provider)
    except ValueError as e:  # e.g. R1: a direct provider requested inside the stack
        raise HTTPException(400, str(e)) from e
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    with _db() as c:
        c.execute(
            "INSERT INTO jobs(id, goal, provider, model, status, max_steps, allowed_domains, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)",
            (job_id, data.goal, provider, data.model or "", data.max_steps, json.dumps(data.allowed_domains), source, now, now),
        )
    _running[job_id] = asyncio.get_running_loop().create_task(_execute(job_id, data.model_copy(update={"provider": provider})))
    return _row(job_id)


async def wait_job(job_id: str, timeout: float) -> dict:
    task = _running.get(job_id)
    if task:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except asyncio.TimeoutError:
            pass
    return _row(job_id)


def _public_url(url: str) -> str:
    """One-off page reads from the service may only reach public hosts."""
    if "://" not in url:
        url = f"https://{url}"
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "Only http(s) URLs are allowed")
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise HTTPException(400, f"Cannot resolve {parsed.hostname}") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(400, "Internal and private network addresses are blocked")
    return url


def _summary(job: dict) -> dict:
    r = job.get("result") or {}
    return {
        "job_id": job["id"], "status": job["status"], "success": r.get("success"), "answer": r.get("final"),
        "urls_visited": (r.get("urls") or [])[-10:], "steps": r.get("steps", len(job["steps"])), "error": job.get("error"),
    }


# MCP (for OpenCode, Claude Code and other MCP clients)
def _build_mcp():
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.server.transport_security import TransportSecuritySettings

    mcp = MCPServer(name="browser", instructions="Control a real web browser. Use browse_web for multi step goals, read_page to read one URL.")

    @mcp.tool(description="Give a browser agent a goal in plain English (e.g. 'find three remote software engineer roles on weworkremotely.com'). Returns its answer and the pages it visited. Takes 30 seconds to several minutes.")
    async def browse_web(goal: str, max_steps: int = 20, allowed_domains: list[str] | None = None) -> dict:
        try:
            job = start_job(JobIn(goal=goal, max_steps=max_steps, allowed_domains=allowed_domains or []), source="mcp")
        except HTTPException as e:
            raise ToolError(str(e.detail)) from e
        return _summary(await wait_job(job["id"], TOOL_TIMEOUT_S))

    @mcp.tool(description="Open one public URL and return its visible text (no AI, fast).")
    async def read_page(url: str, selector: str | None = None) -> str:
        # Agents need the reason (blocked host, missing element, timeout), not a generic failure
        try:
            return await actions.page_text(_public_url(url), selector=selector, max_chars=30000)
        except HTTPException as e:
            raise ToolError(str(e.detail)) from e
        except Exception as e:  # noqa: BLE001
            raise ToolError(str(e).splitlines()[0][:500]) from e

    app = mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return mcp, app


mcp_server, mcp_app = _build_mcp()


@asynccontextmanager
async def lifespan(_: FastAPI):
    with _db() as c:
        c.execute("UPDATE jobs SET status = 'error', error = 'Interrupted by a restart' WHERE status IN ('queued', 'running')")
    async with mcp_server.session_manager.run():
        yield
    for t in list(_running.values()):
        t.cancel()


app = FastAPI(title="Browser agent", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def auth(request: Request, call_next):
    if request.url.path not in ("/healthz",):
        header = request.headers.get("authorization", "")
        token = header[7:] if header.lower().startswith("bearer ") else ""
        if not token or not hmac.compare_digest(token, API_KEY):
            return JSONResponse({"detail": "Invalid or missing bearer token"}, status_code=401)
    return await call_next(request)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/api/providers")
def providers():
    have = set(available())
    return {
        "auto": next(iter(available()), "") if workbench_required() else resolve_provider(None),
        "usage": month_usage(),
        "providers": [{"name": n, "label": p.label, "ready": n in have, "default_model": p.default_model, "notes": p.notes}
                      for n, p in PROVIDERS.items() if not workbench_required() or n == "workbench"],
        "workbench_required": workbench_required(),
    }


@app.post("/api/jobs")
async def create_job(data: JobIn):
    return start_job(data)


@app.get("/api/jobs")
def list_jobs(limit: int = 50):
    with _db() as c:
        rows = c.execute("SELECT id, goal, provider, model, status, source, created_at, updated_at FROM jobs ORDER BY created_at DESC LIMIT ?",
                         (min(limit, 200),)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return _row(job_id)


@app.get("/api/jobs/{job_id}/screenshot")
def job_screenshot(job_id: str):
    path = DATA_DIR / "shots" / f"{job_id.replace('/', '')}.png"
    if not path.exists():
        raise HTTPException(404, "No screenshot yet")
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    task = _running.get(job_id)
    if task:
        task.cancel()
    return _row(job_id)


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    if job_id in _running:
        _running[job_id].cancel()
    with _db() as c:
        c.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    (DATA_DIR / "shots" / f"{job_id.replace('/', '')}.png").unlink(missing_ok=True)
    return {"ok": True}


class ShotIn(BaseModel):
    url: str
    full_page: bool = False


@app.post("/api/actions/screenshot")
async def action_screenshot(data: ShotIn):
    out = DATA_DIR / "shots" / f"oneoff-{uuid.uuid4().hex[:8]}.png"
    info = await actions.screenshot(_public_url(data.url), str(out), full_page=data.full_page)
    return FileResponse(out, media_type="image/png", headers={"X-Page-Title": info.title[:200].encode("ascii", "ignore").decode()})


# OpenAPI tools for Open WebUI (mounted at /tools, spec at /tools/openapi.json)
tools = FastAPI(title="Browser", version="0.1.0", description="Let the assistant use a real web browser.")


class BrowseIn(BaseModel):
    goal: str = Field(..., description="What to accomplish on the web, in plain English, including which site if known")
    max_steps: int = Field(20, ge=1, le=50, description="Upper bound on browser steps")


@tools.post("/browse", operation_id="browse_web",
            summary="Use a browser agent to accomplish a goal on the web (multi step: search, click, fill forms, compare). Slow: 30s to several minutes.")
async def tool_browse(data: BrowseIn) -> dict:
    job = start_job(JobIn(goal=data.goal, max_steps=data.max_steps), source="chat")
    return _summary(await wait_job(job["id"], TOOL_TIMEOUT_S))


@tools.get("/read", operation_id="read_page", summary="Open one public URL and return its visible text. Fast, no AI.")
async def tool_read(url: str, selector: str | None = None) -> dict:
    return {"url": url, "text": await actions.page_text(_public_url(url), selector=selector, max_chars=30000)}


app.mount("/tools", tools)
app.mount("/", mcp_app)  # serves /mcp; must stay last so the routes above win


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "0.0.0.0"), port=int(os.getenv("PORT", "8100")))
