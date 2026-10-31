"""Each teammate's own computer space (feat/grok-style).

On this machine a teammate gets:
  * its own working folder (DATA_DIR/teammates/<id>/work): files it makes land there, nothing else is shared by default;
  * its own Rooks Browser profile ("tm-<id>"): sign in to a site once for that teammate, and other teammates don't
    see that session;
  * its own row of Rooks Desk / browser runs, shown on the Computer tab with Watch and Take over.
A separate VM or container per teammate is a documented follow-up (docs/teammates.md): separate folders and browser
profiles are work surfaces, not a security boundary, exactly like screens on one computer.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx
from fastapi import HTTPException

from .. import config, db


def root() -> Path:
    return config.DATA_DIR / "teammates"


def space(tid: int) -> dict:
    base = root() / str(int(tid))
    return {"work": base / "work", "profile": f"tm-{int(tid)}"}


def ensure_space(tid: int) -> dict:
    s = space(tid)
    s["work"].mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(s["work"].parent, 0o700)
    except OSError:
        pass
    return s


def record_run(tid: int, tool: str, job_id: str, goal: str) -> None:
    db.conn().execute("CREATE TABLE IF NOT EXISTS tm_runs (id INTEGER PRIMARY KEY, teammate_id INTEGER NOT NULL, tool TEXT NOT NULL, "
                      "job_id TEXT NOT NULL, goal TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)")
    db.run("INSERT INTO tm_runs(teammate_id, tool, job_id, goal, created_at) VALUES (?, ?, ?, ?, ?)", tid, tool, str(job_id), goal[:300], db.now())


def runs(tid: int) -> list[dict]:
    try:
        return db.all("SELECT tool, job_id, goal, created_at FROM tm_runs WHERE teammate_id = ? ORDER BY id DESC LIMIT 20", tid)
    except Exception:  # noqa: BLE001 - table not made yet
        return []


def files(tid: int) -> list[dict]:
    work = ensure_space(tid)["work"]
    out = []
    for p in sorted(work.rglob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True)[:100]:
        if p.is_file() and not p.name.startswith("."):
            out.append({"name": p.name, "path": str(p.relative_to(work)), "size": p.stat().st_size})
    return out


def overview(tid: int) -> dict:
    s = ensure_space(tid)
    return {"folder": str(s["work"]), "browser_profile": s["profile"], "runs": runs(tid), "files": files(tid)[:20],
            "browser_available": bool(config.BROWSER_URL), "desk_available": bool(getattr(config, "DESK_URL", "")),
            "isolation": "Separate folder and browser profile on this computer. Not a security boundary: see docs/teammates.md."}


async def sign_in(tid: int, url: str) -> dict:
    """Open a visible Rooks Browser window in this teammate's own profile; the person signs in there themselves."""
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Give the full web address, starting with https://")
    if not (config.BROWSER_URL and config.BROWSER_API_KEY):
        raise HTTPException(400, "Rooks Browser isn't running on this computer.")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{config.BROWSER_URL}/api/site/login", json={"url": url, "profile": space(tid)["profile"]},
                         headers={"Authorization": f"Bearer {config.BROWSER_API_KEY}"})
    if r.status_code >= 400:
        raise HTTPException(r.status_code, "Rooks Browser couldn't open that site.")
    return {"opened": True, "profile": space(tid)["profile"]}
