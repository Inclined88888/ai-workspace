"""The rooks terminal learns with the app: one memory and one set of skills for chat, terminal, browser and computer.

/v1/rooks/context    what the app knows that fits this request (facts about the user) and the saved skills index
/v1/rooks/skills/check  check a GitHub skill for the Skills page (installing stays in the app)
/v1/rooks/memory     the facts the app keeps (the terminal's /memory)
/v1/rooks/skills/N   one skill's steps (counts as a use, like load_skill in chat)
/v1/rooks/remember   save a fact the user stated in the terminal (same store as chat's remember tool)
/v1/rooks/turn       a finished terminal request: kept as a conversation (shown in the app's chat history as a
                     terminal chat) and mined in the background for facts and reusable skills, exactly like a chat turn

Same bridge key as /v1/chat/completions. Learning obeys the app's switches: Settings "Learn about me" and learning on.
"""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import db, learn
from .bridge import require_bridge_key

router = APIRouter(prefix="/v1/rooks", dependencies=[Depends(require_bridge_key)])
CHANNEL = "cli"  # listed in the app's chat history next to web, phone and Telegram chats


@router.get("/context")
def context(q: str = ""):
    block = ""
    if learn.enabled():
        try:
            from .adapt import knowledge

            block = knowledge.context_block(q[:2000]) if q.strip() else ""
        except Exception:  # noqa: BLE001 - context is a help, never a reason for the terminal to fail
            block = ""
    skills = db.all("SELECT name, description FROM skills WHERE enabled = 1 ORDER BY uses DESC, updated_at DESC LIMIT 30")
    return {"context": block, "skills": [dict(s) for s in skills], "learning": learn.enabled()}


@router.get("/memory")
def memory():
    """What the app knows about the user (the terminal's /memory), newest and pinned first."""
    from .adapt import knowledge

    facts = [{"content": f["content"], "category": f.get("category", "")} for f in knowledge.all_facts()[:40]]
    return {"facts": facts, "learning": learn.enabled()}


@router.get("/skills/{name}")
def skill(name: str):
    s = db.one("SELECT * FROM skills WHERE name = ? AND enabled = 1", name)
    if not s:
        raise HTTPException(404, f"No skill named {name}")
    db.run("UPDATE skills SET uses = uses + 1 WHERE id = ?", s["id"])
    return {"name": s["name"], "description": s["description"], "steps": s["steps"]}


@router.get("/search")
async def search(q: str, count: int = 6):
    """The app's web search (the same service chat uses) for the terminal's web_search tool."""
    from . import search as web

    if not q.strip():
        raise HTTPException(400, "q is required")
    try:
        return {"results": await web.web_search(q[:400], max(1, min(count, 10)))}
    except Exception as e:  # noqa: BLE001 - say why in plain words
        raise HTTPException(502, f"Web search isn't working: {e}. Set it up in the app under Settings > Web search.") from e


@router.get("/read")
async def read(url: str):
    """A web page as readable text, for the terminal's web_fetch tool."""
    import httpx

    from .research import fetch_text

    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must start with http:// or https://")
    async with httpx.AsyncClient(timeout=25, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 AI-Workspace"}) as c:
        try:
            text = await fetch_text(c, url)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"Could not read {url}: {e}") from e
    return {"url": url, "text": (text or "")[:60000]}


class CheckIn(BaseModel):
    url: str = Field(..., min_length=3, max_length=500)


@router.post("/skills/check")
async def check_skill(data: CheckIn):
    """/skills add in the terminal: the app's GitHub skill check (pinned commit, code and prompt-injection scans,
    sandboxed trial). Installing stays a decision the user makes on the app's Skills page, never from here."""
    from . import skills_hub

    stages, last = [], {}
    try:
        async for ev in skills_hub.pipeline(data.url):
            last = ev
            if ev.get("stage") and ev.get("status") in ("done", "failed", "blocked", "warn", "choose"):
                stages.append({"stage": ev["stage"], "status": ev["status"], "detail": ev.get("detail", "")})
    except HTTPException as e:
        return {"ok": False, "error": e.detail, "stages": stages}
    out = {"ok": bool(last.get("done")), "stages": stages}
    if last.get("choices"):
        out["choices"] = [{"name": c["name"], "url": c["url"]} for c in last["choices"]]
    if isinstance(last.get("skill"), dict):
        s = last["skill"]
        out["skill"] = {k: s.get(k) for k in ("id", "name", "description", "status", "risk", "risk_level")}
    return out


class RememberIn(BaseModel):
    content: str = Field(..., min_length=4, max_length=500)
    kind: str = "fact"


@router.post("/remember")
def remember(data: RememberIn):
    from .assistant import _remember

    return _remember(data.content, data.kind)


class Call(BaseModel):
    name: str = Field(..., max_length=200)
    arguments: str = Field("", max_length=4000)
    result: str = Field("", max_length=4000)


class TurnIn(BaseModel):
    session: str = Field(..., min_length=1, max_length=120)   # the terminal's session id
    prompt: str = Field(..., min_length=1, max_length=20000)
    answer: str = Field("", max_length=40000)
    calls: list[Call] = Field(default_factory=list, max_length=200)
    model: str = Field("", max_length=300)
    untrusted: bool = False  # the request read web pages or other third-party text: never turned into a skill
    folder: str = Field("", max_length=300)


def _conversation(data: TurnIn) -> int:
    row = db.one("SELECT id FROM conversations WHERE channel = ? AND external_id = ?", CHANNEL, data.session)
    if row:
        db.run("UPDATE conversations SET updated_at = ? WHERE id = ?", db.now(), row["id"])
        return row["id"]
    title = " ".join(data.prompt.split())[:60]  # the chat list labels it "Terminal"
    return db.run("INSERT INTO conversations(title, channel, external_id, model, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                  title, CHANNEL, data.session, data.model if data.model not in ("", "default") else "", db.now(), db.now())


@router.post("/turn")
async def turn(data: TurnIn):
    cid = _conversation(data)
    db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)", cid, data.prompt, db.now())
    for i, c in enumerate(data.calls):
        call_id = f"rooks_{i}"
        db.run("INSERT INTO messages(conversation_id, role, content, tool_calls, created_at) VALUES (?, 'assistant', '', ?, ?)",
               cid, json.dumps([{"id": call_id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}]), db.now())
        db.run("INSERT INTO messages(conversation_id, role, content, tool_call_id, name, created_at) VALUES (?, 'tool', ?, ?, ?, ?)",
               cid, c.result, call_id, c.name, db.now())
    db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
           cid, data.answer or "(no answer)", db.now())
    learning = learn.enabled()
    if learning:
        # untrusted turns still teach facts the user typed, but never a skill (learn.mine_skill's rule for web content)
        task = asyncio.get_running_loop().create_task(
            learn._after(cid, 0 if data.untrusted else len(data.calls), "rooks-code"))
        learn._tasks.add(task)
        task.add_done_callback(learn._tasks.discard)
    return {"conversation_id": cid, "learning": learning}
