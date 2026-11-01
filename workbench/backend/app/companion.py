"""Companion: one live view of every AI agent you run, with approvals and questions you can answer from anywhere.

Surfaces that use it: the notch/top-edge app on the desktop (companion/macos, companion/desktop), the phone (push +
Lock Screen / Dynamic Island), and the web app. Agents that report into it:

- Rooks itself (assistant approvals held by actions.py) and coding jobs handed to Claude Code / Codex (cli_agents.py).
- Any external agent through hooks (companion/hooks): Claude Code, Codex, Gemini CLI, Cursor. A hook posts its steps
  to ``POST /api/companion/events`` and, when the agent wants permission or asks a question, creates a request with
  ``POST /api/companion/requests`` and long-polls ``GET /api/companion/requests/{id}/wait`` for the answer.

Nothing here runs anything by itself. "Always" stores a rule (agent + tool + optional pattern) so the same request is
answered allow automatically next time; rules are listed and removable. Local clients authenticate with the companion
token written to ~/.rooks/companion.json (0600) together with the server URL; the phone uses its paired device token.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db

MAX_STEPS = 40
MAX_AGENTS = 30
IDLE_AFTER = 15 * 60  # an agent that hasn't reported for this long is shown as idle, then dropped after an hour

_lock = threading.Lock()
_agents: dict[str, dict] = {}       # key = f"{tool}:{session}"
_requests: dict[str, dict] = {}     # id -> request
_waiters: dict[str, list[asyncio.Event]] = {}
_version = 0
_changed: asyncio.Event | None = None
_loop: asyncio.AbstractEventLoop | None = None


# Auth -------------------------------------------------------------------------------------------------------------
def _token_hash() -> str:
    h = db.get_setting("companion_token_hash", "") or ""
    return h


def token_file() -> Path:
    return Path(os.getenv("ROOKS_COMPANION_FILE", str(Path.home() / ".rooks" / "companion.json")))


def publish(base_url: str) -> None:
    """Write {url, token} for local companion clients (notch app, hooks). New token each server start."""
    global _published
    if _published == base_url:
        return
    tok = secrets.token_urlsafe(32)
    db.set_setting("companion_token_hash", hashlib.sha256(tok.encode()).hexdigest())
    f = token_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": base_url.rstrip("/"), "token": tok, "pid": os.getpid()}))
        os.chmod(tmp, 0o600)
        tmp.replace(f)
        _published = base_url
    except OSError:
        pass


_published = ""


def _bearer_ok(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    tok = auth[7:] if auth.lower().startswith("bearer ") else ""
    if not tok:
        return False
    stored = _token_hash()
    if stored and hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), stored):
        return True
    cli = db.get_setting("cli_token_hash", "") or ""
    return bool(cli) and hmac.compare_digest(hashlib.sha256(tok.encode()).hexdigest(), cli)


def require_client(request: Request) -> str:
    """Signed-in browser, the local companion token, the CLI token, or a paired phone."""
    from .security import COOKIE, check_session

    if check_session(request.cookies.get(COOKIE)) or _bearer_ok(request):
        return "desktop"
    try:
        from .mobile import require_device

        require_device(request)
        return "phone"
    except HTTPException:
        pass
    raise HTTPException(401, "Not signed in")


# State ------------------------------------------------------------------------------------------------------------
def _bump() -> None:
    global _version
    _version += 1
    if _loop and _changed:
        _loop.call_soon_threadsafe(_changed.set)


def _agent(tool: str, session: str, name: str = "", folder: str = "") -> dict:
    key = f"{tool}:{session or 'default'}"
    a = _agents.get(key)
    if not a:
        if len(_agents) >= MAX_AGENTS:
            oldest = min(_agents.values(), key=lambda x: x["updated_at"])
            _agents.pop(oldest["key"], None)
        a = _agents[key] = {"key": key, "tool": tool, "session": session or "default", "name": name or _TOOL_NAMES.get(tool, tool),
                            "folder": folder, "status": "idle", "steps": [], "usage": None, "started_at": time.time(),
                            "updated_at": time.time()}
    if name:
        a["name"] = name
    if folder:
        a["folder"] = folder
    return a


_TOOL_NAMES = {"claude-code": "Claude Code", "codex": "Codex", "gemini-cli": "Gemini CLI", "cursor": "Cursor", "rooks": "Rooks"}


class EventIn(BaseModel):
    tool: str = Field(..., pattern=r"^[a-z0-9][a-z0-9._-]{0,40}$")
    session: str = Field("", max_length=200)
    name: str = Field("", max_length=80)
    folder: str = Field("", max_length=500)
    event: Literal["start", "step", "thinking", "idle", "done", "error", "usage"]
    text: str = Field("", max_length=2000)
    kind: str = Field("", max_length=40)  # read | edit | run | search | web | think | other
    usage: dict | None = None  # {"label": "5-hour", "used": 0.42, "resets_at": 1793500000} or tokens


def record(e: EventIn) -> dict:
    with _lock:
        a = _agent(e.tool, e.session, e.name, e.folder)
        a["updated_at"] = time.time()
        if e.event == "usage" and e.usage:
            a["usage"] = {k: e.usage[k] for k in list(e.usage)[:8]}
        elif e.event in ("start", "step", "thinking"):
            a["status"] = "thinking" if e.event == "thinking" else "working"
            if e.text:
                a["steps"].append({"t": time.time(), "kind": e.kind or ("think" if e.event == "thinking" else "other"),
                                   "text": e.text[:300]})
                del a["steps"][:-MAX_STEPS]
        elif e.event in ("idle", "done", "error"):
            a["status"] = {"idle": "idle", "done": "done", "error": "error"}[e.event]
            if e.text:
                a["steps"].append({"t": time.time(), "kind": e.event, "text": e.text[:300]})
                del a["steps"][:-MAX_STEPS]
        out = dict(a)
    _bump()
    return out


# Requests (permission / question) ---------------------------------------------------------------------------------
class RequestIn(BaseModel):
    tool: str = Field(..., pattern=r"^[a-z0-9][a-z0-9._-]{0,40}$")
    session: str = Field("", max_length=200)
    name: str = Field("", max_length=80)
    kind: Literal["permission", "question"]
    title: str = Field(..., min_length=1, max_length=300)
    detail: str = Field("", max_length=4000)
    action: str = Field("", max_length=80)   # the agent's tool name, e.g. Bash, Edit, WebFetch
    target: str = Field("", max_length=1000)  # the command or path, matched by "Always" rules
    options: list[str] = Field(default_factory=list, max_length=8)
    multi: bool = False


def _rules() -> list[dict]:
    try:
        return json.loads(db.get_setting("companion_always_rules", "[]") or "[]")
    except ValueError:
        return []


def _save_rules(rules: list[dict]) -> None:
    db.set_setting("companion_always_rules", json.dumps(rules[-200:]))


def _always_matches(r: dict) -> bool:
    for rule in _rules():
        if rule.get("tool") != r["tool"] or rule.get("action") != r["action"]:
            continue
        pat = rule.get("pattern") or "*"
        if fnmatch.fnmatchcase(r["target"] or "", pat):
            return True
    return False


def _rule_pattern(action: str, target: str) -> str:
    """'Always' for a shell command allows that program with any arguments; for files, that exact path."""
    if action.lower() in ("bash", "shell", "run", "exec", "run_shell_command") and target.strip():
        return target.strip().split()[0] + "*"
    return target or "*"


def create_request(data: RequestIn) -> dict:
    with _lock:
        a = _agent(data.tool, data.session, data.name)
        rid = uuid.uuid4().hex[:12]
        r = {"id": rid, "tool": data.tool, "session": a["session"], "agent": a["name"], "agent_key": a["key"],
             "kind": data.kind, "title": data.title, "detail": data.detail, "action": data.action, "target": data.target,
             "options": data.options, "multi": data.multi, "status": "pending", "answer": None, "decided_by": "",
             "created_at": time.time(), "decided_at": None}
        if data.kind == "permission" and _always_matches(r):
            r.update(status="allow", decided_by="rule", decided_at=time.time())
        else:
            a["status"] = "waiting"
        _requests[rid] = r
        for old in sorted(_requests.values(), key=lambda x: x["created_at"])[:-300]:
            _requests.pop(old["id"], None)
    if r["status"] == "pending":
        _notify_phone(r)
    _bump()
    return r


def _notify_phone(r: dict) -> None:
    try:
        from . import push

        if r["kind"] == "permission":
            body = r["title"]
        else:
            body = r["title"] + (" — " + ", ".join(r["options"][:4]) if r["options"] else "")
        push.notify(
            f"{r['agent']} needs you",
            body[:180],
            type="companion",
            request_id=r["id"],
            kind=r["kind"],
            options=r["options"][:4],
            request_kind=r["kind"],  # "permission" or "question" for categoryId
        )
    except Exception:  # noqa: BLE001 - a phone that can't be reached never blocks the agent
        pass


def decide(rid: str, decision: str, answer: Any = None, by: str = "desktop") -> dict:
    with _lock:
        r = _requests.get(rid)
        if not r:
            raise HTTPException(404, "No such request")
        if r["status"] != "pending":
            raise HTTPException(409, f"Already answered ({r['status']})")
        if r["kind"] == "permission":
            if decision not in ("allow", "deny", "always"):
                raise HTTPException(422, "Permission answers are allow, deny or always")
            if decision == "always":
                rules = _rules()
                rules.append({"tool": r["tool"], "action": r["action"], "pattern": _rule_pattern(r["action"], r["target"]),
                              "created_at": time.time()})
                _save_rules(rules)
            r["status"] = decision
        else:
            if decision == "deny":
                r["status"] = "deny"
            else:
                if answer in (None, "", []):
                    raise HTTPException(422, "Pick an option or type an answer")
                r["status"], r["answer"] = "answered", answer
        r["decided_by"], r["decided_at"] = by, time.time()
        a = _agents.get(r["agent_key"])
        if a and not any(x["status"] == "pending" and x["agent_key"] == a["key"] for x in _requests.values()):
            a["status"] = "working"
        events = _waiters.pop(rid, [])
    for ev in events:
        if _loop:
            _loop.call_soon_threadsafe(ev.set)
    _bump()
    return r


def remove_rule(index: int) -> list[dict]:
    rules = _rules()
    if not 0 <= index < len(rules):
        raise HTTPException(404, "No such rule")
    rules.pop(index)
    _save_rules(rules)
    return rules


# One view of everything -------------------------------------------------------------------------------------------
def _rooks_items() -> tuple[list[dict], list[dict]]:
    """Rooks' own held tool calls and coding jobs, in the same shape as hook requests."""
    agents, reqs = [], []
    try:
        from . import actions

        for p in actions.pending()[:20]:
            ca = p["created_at"]
            if isinstance(ca, str):
                try:
                    ca = datetime.fromisoformat(ca.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    ca = time.time()
            reqs.append({"id": f"rooks:{p['id']}", "tool": "rooks", "agent": "Rooks", "kind": "permission",
                         "title": f"Rooks wants to {p['tool'].replace('_', ' ')}", "detail": str(p.get("args") or "")[:600],
                         "action": p["tool"], "target": "", "options": [], "status": "pending", "created_at": ca,
                         "always": False})
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import cli_agents

        for run in list(cli_agents.RUNS.values())[-10:]:
            if run.status not in ("running", "needs_approval", "queued") and (time.time() - (run.ended_at or 0)) > 600:
                continue
            steps = [{"t": ev.get("t"), "kind": ev.get("kind", "other"), "text": str(ev.get("text") or ev.get("tool") or "")[:300]}
                     for ev in run.events[-MAX_STEPS:] if ev.get("text") or ev.get("tool")]
            agents.append({"key": f"job:{run.id}", "tool": run.agent, "session": run.id, "name": f"{run.summary()['agent_name']} (job)",
                           "folder": run.folder, "status": {"needs_approval": "waiting", "running": "working", "queued": "working",
                                                            "failed": "error"}.get(run.status, "done"),
                           "steps": steps, "usage": None, "updated_at": run.ended_at or time.time(), "started_at": run.created_at})
            if run.status == "needs_approval":
                rca = run.created_at
                if isinstance(rca, str):
                    try:
                        rca = datetime.fromisoformat(rca.replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        rca = time.time()
                reqs.append({"id": f"job:{run.id}", "tool": run.agent, "agent": run.summary()["agent_name"], "kind": "permission",
                             "title": f"{run.summary()['agent_name']} needs approval for {len(run.pending)} step(s)",
                             "detail": "\n".join(str(p.get("summary") or p.get("tool") or p)[:200] for p in run.pending[:5]),
                             "action": "job", "target": "", "options": [], "status": "pending", "created_at": rca,
                             "always": False})
    except Exception:  # noqa: BLE001
        pass
    return agents, reqs


def state() -> dict:
    now = time.time()
    with _lock:
        for k in [k for k, a in _agents.items() if now - a["updated_at"] > 3600]:
            _agents.pop(k)
        agents = []
        for a in _agents.values():
            a2: dict = dict(a, steps=list(a["steps"]))
            if a2["status"] in ("working", "thinking") and now - a2["updated_at"] > IDLE_AFTER:
                a2["status"] = "idle"
            agents.append(a2)
        reqs = [dict(r, always=r["kind"] == "permission") for r in _requests.values() if r["status"] == "pending"]
    ra, rr = _rooks_items()
    agents = sorted(agents + ra, key=lambda a: -a["updated_at"])
    reqs = sorted(reqs + rr, key=lambda r: r["created_at"])
    try:
        from . import usage

        s = usage.summary()
        month = {"cents": s["month_cents"], "tokens": s["month_tokens"]}
    except Exception:  # noqa: BLE001
        month = None
    mood = "waiting" if reqs else ("working" if any(a["status"] in ("working", "thinking") for a in agents) else
                                   ("done" if any(a["status"] == "done" and now - a["updated_at"] < 120 for a in agents) else "idle"))
    return {"version": _version, "mood": mood, "agents": agents, "requests": reqs,
            "usage": {"rooks_month": month, "agents": [{"name": a["name"], **a["usage"]} for a in agents if a.get("usage")]},
            "rules": _rules()}


async def wait_for(rid: str, timeout: float) -> dict:
    with _lock:
        r = _requests.get(rid)
        if not r:
            raise HTTPException(404, "No such request")
        if r["status"] != "pending":
            return r
        ev = asyncio.Event()
        _waiters.setdefault(rid, []).append(ev)
    try:
        await asyncio.wait_for(ev.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    return _requests.get(rid) or r


# Routes -----------------------------------------------------------------------------------------------------------
router = APIRouter(prefix="/api/companion")


def _init_loop() -> None:
    global _loop, _changed
    loop = asyncio.get_running_loop()
    if _loop is not loop:
        _loop, _changed = loop, asyncio.Event()


@router.get("/state")
async def get_state(request: Request, who: str = Depends(require_client)):
    _init_loop()
    return state()


@router.get("/stream")
async def stream(request: Request, who: str = Depends(require_client)):
    """Server-sent events: the whole state whenever anything changes (and a heartbeat every 20 s)."""
    from fastapi.responses import StreamingResponse

    _init_loop()

    async def body():
        last = -1
        while not await request.is_disconnected():
            if _version != last:
                last = _version
                yield f"data: {json.dumps(state())}\n\n"
            assert _changed is not None
            _changed.clear()
            try:
                await asyncio.wait_for(_changed.wait(), 20)
            except asyncio.TimeoutError:
                yield ": ping\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/events")
async def post_event(data: EventIn, who: str = Depends(require_client)):
    _init_loop()
    return record(data)


@router.post("/requests")
async def post_request(data: RequestIn, who: str = Depends(require_client)):
    _init_loop()
    return create_request(data)


@router.get("/requests/{rid}/wait")
async def wait(rid: str, timeout: float = 55, who: str = Depends(require_client)):
    """Long-poll for the answer. Returns the request; status stays "pending" if the timeout passed first."""
    _init_loop()
    return await wait_for(rid, max(1.0, min(timeout, 120.0)))


class DecideIn(BaseModel):
    decision: Literal["allow", "deny", "always", "answer"]
    answer: Any = None


@router.post("/requests/{rid}/decide")
async def post_decide(rid: str, data: DecideIn, who: str = Depends(require_client)):
    _init_loop()
    if rid.startswith("rooks:"):
        from . import assistant

        if data.decision == "always":
            raise HTTPException(422, "Rooks' own actions are approved one at a time")
        await assistant.decide(int(rid.split(":", 1)[1]), data.decision == "allow")
        _bump()
        return {"id": rid, "status": data.decision}
    if rid.startswith("job:"):
        from . import cli_agents

        run = cli_agents.RUNS.get(rid.split(":", 1)[1])
        if not run:
            raise HTTPException(404, "No such job")
        try:
            if data.decision == "deny":
                cli_agents.deny(run)
            else:
                cli_agents.approve(run, [], "")
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        _bump()
        return {"id": rid, "status": data.decision}
    return decide(rid, data.decision, data.answer, by=who)


@router.delete("/rules/{index}")
def delete_rule(index: int, who: str = Depends(require_client)):
    rules = remove_rule(index)
    _bump()
    return rules


@router.post("/drop")
async def drop(request: Request, name: str, question: str = "", who: str = Depends(require_client)):
    """A file dropped on the mascot (raw body): add it to Files and hand back the prompt to open a chat with."""
    from . import analysis

    size, chunks = 0, []
    async for c in request.stream():
        size += len(c)
        if size > 100 * 1024 * 1024:
            raise HTTPException(413, "Files up to 100 MB can be dropped here; use the Files page for bigger ones.")
        chunks.append(c)
    saved = analysis.add(Path(name).name or "dropped-file", data=b"".join(chunks))
    label = saved.get("name") or name
    q = question.strip() or f"I dropped {label} on you. What is it, and what's important in it?"
    return {"file": saved, "prompt": q, "open": f"/chat?file={saved.get('id', '')}&prompt=" + _quote(q)}


def _quote(s: str) -> str:
    from urllib.parse import quote

    return quote(s, safe="")
