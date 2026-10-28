"""The versioned API every client uses: browser, terminal CLI and desktop app. Version 1.

It adds no second engine: each route calls the same functions as the browser's own routes, with the same checks.

- Authentication: the browser session, or the terminal token from Settings (Terminal access).
- Compatibility: a client sends `X-Rooks-API: <version>`. A version this server does not speak gets 426 with what to do;
  no header means version 1. GET /api/v1/meta says which versions this server speaks.
- Safe retries: a client may send `Idempotency-Key` with a chat request. The same key again never runs the turn twice; it
  answers with what the first request produced (or that it is still running).
- Live state: GET /api/v1/events opens with a complete baseline, then sends changes. A client that reconnects replaces its
  copy from the new baseline, so nothing is replayed and nothing is run again.
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import assistant, attention, db, llm, work
from .api import sse
from .assistant_api import ChatIn, require_user_or_cli

API_VERSION = 1
SUPPORTED = (1,)
SERVER_VERSION = "15.0.0"


def check_version(x_rooks_api: str | None = Header(None)) -> None:
    if x_rooks_api is None:
        return
    try:
        v = int(x_rooks_api)
    except ValueError:
        raise HTTPException(400, "X-Rooks-API must be a whole number.") from None
    if v not in SUPPORTED:
        newer = v > max(SUPPORTED)
        raise HTTPException(426, f"This ROOKS server speaks API version {', '.join(map(str, SUPPORTED))}; your client uses {v}. "
                                 + ("Update the ROOKS server, or use an older client." if newer else "Update your client."))


router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_user_or_cli), Depends(check_version)])


@router.get("/meta")
def meta():
    return {"api_version": API_VERSION, "supported": list(SUPPORTED), "server_version": SERVER_VERSION,
            "phases": [str(p) for p in work.Phase], "phase_words": {str(k): v for k, v in work.WORDS.items()}}


@router.get("/status")
def status():
    """What a person needs to know before asking: is a model connected, what can ROOKS reach, does anything need them."""
    from . import computer

    providers = db.all("SELECT id, name, enabled FROM providers ORDER BY id")
    try:
        model, source = llm.effective_default()
        model_problem = ""
    except HTTPException as e:
        model, source, model_problem = "", "", str(e.detail)
    device = computer.online_device()
    return {"api_version": API_VERSION, "server_version": SERVER_VERSION, "ai_mode": llm.ai_mode(),
            "providers": [{"id": p["id"], "name": p["name"], "enabled": bool(p["enabled"])} for p in providers],
            "model": model, "model_source": source, "model_problem": model_problem,
            "needs_review": attention.count(), "active_work": len(work.list_items(active=True, limit=200)),
            "companion": {"online": bool(device), "name": device["name"] if device else ""}}


# Conversations
@router.get("/conversations")
def conversations(limit: int = Query(50, ge=1, le=200), project_id: int | None = None):
    if project_id:
        return db.all("SELECT id, title, channel, project_id, updated_at FROM conversations WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                      project_id, limit)
    return db.all("SELECT id, title, channel, project_id, updated_at FROM conversations WHERE channel IN ('web', 'cli', 'telegram', 'schedule') "
                  "ORDER BY updated_at DESC LIMIT ?", limit)


@router.get("/conversations/{cid}")
def conversation(cid: int, after: int = 0):
    c = db.one("SELECT id, title, channel, project_id, model, updated_at FROM conversations WHERE id = ?", cid)
    if not c:
        raise HTTPException(404, "Conversation not found")
    c["messages"] = db.all("SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? AND id > ? "
                           "AND role IN ('user', 'assistant') AND content != '' ORDER BY id", cid, after)
    c["work"] = work.list_items(conversation_id=cid)
    return c


@router.post("/chat")
async def chat(data: ChatIn, idempotency_key: str | None = Header(None, max_length=200)):
    """One turn, streamed as server-sent events. With Idempotency-Key, a retried request is answered, not run again."""
    from .assistant_api import chat as run_chat

    if not idempotency_key:
        return run_chat(data)
    key = f"chat:{idempotency_key}"
    if data.conversation_id and not db.one("SELECT id FROM conversations WHERE id = ?", data.conversation_id):
        raise HTTPException(404, "Conversation not found")
    last = db.one("SELECT MAX(id) AS m FROM messages WHERE conversation_id = ?", data.conversation_id)["m"] if data.conversation_id else 0
    try:
        db.run("INSERT INTO client_requests(key, route, conversation_id, after_message_id, created_at) VALUES (?, 'chat', ?, ?, ?)",
               key, data.conversation_id, last or 0, db.now())
    except Exception:  # noqa: BLE001 - the key exists: this is a retry
        return sse(_replay(key))

    async def first():
        inner = run_chat(data).body_iterator
        async for chunk in inner:
            text = chunk.decode() if isinstance(chunk, bytes) else chunk
            if '"type": "start"' in text:
                try:
                    cid = json.loads(text[6:])["conversation_id"]
                    db.run("UPDATE client_requests SET conversation_id = ? WHERE key = ?", cid, key)
                except (ValueError, KeyError):
                    pass
            yield text

    from fastapi.responses import StreamingResponse

    return StreamingResponse(first(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def _replay(key: str):
    r = db.one("SELECT * FROM client_requests WHERE key = ?", key)
    cid = r["conversation_id"]
    yield {"type": "start", "conversation_id": cid, "replayed": True}
    if not cid:
        yield {"type": "error", "error": "The first request with this key did not start a conversation."}
        return
    running = assistant.busy(cid)
    reply = db.one("SELECT content FROM messages WHERE conversation_id = ? AND id > ? AND role = 'assistant' AND content != '' "
                   "ORDER BY id DESC LIMIT 1", cid, r["after_message_id"])
    if reply and not running:
        yield {"type": "message", "content": reply["content"], "replayed": True}
    else:
        yield {"type": "in_progress", "conversation_id": cid,
               "text": "The first request with this key is still being answered. Read the conversation for the reply."}
    yield {"type": "done", "conversation_id": cid}


# Work: research, missions, workers, coding tasks and agent tasks in one typed lifecycle (work.py)
@router.get("/work")
def work_list(conversation_id: int | None = None, project_id: int | None = None, active: bool = False,
              limit: int = Query(50, ge=1, le=200)):
    return work.list_items(conversation_id, project_id, active, limit)


@router.get("/work/{item_id}")
def work_get(item_id: str):
    return work.get(item_id).dict() | {"events": work.events(item_id)}


@router.post("/work/{item_id}/cancel")
async def work_cancel(item_id: str):
    return await work.cancel(item_id)


@router.post("/work/{item_id}/retry")
async def work_retry(item_id: str):
    return await work.retry(item_id)


# Approvals: the same decision path as the browser (assistant.decide_approval)
@router.get("/approvals")
def approvals():
    return [assistant.approval_view(r) for r in db.all("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id")]


class Decision(BaseModel):
    approve: bool


@router.post("/approvals/{aid}")
async def decide(aid: int, data: Decision):
    """Deciding twice (a client retrying after a dropped connection) answers with the decision already made."""
    a = db.one("SELECT * FROM approvals WHERE id = ?", aid)
    if not a:
        raise HTTPException(404, "Approval not found")
    if a["status"] != "pending":
        return assistant.approval_view(a) | {"already_decided": True}
    try:
        return await assistant.decide_approval(aid, data.approve)
    except HTTPException as e:
        if e.status_code == 409:
            return assistant.approval_view(db.one("SELECT * FROM approvals WHERE id = ?", aid)) | {"already_decided": True}
        raise


@router.get("/projects")
def projects_list():
    from . import projects_api

    return projects_api.list_projects()


@router.get("/capabilities")
def capabilities():
    """What ROOKS can do here: each tool's group, risk, time limit, whether it asks first, and whether it is set up."""
    from .assistant_api import tool_registry

    out = []
    for t in tool_registry():
        state = "off" if t["permission"] == "off" else "available" if t["available"] else "needs_setup"
        out.append({"name": t["name"], "description": t["description"], "group": t["group"], "group_label": t["group_label"],
                    "risk": t["risk"], "timeout": t["timeout"], "params": t["params"], "required": t["required"],
                    "asks_first": t["always_asks"] or t["permission"] == "ask", "outside_content": t["name"] in assistant.EXTERNAL_TOOLS,
                    "state": state})
    return out


# Live state
def _snapshot() -> dict:
    conv = db.one("SELECT MAX(updated_at) AS u FROM conversations")["u"] or ""
    ev = db.one("SELECT MAX(id) AS m FROM work_events")["m"] or 0
    return {"conversations_at": conv, "work_event": ev, "needs_review": attention.count()}


@router.get("/events")
async def events(request: Request, once: bool = False, interval: float = Query(2.0, ge=0.5, le=30)):
    """Opens with {"type": "baseline", ...}: everything a client shows. Then one event per kind of change:
    conversations, work (with the changed items), needs_review. A heartbeat every 15 seconds keeps proxies open."""

    async def gen():
        snap = _snapshot()
        yield {"type": "baseline", "api_version": API_VERSION, "work": work.list_items(active=True, limit=100),
               "conversations": conversations(limit=50), **snap}
        if once:
            return
        beat = time.monotonic()
        while not await request.is_disconnected():
            await asyncio.sleep(interval)
            now = _snapshot()
            if now["conversations_at"] != snap["conversations_at"]:
                yield {"type": "conversations", "conversations_at": now["conversations_at"]}
            if now["work_event"] != snap["work_event"]:
                rows = db.all("SELECT DISTINCT kind, ref_id FROM work_events WHERE id > ? AND id <= ?", snap["work_event"], now["work_event"])
                items = []
                for r in rows[:50]:
                    try:
                        items.append(work.get(f"{r['kind']}:{r['ref_id']}").dict())
                    except HTTPException:
                        pass  # deleted since
                yield {"type": "work", "items": items, "work_event": now["work_event"]}
            if now["needs_review"] != snap["needs_review"]:
                yield {"type": "needs_review", "count": now["needs_review"]}
            if time.monotonic() - beat > 15:
                yield {"type": "heartbeat"}
                beat = time.monotonic()
            snap = now

    return sse(gen())
