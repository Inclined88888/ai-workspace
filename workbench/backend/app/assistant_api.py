"""HTTP API for the assistant, memory, skills, schedules, inbox, Telegram, usage and the company.

Signed-in browser sessions and the terminal CLI (bearer token from Settings) can both call these routes.
"""

import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import actions, assistant, company, db, learn, receipts, scheduler, search, telegram, usage
from .api import sse
from .security import COOKIE, check_session, encrypt


def require_user_or_cli(request: Request) -> None:
    if check_session(request.cookies.get(COOKIE)):
        return
    auth = request.headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else ""
    stored = db.get_setting("cli_token_hash", "") or ""
    if token and stored and hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), stored):
        return
    raise HTTPException(401, "Not signed in")


router = APIRouter(prefix="/api", dependencies=[Depends(require_user_or_cli)])


# Chat
class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=50_000)
    conversation_id: int | None = None
    model: str | None = None
    channel: str = Field("web", pattern="^(web|cli)$")


@router.post("/assistant/chat")
def chat(data: ChatIn):
    cid = data.conversation_id
    if cid:
        if not db.one("SELECT id FROM conversations WHERE id = ?", cid):
            raise HTTPException(404, "Conversation not found")
    else:
        cid = assistant.new_conversation(data.channel)
    if data.model:
        db.run("UPDATE conversations SET model = ? WHERE id = ?", data.model, cid)

    async def events():
        yield {"type": "start", "conversation_id": cid}
        async for e in assistant.run_turn(assistant.Ctx(channel=data.channel, conversation_id=cid), data.message, data.model):
            yield e

    return sse(events())


@router.get("/conversations")
def conversations(channel: str | None = None, q: str = ""):
    if q:
        hits = assistant.search_history(q, 30)
        ids = list(dict.fromkeys(h["conversation_id"] for h in hits))
        return [db.one("SELECT * FROM conversations WHERE id = ?", i) for i in ids]
    if channel:
        return db.all("SELECT * FROM conversations WHERE channel = ? ORDER BY updated_at DESC LIMIT 200", channel)
    return db.all("SELECT * FROM conversations WHERE channel IN ('web', 'cli', 'telegram', 'schedule') ORDER BY updated_at DESC LIMIT 200")


@router.get("/conversations/{cid}")
def conversation(cid: int):
    c = db.one("SELECT * FROM conversations WHERE id = ?", cid)
    if not c:
        raise HTTPException(404, "Not found")
    rows = db.all("SELECT id, role, content, tool_calls, name, created_at FROM messages WHERE conversation_id = ? ORDER BY id", cid)
    for r in rows:
        r["tool_calls"] = [tc["function"]["name"] for tc in json.loads(r["tool_calls"])] if r["tool_calls"] else []
        if r["role"] == "tool":
            r["content"] = r["content"][:400]
    return {**c, "messages": rows}


class ConvPatch(BaseModel):
    title: str | None = Field(None, max_length=120)
    model: str | None = None


@router.patch("/conversations/{cid}")
def rename(cid: int, data: ConvPatch):
    if data.title is not None:
        db.run("UPDATE conversations SET title = ? WHERE id = ?", data.title.strip() or "Chat", cid)
    if data.model is not None:
        db.run("UPDATE conversations SET model = ? WHERE id = ?", data.model, cid)
    return db.one("SELECT * FROM conversations WHERE id = ?", cid)


@router.delete("/conversations/{cid}")
def delete_conversation(cid: int):
    db.run("DELETE FROM conversations WHERE id = ?", cid)
    return {"ok": True}


# Approvals: actions held by assistant.needs_approval
@router.get("/approvals")
def approvals(conversation_id: int | None = None):
    """Actions waiting for the user's decision."""
    return actions.pending(conversation_id)


class DecisionIn(BaseModel):
    approve: bool


@router.post("/approvals/{aid}")
async def decide(aid: int, data: DecisionIn):
    return await assistant.decide(aid, data.approve)


# Reliability record: per model tool outcomes and claim checks
@router.get("/reliability")
def reliability():
    return {"models": list(receipts.reliability().values()),
            "recent": db.all("SELECT id, ts, model, tool, outcome, verified, evidence, from_text, latency_ms FROM actions "
                             "WHERE kind = 'tool_call' ORDER BY id DESC LIMIT 50")}


# Run log and trace (item 21): every turn and model call, with the model calls, tool calls and decisions under it
@router.get("/runs")
def runs(limit: int = 50, before: int | None = None, channel: str = "", failed: bool = False, conversation_id: int | None = None):
    return {"runs": actions.runs(limit, before, channel, failed, conversation_id),
            "channels": [r["channel"] for r in db.all("SELECT DISTINCT channel FROM actions WHERE parent_action_id IS NULL "
                                                       "AND channel != '' ORDER BY channel")]}


@router.get("/runs/{aid}")
def run_trace(aid: int):
    return actions.trace(aid)


# Memory
class MemoryIn(BaseModel):
    content: str = Field(..., min_length=3, max_length=1000)
    kind: str = Field("fact", pattern="^(profile|fact|preference)$")


@router.get("/memories")
def memories(q: str = ""):
    if q:
        return assistant.search_memories(q, 50)
    return db.all("SELECT * FROM memories ORDER BY kind = 'profile' DESC, updated_at DESC LIMIT 500")


@router.post("/memories")
def add_memory(data: MemoryIn):
    mid = db.run("INSERT INTO memories(content, kind, source, created_at, updated_at) VALUES (?, ?, 'user', ?, ?)",
                 data.content.strip(), data.kind, db.now(), db.now())
    return db.one("SELECT * FROM memories WHERE id = ?", mid)


@router.put("/memories/{mid}")
def edit_memory(mid: int, data: MemoryIn):
    db.run("UPDATE memories SET content = ?, kind = ?, updated_at = ? WHERE id = ?", data.content.strip(), data.kind, db.now(), mid)
    return db.one("SELECT * FROM memories WHERE id = ?", mid)


@router.delete("/memories/{mid}")
def delete_memory(mid: int):
    db.run("DELETE FROM memories WHERE id = ?", mid)
    return {"ok": True}


# Skills
class SkillIn(BaseModel):
    name: str = Field(..., max_length=50)
    description: str = Field(..., max_length=300)
    steps: str = Field(..., max_length=20000)
    enabled: bool = True


@router.get("/skills")
def skills():
    return db.all("SELECT * FROM skills ORDER BY uses DESC, updated_at DESC")


@router.post("/skills")
def add_skill(data: SkillIn):
    return learn.upsert_skill(data.name, data.description, data.steps, source="user")


@router.put("/skills/{sid}")
def edit_skill(sid: int, data: SkillIn):
    db.run("UPDATE skills SET description = ?, steps = ?, enabled = ?, updated_at = ? WHERE id = ?",
           data.description, data.steps, int(data.enabled), db.now(), sid)
    return db.one("SELECT * FROM skills WHERE id = ?", sid)


@router.delete("/skills/{sid}")
def delete_skill(sid: int):
    db.run("DELETE FROM skills WHERE id = ?", sid)
    return {"ok": True}


# Schedules and inbox
class ScheduleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    cron: str = Field(..., max_length=100)
    prompt: str = Field(..., min_length=3, max_length=8000)
    deliver: str = Field("inbox", pattern="^(inbox|telegram)$")
    agent_id: int | None = None


@router.get("/schedules")
def schedules():
    return {"timezone": assistant.timezone_name(), "schedules": db.all("SELECT * FROM schedules ORDER BY id")}


@router.post("/schedules")
def add_schedule(data: ScheduleIn):
    return scheduler.create_schedule(data.name, data.cron, data.prompt, data.deliver, data.agent_id)


class ScheduleToggle(BaseModel):
    enabled: bool


@router.patch("/schedules/{sid}")
def toggle_schedule(sid: int, data: ScheduleToggle):
    s = db.one("SELECT * FROM schedules WHERE id = ?", sid)
    if not s:
        raise HTTPException(404, "Not found")
    db.run("UPDATE schedules SET enabled = ?, next_run = ? WHERE id = ?", int(data.enabled), scheduler.next_run(s["cron"]), sid)
    return db.one("SELECT * FROM schedules WHERE id = ?", sid)


@router.delete("/schedules/{sid}")
def delete_schedule(sid: int):
    db.run("DELETE FROM schedules WHERE id = ?", sid)
    return {"ok": True}


@router.post("/schedules/{sid}/run")
async def run_schedule_now(sid: int):
    s = db.one("SELECT * FROM schedules WHERE id = ?", sid)
    if not s:
        raise HTTPException(404, "Not found")
    return {"output": await scheduler.run_schedule(s)}


@router.get("/inbox")
def inbox():
    return {"unread": db.one("SELECT COUNT(*) AS n FROM inbox WHERE read = 0")["n"],
            "items": db.all("SELECT * FROM inbox ORDER BY id DESC LIMIT 200")}


@router.post("/inbox/read")
def inbox_read():
    db.run("UPDATE inbox SET read = 1 WHERE read = 0")
    return {"ok": True}


@router.delete("/inbox/{iid}")
def inbox_delete(iid: int):
    db.run("DELETE FROM inbox WHERE id = ?", iid)
    return {"ok": True}


# Assistant settings: name, timezone, learning, search keys, Telegram, CLI token, prices
class AssistantSettings(BaseModel):
    assistant_name: str | None = Field(None, max_length=40)
    timezone: str | None = Field(None, max_length=64)
    learning_enabled: bool | None = None
    brave_api_key: str | None = Field(None, max_length=200)
    tavily_api_key: str | None = Field(None, max_length=200)
    telegram_token: str | None = Field(None, max_length=200)
    telegram_allowed_users: str | None = Field(None, max_length=500)


@router.get("/assistant/settings")
def get_assistant_settings():
    return {
        "assistant_name": db.get_setting("assistant_name", "Atlas"),
        "timezone": assistant.timezone_name(),
        "learning_enabled": learn.enabled(),
        "search_provider": search.provider(),
        "telegram": telegram.status(),
        "cli_token_set": bool(db.get_setting("cli_token_hash", "")),
    }


@router.put("/assistant/settings")
def put_assistant_settings(data: AssistantSettings):
    from zoneinfo import ZoneInfo

    if data.assistant_name is not None:
        db.set_setting("assistant_name", data.assistant_name.strip() or "Atlas")
    if data.timezone is not None:
        try:
            ZoneInfo(data.timezone)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"Unknown timezone {data.timezone}. Use an IANA name like Australia/Sydney.") from e
        db.set_setting("timezone", data.timezone)
        for s in db.all("SELECT id, cron FROM schedules"):
            db.run("UPDATE schedules SET next_run = ? WHERE id = ?", scheduler.next_run(s["cron"]), s["id"])
    if data.learning_enabled is not None:
        db.set_setting("learning_enabled", "1" if data.learning_enabled else "0")
    for field, name in (("brave_api_key", "BRAVE_API_KEY"), ("tavily_api_key", "TAVILY_API_KEY")):
        value = getattr(data, field)
        if value is not None:
            db.set_setting(f"secret:{name}", encrypt(value.strip()) if value.strip() else "")
    if data.telegram_token is not None or data.telegram_allowed_users is not None:
        telegram.configure(data.telegram_token, data.telegram_allowed_users)
    return get_assistant_settings()


@router.post("/cli-token")
def new_cli_token():
    token = "wb_" + secrets.token_urlsafe(32)
    db.set_setting("cli_token_hash", hashlib.sha256(token.encode()).hexdigest())
    return {"token": token, "note": "Shown once. Creating a new token revokes the old one."}


@router.get("/usage")
def usage_summary():
    return usage.summary()


class PricesIn(BaseModel):
    prices: dict[str, dict[str, float]]


@router.put("/usage/prices")
def set_prices(data: PricesIn):
    clean = {m.strip(): {"in": max(0.0, float(p.get("in", 0))), "out": max(0.0, float(p.get("out", 0)))}
             for m, p in data.prices.items() if m.strip()}
    db.set_setting("model_prices", json.dumps(clean))
    return usage.summary()


# Company
@router.get("/company")
def company_overview():
    return company.overview()


class MissionIn(BaseModel):
    mission: str = Field(..., max_length=4000)


@router.put("/company/mission")
def set_mission(data: MissionIn):
    db.set_setting("company_mission", data.mission.strip())
    return company.overview()


class AgentIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    title: str = Field("", max_length=80)
    reports_to: int | None = None
    model: str = Field("", max_length=300)
    instructions: str = Field("", max_length=8000)
    monthly_budget_cents: int = Field(0, ge=0, le=10_000_000)
    monthly_token_limit: int = Field(0, ge=0, le=10_000_000_000)


def _check_manager(agent_id: int | None, reports_to: int | None) -> None:
    seen, cur = set(), reports_to
    while cur:
        if cur == agent_id or cur in seen:
            raise HTTPException(400, "That would create a reporting loop.")
        seen.add(cur)
        row = db.one("SELECT reports_to FROM agents WHERE id = ?", cur)
        if not row:
            raise HTTPException(404, "Manager not found")
        cur = row["reports_to"]


@router.post("/company/agents")
def hire(data: AgentIn):
    _check_manager(None, data.reports_to)
    aid = db.run("INSERT INTO agents(name, title, reports_to, model, instructions, monthly_budget_cents, monthly_token_limit, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", data.name.strip(), data.title.strip(), data.reports_to, data.model,
                 data.instructions, data.monthly_budget_cents, data.monthly_token_limit, db.now())
    return db.one("SELECT * FROM agents WHERE id = ?", aid)


@router.put("/company/agents/{aid}")
def edit_agent(aid: int, data: AgentIn):
    _check_manager(aid, data.reports_to)
    db.run("UPDATE agents SET name = ?, title = ?, reports_to = ?, model = ?, instructions = ?, monthly_budget_cents = ?, "
           "monthly_token_limit = ? WHERE id = ?", data.name.strip(), data.title.strip(), data.reports_to, data.model,
           data.instructions, data.monthly_budget_cents, data.monthly_token_limit, aid)
    return db.one("SELECT * FROM agents WHERE id = ?", aid)


class AgentStatus(BaseModel):
    status: str = Field(..., pattern="^(active|paused)$")


@router.post("/company/agents/{aid}/status")
def set_agent_status(aid: int, data: AgentStatus):
    a = db.one("SELECT * FROM agents WHERE id = ?", aid)
    if not a:
        raise HTTPException(404, "Agent not found")
    if data.status == "active":
        reason = usage.over_budget(a)
        if reason:
            raise HTTPException(400, f"Still over budget: {reason}. Raise the budget first.")
    db.run("UPDATE agents SET status = ?, paused_reason = ? WHERE id = ?", data.status, "" if data.status == "active" else "Paused by the board", aid)
    return db.one("SELECT * FROM agents WHERE id = ?", aid)


@router.delete("/company/agents/{aid}")
def fire(aid: int):
    db.run("DELETE FROM agents WHERE id = ?", aid)
    return {"ok": True}


class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field("", max_length=10000)
    agent_id: int | None = None
    start: bool = False


@router.post("/company/tasks")
def add_company_task(data: TaskIn):
    return company.create_task(data.title, data.description, data.agent_id, queue=data.start)


class TaskPatch(BaseModel):
    agent_id: int | None = None
    status: str | None = Field(None, pattern="^(todo|queued|done|failed|blocked)$")


@router.patch("/company/tasks/{tid}")
def edit_company_task(tid: int, data: TaskPatch):
    t = db.one("SELECT * FROM agent_tasks WHERE id = ?", tid)
    if not t:
        raise HTTPException(404, "Task not found")
    if data.agent_id is not None:
        db.run("UPDATE agent_tasks SET agent_id = ?, updated_at = ? WHERE id = ?", data.agent_id or None, db.now(), tid)
    if data.status:
        if data.status == "queued" and not (data.agent_id or t["agent_id"]):
            raise HTTPException(400, "Assign the task to an agent first.")
        db.run("UPDATE agent_tasks SET status = ?, updated_at = ? WHERE id = ?", data.status, db.now(), tid)
    return db.one("SELECT * FROM agent_tasks WHERE id = ?", tid)


@router.delete("/company/tasks/{tid}")
def delete_company_task(tid: int):
    db.run("DELETE FROM agent_tasks WHERE id = ?", tid)
    return {"ok": True}
