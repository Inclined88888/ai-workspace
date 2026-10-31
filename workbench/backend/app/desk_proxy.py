"""Proxy to Rooks Desk (computer use) so the Workbench "Computer" page can start, watch, approve and stop desktop tasks.

Rooks Desk runs on the user's own computer (the desktop app starts `rooks-desk serve` on loopback). Every sensitive step
it takes waits for an approve/deny answered here; Stop halts everything at once, like the kill-switch hotkey."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from . import config
from .security import require_user

router = APIRouter(prefix="/api/desk", dependencies=[Depends(require_user)])


def enabled() -> bool:
    return bool(config.DESK_URL and config.DESK_API_KEY)


async def call(method: str, path: str, json: dict | None = None, params: dict | None = None, timeout: float = 30) -> httpx.Response:
    if not enabled():
        raise HTTPException(503, "Computer use is off. The one-command installer adds Rooks Desk (unless --no-desk was used); then open the desktop app.")
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.request(method, f"{config.DESK_URL}{path}", json=json, params=params,
                                headers={"Authorization": f"Bearer {config.DESK_API_KEY}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Rooks Desk unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    return r


def _png(r: httpx.Response, cache: str) -> Response:
    return Response(r.content, media_type="image/png", headers={"Cache-Control": cache})


@router.get("/status")
async def status():
    return (await call("GET", "/api/status")).json()


@router.post("/stop")
async def stop():
    """Stop every running and queued task now (same as the kill-switch hotkey)."""
    return (await call("POST", "/api/stop")).json()


@router.get("/settings")
async def settings():
    return (await call("GET", "/api/settings")).json()


@router.put("/settings")
async def save_settings(request: Request):
    return (await call("PUT", "/api/settings", json=await request.json())).json()


@router.get("/providers")
async def providers():
    return (await call("GET", "/api/providers")).json()


@router.get("/screen")
async def screen():
    return _png(await call("GET", "/api/screen"), "no-store")


@router.get("/jobs")
async def jobs():
    return (await call("GET", "/api/jobs")).json()


@router.post("/jobs")
async def create_job(request: Request):
    body = await request.json()
    body.setdefault("provider", "workbench")  # think with the app's own models, through the /v1 bridge and the router
    body["source"] = "workbench"
    return (await call("POST", "/api/jobs", json=body)).json()


@router.get("/jobs/{job_id}")
async def job(job_id: str):
    return (await call("GET", f"/api/jobs/{job_id}")).json()


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    return (await call("POST", f"/api/jobs/{job_id}/cancel")).json()


@router.delete("/jobs/{job_id}")
async def delete(job_id: str):
    return (await call("DELETE", f"/api/jobs/{job_id}")).json()


@router.get("/jobs/{job_id}/receipt")
async def receipt(job_id: str):
    return (await call("GET", f"/api/jobs/{job_id}/receipt")).json()


@router.get("/jobs/{job_id}/screenshot")
async def screenshot(job_id: str):
    return _png(await call("GET", f"/api/jobs/{job_id}/screenshot"), "no-store")


@router.get("/jobs/{job_id}/screenshot/{step}")
async def step_screenshot(job_id: str, step: int):
    return _png(await call("GET", f"/api/jobs/{job_id}/screenshot/{step}"), "private, max-age=3600")


@router.post("/jobs/{job_id}/approvals/{approval_id}")
async def answer_approval(job_id: str, approval_id: str, request: Request):
    """Approve or deny a step the agent paused on (a shell command, a file outside the allowed folders, a message, a
    purchase, a login, an app that is not on the allow-list)."""
    body = await request.json()
    return (await call("POST", f"/api/jobs/{job_id}/approvals/{approval_id}", json={"approve": bool(body.get("approve"))})).json()
