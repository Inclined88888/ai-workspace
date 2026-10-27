"""HTTP API for the assistant, memory, skills, schedules, inbox, Telegram, usage and the company.

Signed-in browser sessions and the terminal CLI (bearer token from Settings) can both call these routes.
"""

import hashlib
import hmac
import json
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from . import assistant, audit, company, db, files, learn, missions, permissions, scheduler, search, sensitive, telegram, usage
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
    file_ids: list[int] = Field(default_factory=list, max_length=10)
    project_id: int | None = None   # for a new conversation: start it in this project


@router.post("/assistant/chat")
def chat(data: ChatIn):
    from . import projects

    cid = data.conversation_id
    if cid:
        if not db.one("SELECT id FROM conversations WHERE id = ?", cid):
            raise HTTPException(404, "Conversation not found")
    else:
        if data.project_id:
            p = projects.get(data.project_id)
            if not p or p["status"] != "active":
                raise HTTPException(409, "That project is archived or gone. Restore it to work in it.")
        cid = assistant.new_conversation(data.channel, project_id=data.project_id or None)
    if data.model:
        db.run("UPDATE conversations SET model = ? WHERE id = ?", data.model, cid)
    message = data.message
    if data.file_ids:  # attached files travel inside the user's message, so history and search keep them
        message += "\n\n" + "\n\n".join(files.attachment_block(fid) for fid in dict.fromkeys(data.file_ids))

    async def events():
        yield {"type": "start", "conversation_id": cid}
        async for e in assistant.run_turn(assistant.Ctx(channel=data.channel, conversation_id=cid), message, data.model):
            yield e

    return sse(events())


@router.post("/assistant/stop/{cid}")
def stop(cid: int):
    """Stop a running turn after its current step. Steps already done are not undone."""
    assistant.request_stop(cid)
    return {"ok": True}


# Approvals: persistent actions requested in conversations that contain outside content
@router.get("/approvals")
def approvals(status: str = "pending"):
    rows = (db.all("SELECT * FROM approvals ORDER BY id DESC LIMIT 200") if status == "all" else
            db.all("SELECT * FROM approvals WHERE status = ? ORDER BY id DESC LIMIT 200", status))
    return [assistant.approval_view(r) for r in rows]  # Computer Use details only while the request is pending


class Decision(BaseModel):
    approve: bool


@router.post("/approvals/{aid}")
async def decide(aid: int, data: Decision):
    return await assistant.decide_approval(aid, data.approve)


# Files
@router.post("/files")
async def upload_file(request: Request, name: str = Query(..., max_length=255)):
    """Raw upload: the request body is the file. Keeps the core free of a multipart parser."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > files.MAX_BYTES:
            raise HTTPException(413, f"Files can be up to {files.MAX_BYTES // (1024 * 1024)} MB.")
    return files.save(name, (request.headers.get("content-type") or "application/octet-stream").split(";")[0].strip(), bytes(data))


@router.get("/files")
def list_files(q: str = ""):
    if q:
        return files.search(q, 50)
    return db.all("SELECT id, name, mime, size, note, created_at, length(text) AS chars FROM files ORDER BY id DESC LIMIT 500")


@router.get("/files/{fid}")
def file_detail(fid: int, start: int = 0):
    return {**files.meta(fid), **files.get_text(fid, start, 20000)}


@router.get("/files/{fid}/download")
def download_file(fid: int):
    from fastapi.responses import FileResponse

    f = db.one("SELECT name, mime, path FROM files WHERE id = ?", fid)
    if not f:
        raise HTTPException(404, "File not found")
    # Always a download, and never a type the browser would run (HTML, SVG): uploaded files are untrusted
    safe = f["mime"] if f["mime"] in ("application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain") else "application/octet-stream"
    return FileResponse(files.root() / f["path"], media_type=safe, filename=f["name"], content_disposition_type="attachment")


@router.delete("/files/{fid}")
def delete_file(fid: int):
    files.delete(fid)
    return {"ok": True}


@router.get("/conversations")
def conversations(channel: str | None = None, q: str = "", project_id: int | None = None):
    if project_id:
        return db.all("SELECT * FROM conversations WHERE project_id = ? ORDER BY updated_at DESC LIMIT 200", project_id)
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
    project_id: int | None = Field(None, ge=0)   # 0 takes it out of its project
    allow_cloud: bool = False                    # confirms moving out of a local-only project


@router.patch("/conversations/{cid}")
def rename(cid: int, data: ConvPatch):
    from . import projects

    c = db.one("SELECT * FROM conversations WHERE id = ?", cid)
    if not c:
        raise HTTPException(404, "Not found")
    if data.project_id is not None and data.project_id != (c["project_id"] or 0):
        target = projects.get(data.project_id) if data.project_id else None
        if data.project_id and not target:
            raise HTTPException(404, "Project not found")
        if projects.local_only(c["project_id"]) and not (target and target["local_only"]) and not data.allow_cloud:
            raise HTTPException(409, "This conversation is in a local-only project. Moving it out lets cloud models read it; confirm to move it.")
        db.run("UPDATE conversations SET project_id = ? WHERE id = ?", data.project_id or None, cid)
        audit.record("user", "conversation moved", f"#{cid}", target["name"] if target else "out of its project")
    if data.title is not None:
        db.run("UPDATE conversations SET title = ? WHERE id = ?", data.title.strip() or "Chat", cid)
    if data.model is not None:
        db.run("UPDATE conversations SET model = ? WHERE id = ?", data.model, cid)
    return db.one("SELECT * FROM conversations WHERE id = ?", cid)


@router.delete("/conversations/{cid}")
def delete_conversation(cid: int):
    db.run("DELETE FROM conversations WHERE id = ?", cid)
    return {"ok": True}


# Memory
class MemoryIn(BaseModel):
    content: str = Field(..., min_length=3, max_length=1000)
    kind: str = Field("fact", pattern="^(profile|fact|preference)$")
    project_id: int | None = None   # kept in this project only; none means everywhere


@router.get("/memories")
def memories(q: str = "", project_id: int | None = None):
    from .memory import EVERYWHERE

    if q:
        return assistant.search_memories(q, 50, project_id or EVERYWHERE)
    if project_id:
        return db.all("SELECT * FROM memories WHERE project_id = ? ORDER BY updated_at DESC LIMIT 500", project_id)
    return db.all("SELECT m.*, p.name AS project FROM memories m LEFT JOIN projects p ON p.id = m.project_id "
                  "ORDER BY m.kind = 'profile' DESC, m.updated_at DESC LIMIT 500")


@router.get("/memories/export")
def export_memories():
    rows = db.all("SELECT content, kind, source, conversation_id, created_at, updated_at FROM memories ORDER BY id")
    return {"format": "ai-workspace-memories", "version": 1, "exported_at": db.now(), "memories": rows}


class MemoryImport(BaseModel):
    memories: list[MemoryIn] = Field(..., max_length=5000)


def _no_secrets(text: str) -> None:
    if found := sensitive.find(text):
        raise HTTPException(400, sensitive.refuse_message(found))


@router.post("/memories/import")
def import_memories(data: MemoryImport):
    known = {learn._norm(m["content"]) for m in db.all("SELECT content FROM memories")}
    added = 0
    for m in data.memories:
        n = learn._norm(m.content)
        if not n or n in known or sensitive.find(m.content):
            continue
        db.run("INSERT INTO memories(content, kind, source, confidence, created_at, updated_at) VALUES (?, ?, 'import', 0.7, ?, ?)",
               m.content.strip(), m.kind, db.now(), db.now())
        known.add(n)
        added += 1
    return {"added": added, "skipped": len(data.memories) - added}


@router.post("/memories")
def add_memory(data: MemoryIn):
    _no_secrets(data.content)
    from . import projects

    pid = data.project_id or None
    if pid and not projects.get(pid):
        raise HTTPException(404, "Project not found")
    mid = db.run("INSERT INTO memories(content, kind, source, confidence, project_id, created_at, updated_at) VALUES (?, ?, 'user', 1.0, ?, ?, ?)",
                 data.content.strip(), data.kind, pid, db.now(), db.now())
    return db.one("SELECT * FROM memories WHERE id = ?", mid)


@router.put("/memories/{mid}")
def edit_memory(mid: int, data: MemoryIn):
    _no_secrets(data.content)
    old = db.one("SELECT content FROM memories WHERE id = ?", mid)
    if not old:
        raise HTTPException(404, "Memory not found")
    # A correction by the user is the strongest evidence there is; the earlier wording is kept so it can be undone
    db.run("UPDATE memories SET previous = CASE WHEN content != ? THEN content ELSE previous END, content = ?, kind = ?, "
           "source = 'user', confidence = 1.0, updated_at = ? WHERE id = ?", data.content.strip(), data.content.strip(), data.kind, db.now(), mid)
    audit.record("user", "memory corrected", f"#{mid}")
    return db.one("SELECT * FROM memories WHERE id = ?", mid)


@router.delete("/memories/{mid}")
def delete_memory(mid: int):
    if db.changed("DELETE FROM memories WHERE id = ?", mid):
        audit.record("user", "memory deleted", f"#{mid}")
    return {"ok": True}


@router.post("/memories/{mid}/revert")
def revert_memory(mid: int):
    """Undo the last update to a memory: its previous wording comes back (and the replaced one becomes previous)."""
    m = db.one("SELECT * FROM memories WHERE id = ?", mid)
    if not m:
        raise HTTPException(404, "Memory not found")
    if not m["previous"]:
        raise HTTPException(409, "This memory has no earlier version.")
    db.run("UPDATE memories SET content = previous, previous = content, updated_at = ? WHERE id = ?", db.now(), mid)
    audit.record("user", "memory reverted", f"#{mid}")
    return db.one("SELECT * FROM memories WHERE id = ?", mid)


@router.get("/memory/status")
def memory_status():
    from . import memory

    return memory.status()


class EmbedIn(BaseModel):
    model: str = Field("", max_length=300)


@router.put("/memory/embeddings")
def set_memory_embeddings(data: EmbedIn):
    """Turn meaning-based memory search on with a provider model (tested first), or off with an empty model."""
    from . import memory

    ref = data.model.strip()
    if ref:
        try:
            dim = len(memory.embed(["test"], ref)[0])
        except HTTPException as e:
            raise HTTPException(400, f"That model cannot make embeddings: {e.detail}") from e
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, f"That model cannot make embeddings: {str(e)[:200]}") from e
        if not dim:
            raise HTTPException(400, "That model returned an empty embedding.")
    db.set_setting("memory_embed_model", ref)
    audit.record("user", "memory search", "embeddings", ref or "off")
    return memory.status()


# Skills
class SkillIn(BaseModel):
    name: str = Field(..., max_length=50)
    description: str = Field(..., max_length=300)
    steps: str = Field(..., max_length=20000)
    enabled: bool = True


@router.get("/skills")
def skills():
    rows = db.all("SELECT * FROM skills ORDER BY uses DESC, updated_at DESC")
    for r in rows:
        r["warnings"] = learn.skill_warnings(r["steps"])
    return rows


@router.post("/skills")
def add_skill(data: SkillIn):
    return learn.upsert_skill(data.name, data.description, data.steps, source="user")


@router.put("/skills/{sid}")
def edit_skill(sid: int, data: SkillIn):
    learn.check_skill_text(data.description, data.steps)
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
    github_token: str | None = Field(None, max_length=300)


@router.get("/assistant/settings")
def get_assistant_settings():
    return {
        "assistant_name": assistant.assistant_name(),
        "timezone": assistant.timezone_name(),
        "learning_enabled": learn.enabled(),
        "search_provider": search.provider(),
        "search_providers": search.providers(),
        "telegram": telegram.status(),
        "cli_token_set": bool(db.get_setting("cli_token_hash", "")),
        "github": _github_state(),
    }


def _github_state() -> dict:
    from . import github

    try:
        chk = json.loads(db.get_setting("github_check", "") or "{}")
    except ValueError:
        chk = {}
    return {"connected": bool(github.token()), **chk}


@router.put("/assistant/settings")
def put_assistant_settings(data: AssistantSettings):
    from zoneinfo import ZoneInfo

    if data.assistant_name is not None:
        db.set_setting("assistant_name", data.assistant_name.strip() or "ROOKS")
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
            audit.record("user", "key saved" if value.strip() else "key removed", name)
    if data.github_token is not None:
        from . import github

        github.set_token(data.github_token)
        audit.record("user", "key saved" if data.github_token.strip() else "key removed", "GITHUB_TOKEN")
    if data.telegram_token is not None or data.telegram_allowed_users is not None:
        telegram.configure(data.telegram_token, data.telegram_allowed_users)
        audit.record("user", "telegram settings", "telegram", "token changed" if data.telegram_token is not None else "allowed users changed")
    return get_assistant_settings()


# Permissions and the audit trail
@router.get("/permissions")
def get_permissions():
    return {"groups": permissions.describe(), "modes": list(permissions.MODES)}


class PermissionsIn(BaseModel):
    groups: dict[str, str]


@router.put("/permissions")
def put_permissions(data: PermissionsIn):
    before = permissions.policy()
    after = permissions.set_policy(data.groups)
    for g, m in after.items():
        if before[g] != m:
            audit.record("user", "permission", g, f"{before[g]} -> {m}")
    return get_permissions()


@router.get("/audit")
def get_audit(limit: int = Query(200, ge=1, le=1000), actor: str = "", action: str = ""):
    return audit.recent(limit, actor, action)


class SearchTest(BaseModel):
    query: str = Field("ROOKS self-hosted AI workspace", max_length=200)
    provider: str = Field("", pattern="^(|searxng|brave|tavily)$")


@router.post("/search/test")
async def search_test(data: SearchTest):
    """Run one real search so the settings page only says it works after it did."""
    try:
        items = await search.web_search(data.query, 5, data.provider)
    except HTTPException as e:
        return {"ok": False, "error": str(e.detail), "providers": search.providers()}
    return {"ok": True, "results": items, "providers": search.providers(), "provider": data.provider or search.provider()}


@router.post("/cli-token")
def new_cli_token():
    token = "wb_" + secrets.token_urlsafe(32)
    db.set_setting("cli_token_hash", hashlib.sha256(token.encode()).hexdigest())
    return {"token": token, "note": "Shown once. Creating a new token revokes the old one."}


@router.get("/usage")
def usage_summary():
    return {**usage.summary(), "limits": _limits()}


def _limits() -> dict:
    return {"monthly_limit_cents": float(db.get_setting("monthly_limit_cents", "0") or 0),
            "fallback_allow_paid": db.get_setting("fallback_allow_paid", "0") == "1"}


class LimitsIn(BaseModel):
    monthly_limit_cents: float | None = Field(None, ge=0, le=10_000_000)
    fallback_allow_paid: bool | None = None


@router.put("/usage/limits")
def set_limits(data: LimitsIn):
    if data.monthly_limit_cents is not None:
        db.set_setting("monthly_limit_cents", str(data.monthly_limit_cents))
        audit.record("user", "spending limit", "models", f"{data.monthly_limit_cents:g} cents a month" if data.monthly_limit_cents else "none")
    if data.fallback_allow_paid is not None:
        db.set_setting("fallback_allow_paid", "1" if data.fallback_allow_paid else "0")
        audit.record("user", "backup models", "models", "may use paid models" if data.fallback_allow_paid else "free only after a free default")
    return _limits()


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


@router.get("/connections")
def connections():
    """Everything ROOKS is connected to, with its last known health and the permission that governs it.
    Built from stored state only: opening the page never calls an outside service."""
    from . import config, llm

    pol = permissions.policy()
    out = []
    for p in db.all("SELECT id, name, enabled FROM providers ORDER BY id"):
        chk = llm.check_info(p["id"])
        status = "off" if not p["enabled"] else ("ok" if chk.get("ok") else "error" if chk else "warn")
        detail = "Turned off" if not p["enabled"] else (f"Working, using {chk.get('recommended', '')}" if chk.get("ok")
                                                        else (chk.get("error") or "")[:200] if chk else "Not checked yet")
        out.append({"id": f"provider:{p['id']}", "kind": "Model provider", "name": p["name"], "status": status, "detail": detail,
                    "settings": "#models"})
    found = search.providers()
    out.append({"id": "search", "kind": "Web search", "name": ", ".join(search.NAMES[x] for x in found) or "Web search",
                "status": ("off" if pol["web"] == "off" else "ok") if found else "warn",
                "detail": "Set up" if found else "Not set up", "group": "web", "settings": "#search"})
    for c in db.all("SELECT name, kind, enabled, last_synced, last_error FROM calendars WHERE kind != 'local'"):
        out.append({"id": f"calendar:{c['name']}", "kind": "Calendar", "name": c["name"], "group": "calendar",
                    "status": "off" if not c["enabled"] else "error" if c["last_error"] else "ok" if c["last_synced"] else "warn",
                    "detail": (c["last_error"] or "")[:200] or (f"Synced {c['last_synced']}" if c["last_synced"] else "Not synced yet"),
                    "settings": "/calendar"})
    for m in db.all("SELECT name, email FROM mail_accounts"):
        out.append({"id": f"mail:{m['email']}", "kind": "Email", "name": m["name"], "group": "email", "status": "ok",
                    "detail": m["email"], "settings": "/mail"})
    tg = telegram.status()
    if tg["configured"]:
        out.append({"id": "telegram", "kind": "Chat app", "name": "Telegram", "settings": "#telegram",
                    "status": "ok" if tg["running"] else "error" if tg["error"] else "warn",
                    "detail": f"@{tg['bot']}" if tg["running"] else tg["error"][:200] or "Starting"})
    gh = _github_state()
    if gh["connected"]:
        out.append({"id": "github", "kind": "Integration", "name": "GitHub", "group": "github", "settings": "#github",
                    "status": "ok" if gh.get("ok") else "error" if gh.get("error") else "warn",
                    "detail": f"@{gh['login']}" if gh.get("ok") else gh.get("error", "") or "Not checked yet"})
    out.append({"id": "browser", "kind": "Add-on", "name": "Browser", "group": "browser",
                "status": ("off" if pol["browser"] == "off" else "ok") if config.BROWSER_URL else "off",
                "detail": "Installed" if config.BROWSER_URL else "Not installed (optional add-on)"})
    for item in out:
        if item.get("group"):
            item["permission"] = pol[item["group"]]
    return out


# Missions
class MissionIn(BaseModel):
    goal: str = Field(..., min_length=5, max_length=4000)
    model: str | None = None
    auto_start: bool = False
    budget_tool_calls: int | None = Field(None, ge=1, le=500)
    budget_minutes: int | None = Field(None, ge=1, le=24 * 60)
    budget_cents: int | None = Field(None, ge=0, le=100_000)
    project_id: int | None = None
    max_attempts: int = Field(2, ge=1, le=5)
    review_unverified: bool = False


class MissionStep(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    done_when: str = Field("", max_length=500)
    needs_tool: bool = True
    after: list[int] = Field(default_factory=list, max_length=10)


class MissionPlanIn(BaseModel):
    steps: list[MissionStep] = Field(..., min_length=1, max_length=10)


class MissionBudgetIn(BaseModel):
    budget_tool_calls: int | None = Field(None, ge=1, le=500)
    budget_minutes: int | None = Field(None, ge=1, le=24 * 60)
    budget_cents: int | None = Field(None, ge=0, le=100_000)


@router.post("/missions")
async def create_mission(data: MissionIn):
    return missions.create(data.goal, data.model, data.auto_start, project_id=data.project_id, max_attempts=data.max_attempts,
                           review_unverified=data.review_unverified, budget_tool_calls=data.budget_tool_calls,
                           budget_minutes=data.budget_minutes, budget_cents=data.budget_cents)


@router.get("/missions")
def list_missions(project_id: int | None = None):
    return missions.listing(project_id)


@router.get("/templates")
def list_templates():
    from . import templates

    return templates.listing()


class TemplateStart(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)
    project_id: int | None = None
    auto_start: bool = False
    budget_tool_calls: int | None = Field(None, ge=1, le=500)
    budget_minutes: int | None = Field(None, ge=1, le=24 * 60)
    budget_cents: int | None = Field(None, ge=0, le=100_000)


@router.post("/templates/{tid}/start")
async def start_template(tid: str, data: TemplateStart):
    from . import templates

    return templates.start(tid, data.values, data.project_id, data.auto_start, budget_tool_calls=data.budget_tool_calls,
                           budget_minutes=data.budget_minutes, budget_cents=data.budget_cents)


class StepReview(BaseModel):
    accept: bool
    note: str = Field("", max_length=500)


@router.post("/missions/{mid}/steps/{i}/review")
def review_mission_step(mid: int, i: int, data: StepReview):
    return missions.review_step(mid, i, data.accept, data.note)


@router.get("/missions/{mid}")
def get_mission(mid: int):
    return missions.view(mid)


@router.put("/missions/{mid}/plan")
def edit_mission_plan(mid: int, data: MissionPlanIn):
    return missions.update_plan(mid, [s.model_dump() for s in data.steps])


@router.put("/missions/{mid}/budget")
def edit_mission_budget(mid: int, data: MissionBudgetIn):
    return missions.update_budget(mid, **data.model_dump())


@router.post("/missions/{mid}/start")
async def start_mission(mid: int):
    return missions.start(mid)


@router.post("/missions/{mid}/pause")
def pause_mission(mid: int):
    return missions.pause(mid)


@router.post("/missions/{mid}/cancel")
def cancel_mission(mid: int):
    return missions.cancel(mid)


@router.delete("/missions/{mid}")
def delete_mission(mid: int):
    missions.delete(mid)
    return {"ok": True}


# Tool registry and log
@router.get("/tools")
def tool_registry():
    """Every assistant tool with its purpose, parameters, risk, time limit and current permission."""
    ctx = assistant.Ctx(channel="web", conversation_id=0)
    out = []
    for t in assistant.TOOLS:
        g = permissions.TOOL_GROUP.get(t.name, "")
        out.append({"name": t.name, "description": t.description, "group": g, "group_label": permissions.GROUPS[g][0] if g else "",
                    "risk": permissions.TOOL_RISK.get(t.name, "write"), "params": t.params, "required": list(t.required),
                    "timeout": t.timeout, "permission": permissions.mode(t.name), "always_asks": t.name in permissions.ALWAYS_ASK,
                    "available": t.when(ctx), "in_chat": t.when(ctx) and permissions.allowed(t.name)})
    return out


@router.get("/tool-runs")
def tool_runs(tool: str = "", limit: int = Query(100, ge=1, le=1000), failed: bool = False):
    where, args = [], []
    if tool:
        where.append("tool = ?")
        args.append(tool)
    if failed:
        where.append("ok = 0")
    sql = "SELECT * FROM tool_runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return db.all(sql, *args, limit)


@router.post("/github/test")
async def github_test():
    from . import github

    if not github.token():
        raise HTTPException(400, "Add a GitHub token first.")
    return await github.check()


# Workspace and terminal (workspace.py; executed by the runner service)
@router.get("/workspace")
async def workspace_status():
    from . import workspace

    pol = permissions.policy()
    return {"runner": await workspace.health(), "settings": workspace.settings(), "risky": [why for _, why in workspace.RISKY],
            "terminal_mode": pol.get("terminal"), "files_mode": pol.get("workspace")}


class WorkspaceSettingsIn(BaseModel):
    allow: list[str] | None = Field(None, max_length=100)
    default_timeout: int | None = Field(None, ge=5, le=900)


@router.put("/workspace/settings")
def workspace_settings(data: WorkspaceSettingsIn):
    from . import workspace

    out = workspace.set_settings(data.allow, data.default_timeout)
    audit.record("user", "terminal settings", "", f"{len(out['allow'])} commands without asking, timeout {out['default_timeout']} s")
    return out


@router.get("/workspace/files")
async def workspace_files(path: str = Query("", max_length=1000)):
    """What is in the workspace, for you to look at (read only)."""
    from . import workspace

    return await workspace._call("/fs/list", {"path": path or "."})


@router.get("/workspace/file")
async def workspace_file(path: str = Query(..., max_length=1000), offset: int = Query(1, ge=1)):
    from . import workspace

    return await workspace._call("/fs/read", {"path": path, "offset": offset, "limit": 2000})
