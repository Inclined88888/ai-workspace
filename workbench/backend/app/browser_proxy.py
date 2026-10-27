"""Proxy to the browser agent service so the ROOKS web app can run and watch browser jobs."""

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from . import config
from .security import require_user

router = APIRouter(prefix="/api/browser", dependencies=[Depends(require_user)])


async def _call(method: str, path: str, json: dict | None = None, params: dict | None = None) -> httpx.Response:
    if not config.BROWSER_URL or not config.BROWSER_API_KEY:
        raise HTTPException(503, "The browser agent add-on is not enabled. Run setup with --with browser.")
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.request(method, f"{config.BROWSER_URL}{path}", json=json, params=params,
                                headers={"Authorization": f"Bearer {config.BROWSER_API_KEY}"})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Browser agent unreachable: {e}") from e
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise HTTPException(r.status_code, detail)
    return r


@router.get("/providers")
async def providers():
    return (await _call("GET", "/api/providers")).json()


@router.get("/jobs")
async def jobs():
    return (await _call("GET", "/api/jobs")).json()


@router.post("/jobs")
async def create_job(request: Request):
    return (await _call("POST", "/api/jobs", json=await request.json())).json()


@router.get("/jobs/{job_id}")
async def job(job_id: str):
    return (await _call("GET", f"/api/jobs/{job_id}")).json()


@router.post("/jobs/{job_id}/cancel")
async def cancel(job_id: str):
    return (await _call("POST", f"/api/jobs/{job_id}/cancel")).json()


@router.delete("/jobs/{job_id}")
async def delete(job_id: str):
    return (await _call("DELETE", f"/api/jobs/{job_id}")).json()


@router.get("/jobs/{job_id}/screenshot")
async def screenshot(job_id: str):
    r = await _call("GET", f"/api/jobs/{job_id}/screenshot")
    return Response(r.content, media_type="image/png", headers={"Cache-Control": "no-store"})
