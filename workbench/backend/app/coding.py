"""Coding tasks run by an official coding agent (OpenAI Codex CLI or Claude Code) on your paired computer.

ROOKS does not talk to these agents' accounts. The companion (companion/rooks_companion/agents.py) runs the agent's
own command line tool in a folder you allowed on that computer, with the agent's own sandbox, and only after you
approved this one task. ROOKS keeps the task, a short progress log and the agent's final message, and the coordinator
posts the result into the conversation that asked for it.

Billing is the agent's, not ROOKS's: Codex signed in with ChatGPT uses your plan's Codex limits, Codex signed in with
an API key bills that key, and Claude Code runs only with an API key (billed per use). None of this powers ROOKS chat.
"""

import asyncio
import json
import time

from fastapi import HTTPException

from . import audit, computer, db

AGENTS = {"codex": "Codex", "claude": "Claude Code"}
POLL_SECONDS = 2.0
DETECT_TTL = 120
_tasks: set[asyncio.Task] = set()
_polling: set[int] = set()

def _row(rid: int) -> dict | None:
    r = db.one("SELECT * FROM coding_runs WHERE id = ?", rid)
    if r:
        r["progress"] = json.loads(r["progress"] or "[]")
        r["agent_name"] = AGENTS.get(r["agent"], r["agent"])
    return r


def view(rid: int) -> dict | None:
    r = _row(rid)
    if not r:
        return None
    cost = next((p.get("text", "") for p in reversed(r["progress"]) if p.get("kind") == "cost"), "")
    return {"id": r["id"], "agent": r["agent_name"], "task": r["task"], "folder": r["folder"], "status": r["status"], "cost": cost,
            "progress": [p.get("text", "")[:200] for p in r["progress"] if p.get("kind") in ("command", "files", "cost", "note")],
            "result": r["result"], "error": r["error"], "billing": r["billing"], "created_at": r["created_at"]}


async def detect(fresh: bool = False) -> dict:
    """What the paired computer reports about its coding agents (cached for two minutes)."""
    if not computer.online_device():
        return {"at": 0, "device": None, "agents": {}, "reason": "No computer is paired and online. Pair the ROOKS Companion under Computer."}
    cached = json.loads(db.get_setting("coding_agents", "{}") or "{}")
    if cached and not fresh and time.time() - cached.get("at", 0) < DETECT_TTL and cached.get("device") == computer.online_device()["name"]:
        return cached
    if "coding" not in (computer.online_device() or {}).get("capabilities", []):
        out = {"at": time.time(), "device": computer.online_device()["name"], "agents": {},
               "reason": "The paired computer reports no coding agent (Codex CLI or Claude Code), or no allowed folder."}
        db.set_setting("coding_agents", json.dumps(out))
        return out
    r = await computer.request("agents_detect", {}, timeout=60)
    if not r["ok"]:
        raise HTTPException(502, f"The companion could not check its coding agents: {r['error'] or r['status']}")
    out = {"at": time.time(), "device": computer.online_device()["name"], "agents": r["result"] or {}, "reason": ""}
    db.set_setting("coding_agents", json.dumps(out))
    return out


def approval_reason(raw_args: str) -> str:
    try:
        a = json.loads(raw_args or "{}")
    except ValueError:
        a = {}
    agent = a.get("agent") if isinstance(a, dict) and a.get("agent") in AGENTS else "codex"
    info = (json.loads(db.get_setting("coding_agents", "{}") or "{}").get("agents") or {}).get(agent) or {}
    billing = info.get("billing") or ("Codex's own sign-in decides billing" if agent == "codex" else "Anthropic bills your API key per use")
    return (f"{AGENTS[agent]} will change files in {a.get('folder') or 'a folder'} on your computer, inside its own sandbox. "
            f"Billing: {billing}. ROOKS's spending limit does not cover it. Always asks first.")


async def start(task: str, folder: str, agent: str = "codex", ctx=None) -> dict:
    """The assistant's tool. Runs only from an approval (ctx.approved), never straight from a model's call."""
    from . import coordinator

    if not ctx or not ctx.approved:
        raise HTTPException(403, "A coding task runs only after the user approves it.")
    if ctx.channel in ("schedule", "agent", "telegram", "mission", "learn"):
        raise HTTPException(403, "Coding agents are not available to unattended runs.")
    agent = agent if agent in AGENTS else "codex"
    found = await detect(fresh=True)
    info = (found.get("agents") or {}).get(agent)
    if not info or not info.get("installed"):
        raise HTTPException(409, found.get("reason") or f"{AGENTS[agent]} is not installed on the paired computer.")
    if not info.get("ready"):
        raise HTTPException(409, info.get("reason") or f"{AGENTS[agent]} is not ready on the paired computer.")
    r = await computer.request("agent_start", {"agent": agent, "task": task, "folder": folder}, conversation_id=ctx.conversation_id,
                               approved=True, timeout=90)
    if not r["ok"]:
        raise HTTPException(409, f"{AGENTS[agent]} did not start: {r['error'] or r['status']}")
    res = r["result"] or {}
    rid = db.run("INSERT INTO coding_runs(conversation_id, agent, task, folder, device_run, billing, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", ctx.conversation_id or None, agent, task[:8000], str(res.get("folder") or folder)[:1000],
                 str(res.get("run", "")), info.get("billing", ""), db.now(), db.now())
    audit.record("user", "coding task", f"#{rid}", f"{AGENTS[agent]} in {folder}")
    coordinator.register(ctx.conversation_id, "coding", rid, task)
    spawn(rid)
    return {"id": rid, "agent": AGENTS[agent], "status": "running", "folder": res.get("folder") or folder,
            "note": "It runs on the user's computer; ROOKS posts the result in this conversation when it finishes."}


async def stop(rid: int) -> dict:
    r = _row(rid)
    if not r:
        raise HTTPException(404, "Coding task not found")
    if r["status"] == "running":
        out = await computer.request("agent_stop", {"run": r["device_run"]}, conversation_id=r["conversation_id"], approved=True, timeout=30)
        db.run("UPDATE coding_runs SET status = 'cancelled', updated_at = ? WHERE id = ? AND status = 'running'", db.now(), rid)
        if not out["ok"]:
            db.run("UPDATE coding_runs SET error = ? WHERE id = ?", f"Stop was sent, but the companion answered: {out['error']}"[:600], rid)
        audit.record("user", "coding task stopped", f"#{rid}")
        from . import coordinator

        coordinator.wake()
    return view(rid)


async def poll_once(rid: int) -> bool:
    """One progress check. Returns False when the run is over."""
    r = _row(rid)
    if not r or r["status"] != "running":
        return False
    out = await computer.request("agent_poll", {"run": r["device_run"], "since": r["next"]}, conversation_id=r["conversation_id"], timeout=20)
    if not out["ok"]:
        if "not running on this computer" in (out["error"] or ""):
            db.run("UPDATE coding_runs SET status = 'failed', error = ?, updated_at = ? WHERE id = ?",
                   "The companion no longer knows this task (it restarted). Check the folder before running it again.", db.now(), rid)
            return False
        return True  # the computer may be briefly away; try again
    from .sensitive import redact

    res = out["result"] or {}
    # what the agent ran and said is kept with keys, tokens and card numbers removed
    events = [{**e, "text": redact(str(e.get("text", "")))[:400]} for e in (res.get("events") or []) if isinstance(e, dict)]
    progress = (r["progress"] + events)[-200:]
    res["result"], res["error"] = redact(str(res.get("result") or "")), redact(str(res.get("error") or ""))
    status = res.get("status") if res.get("status") in ("running", "done", "failed", "cancelled") else "running"
    db.run("UPDATE coding_runs SET progress = ?, next = ?, status = ?, result = ?, error = ?, updated_at = ? WHERE id = ? AND status = 'running'",
           json.dumps(progress), int(res.get("next") or r["next"]), status, str(res.get("result") or "")[:24000],
           str(res.get("error") or "")[:2000], db.now(), rid)
    if status != "running":
        from . import coordinator

        coordinator.wake()
        return False
    return True


async def _poll(rid: int) -> None:
    try:
        while await poll_once(rid):
            await asyncio.sleep(POLL_SECONDS)
    except HTTPException as e:  # Computer Use turned off or stopped: the run on the computer was stopped with it
        db.run("UPDATE coding_runs SET status = 'failed', error = ?, updated_at = ? WHERE id = ? AND status = 'running'",
               f"ROOKS lost contact with the task: {e.detail}"[:600], db.now(), rid)
        from . import coordinator

        coordinator.wake()
    finally:
        _polling.discard(rid)


def spawn(rid: int) -> None:
    if rid in _polling:
        return
    try:
        t = asyncio.get_running_loop().create_task(_poll(rid))
    except RuntimeError:
        return
    _polling.add(rid)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def recover() -> None:
    """After a restart, keep following tasks that were running: the companion kept running them."""
    for r in db.all("SELECT id FROM coding_runs WHERE status = 'running'"):
        spawn(r["id"])


async def status_tool() -> dict:
    """The assistant's read-only view: which agents the paired computer has, and recent tasks."""
    cached = await detect()
    return {"computer": cached.get("device"), "agents": cached.get("agents") or {}, "note": cached.get("reason", ""),
            "recent": [view(r["id"]) for r in db.all("SELECT id FROM coding_runs ORDER BY id DESC LIMIT 5")]}
