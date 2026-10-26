import asyncio
import json
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import calendar_sync, config, db, llm, mail, research, services
from .security import (
    COOKIE, check_session, decrypt, encrypt, hash_password, make_session, require_user, throttle, verify_password,
)

public = APIRouter(prefix="/api")
api = APIRouter(prefix="/api", dependencies=[Depends(require_user)])
SETUP_CODE = os.getenv("SETUP_CODE", "")
_tasks: set[asyncio.Task] = set()


def sse(gen):
    async def body():
        try:
            async for item in gen:
                yield f"data: {json.dumps(item)}\n\n"
        except HTTPException as e:
            yield f"data: {json.dumps({'error': e.detail})}\n\n"
        except Exception as e:  # noqa: BLE001 - report to the client instead of dropping the stream
            yield f"data: {json.dumps({'error': str(e) or e.__class__.__name__})}\n\n"

    return StreamingResponse(body(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _login(response: Response) -> None:
    response.set_cookie(
        COOKIE, make_session(), max_age=config.SESSION_DAYS * 86400, httponly=True, samesite="lax",
        secure=config.COOKIE_SECURE, path="/",
    )


# Status, setup, auth
@public.get("/status")
def status(request: Request):
    return {
        "setup_complete": db.get_setting("setup_complete") == "1",
        "signed_in": check_session(request.cookies.get(COOKIE)),
        "setup_code_required": bool(SETUP_CODE),
        "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME),
        "chat_url": config.CHAT_URL,
        "paperclip_url": config.PAPERCLIP_URL,
        "hermes_url": config.HERMES_URL,
        "browser_enabled": bool(config.BROWSER_API_KEY),
    }


class SetupIn(BaseModel):
    password: str = Field(..., min_length=10, max_length=200)
    setup_code: str = ""
    workspace_name: str = Field("", max_length=80)


@public.post("/setup")
def setup(data: SetupIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    if db.get_setting("setup_complete") == "1":
        raise HTTPException(409, "Setup already completed")
    if SETUP_CODE and not secrets.compare_digest(data.setup_code.strip(), SETUP_CODE):
        raise HTTPException(403, "Setup code is wrong. It is printed by setup.sh and stored as SETUP_CODE in .env")
    db.set_setting("password_hash", hash_password(data.password))
    if data.workspace_name.strip():
        db.set_setting("workspace_name", data.workspace_name.strip())
    db.set_setting("setup_complete", "1")
    services.local_calendar_id()
    _login(response)
    return {"ok": True}


class LoginIn(BaseModel):
    password: str


@public.post("/login")
def login(data: LoginIn, request: Request, response: Response):
    throttle(request.client.host if request.client else "?")
    stored = db.get_setting("password_hash")
    if not stored or not verify_password(data.password, stored):
        raise HTTPException(401, "Wrong password")
    _login(response)
    return {"ok": True}


@public.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class PasswordIn(BaseModel):
    current: str
    new: str = Field(..., min_length=10, max_length=200)


@api.post("/password")
def change_password(data: PasswordIn, response: Response):
    if not verify_password(data.current, db.get_setting("password_hash", "")):
        raise HTTPException(401, "Current password is wrong")
    db.set_setting("password_hash", hash_password(data.new))
    db.set_setting("session_generation", secrets.token_hex(8))
    _login(response)
    return {"ok": True}


# Settings and providers
@api.get("/settings")
def get_settings():
    return {
        "workspace_name": db.get_setting("workspace_name", config.WORKSPACE_NAME),
        "default_model": db.get_setting("default_model", ""),
        "chat_url": config.CHAT_URL,
        "tools_url": "http://workbench:8000/tools/openapi.json",
    }


class SettingsIn(BaseModel):
    workspace_name: str | None = Field(None, max_length=80)
    default_model: str | None = Field(None, max_length=300)


@api.put("/settings")
def put_settings(data: SettingsIn):
    if data.workspace_name is not None:
        db.set_setting("workspace_name", data.workspace_name.strip() or config.WORKSPACE_NAME)
    if data.default_model is not None:
        if data.default_model:
            llm.split_ref(data.default_model)
        db.set_setting("default_model", data.default_model)
    return get_settings()


def _provider_out(p: dict) -> dict:
    key = decrypt(p["api_key_enc"])
    return {**{k: p[k] for k in ("id", "name", "kind", "base_url", "models", "enabled")},
            "has_key": bool(key), "key_hint": f"…{key[-4:]}" if len(key) > 8 else ""}


@api.get("/providers")
def providers():
    return {"providers": [_provider_out(p) for p in db.all("SELECT * FROM providers ORDER BY id")], "presets": llm.PRESETS}


class ProviderIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: Literal["openai", "anthropic", "ollama"] = "openai"
    base_url: str = Field(..., pattern=r"^https?://", max_length=300)
    api_key: str | None = Field(None, max_length=500)
    models: str = Field("", max_length=2000)
    enabled: bool = True


@api.post("/providers")
def add_provider(data: ProviderIn):
    pid = db.run(
        "INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data.name, data.kind, data.base_url.rstrip("/"), encrypt(data.api_key or ""), data.models, int(data.enabled), db.now(),
    )
    return _provider_out(db.one("SELECT * FROM providers WHERE id = ?", pid))


@api.put("/providers/{pid}")
def edit_provider(pid: int, data: ProviderIn):
    if not db.one("SELECT id FROM providers WHERE id = ?", pid):
        raise HTTPException(404, "Provider not found")
    db.run(
        "UPDATE providers SET name = ?, kind = ?, base_url = ?, models = ?, enabled = ? WHERE id = ?",
        data.name, data.kind, data.base_url.rstrip("/"), data.models, int(data.enabled), pid,
    )
    if data.api_key is not None:
        db.run("UPDATE providers SET api_key_enc = ? WHERE id = ?", encrypt(data.api_key), pid)
    llm._model_cache.pop(pid, None)
    return _provider_out(db.one("SELECT * FROM providers WHERE id = ?", pid))


@api.delete("/providers/{pid}")
def delete_provider(pid: int):
    db.run("DELETE FROM providers WHERE id = ?", pid)
    llm._model_cache.pop(pid, None)
    return {"ok": True}


@api.get("/models")
def models(fresh: bool = False):
    try:
        default = llm.default_model_ref()
    except HTTPException:
        default = ""
    return {"models": llm.list_models(fresh), "default": default}


@api.get("/ollama/status")
async def ollama_status():
    import httpx

    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"{config.OLLAMA_URL}/api/tags")
        return {"online": True, "models": [m["name"] for m in r.json().get("models", [])]}
    except (httpx.HTTPError, ValueError):
        return {"online": False, "models": []}


class PullIn(BaseModel):
    model: str = Field(..., pattern=r"^[\w.\-/:]+$", max_length=200)


@api.post("/ollama/pull")
async def ollama_pull(data: PullIn):
    import httpx

    async def gen():
        async with httpx.AsyncClient(timeout=httpx.Timeout(None, connect=10)) as c:
            async with c.stream("POST", f"{config.OLLAMA_URL}/api/pull", json={"model": data.model, "stream": True}) as r:
                async for line in r.aiter_lines():
                    if line.strip():
                        try:
                            yield json.loads(line)
                        except ValueError:
                            continue
        for p in db.all("SELECT id FROM providers WHERE kind = 'ollama'"):
            llm._model_cache.pop(p["id"], None)
        yield {"done": True}

    return sse(gen())


# Dashboard
@api.get("/dashboard")
def dashboard():
    return {
        **services.agenda(7),
        "recent_docs": services.list_docs(limit=6),
        "research": db.all("SELECT id, question, status, created_at FROM research ORDER BY id DESC LIMIT 5"),
    }


# Docs
@api.get("/docs")
def list_docs(kind: str | None = None, q: str = "", tag: str = ""):
    return services.list_docs(kind, q, tag)


@api.post("/docs")
def create_doc(data: services.DocIn):
    return services.create_doc(data)


@api.get("/docs/{did}")
def get_doc(did: int):
    return services.get_doc(did)


@api.patch("/docs/{did}")
def update_doc(did: int, data: services.DocPatch):
    return services.update_doc(did, data)


@api.delete("/docs/{did}")
def delete_doc(did: int):
    services.delete_doc(did)
    return {"ok": True}


ASSIST = {
    "improve": "Improve the clarity, flow and word choice of the text. Keep the meaning, facts and voice.",
    "shorten": "Make the text substantially shorter while keeping every important point.",
    "expand": "Expand the text with more useful detail and explanation. Do not invent facts, names or numbers.",
    "summarize": "Summarize the text as a few tight bullet points.",
    "fix": "Fix spelling, grammar and punctuation only. Change nothing else.",
    "continue": "Continue writing from where the text ends, matching its style. Output only the continuation.",
    "outline": "Turn the topic or notes into a clear Markdown outline for a document.",
    "custom": "Follow the user's instruction for the text.",
}


class AssistIn(BaseModel):
    action: Literal["improve", "shorten", "expand", "summarize", "fix", "continue", "outline", "custom"]
    text: str = Field(..., max_length=200_000)
    instruction: str = Field("", max_length=4000)
    model: str | None = None


@api.post("/ai/assist")
def assist(data: AssistIn):
    system = (
        "You are a precise writing assistant inside a document editor. " + ASSIST[data.action] +
        " Output only the resulting Markdown text with no preamble, no explanation and no code fences."
    )
    user = data.text if data.action != "custom" else f"Instruction: {data.instruction}\n\nText:\n{data.text}"
    return sse(llm.stream(data.model, [{"role": "system", "content": system}, {"role": "user", "content": user}]))


# Tasks
@api.get("/tasks")
def list_tasks(status: str | None = None, project: str | None = None):
    return services.list_tasks(status, project)


@api.post("/tasks")
def create_task(data: services.TaskIn):
    return services.create_task(data)


@api.patch("/tasks/{tid}")
def update_task(tid: int, data: services.TaskPatch):
    return services.update_task(tid, data)


@api.delete("/tasks/{tid}")
def delete_task(tid: int):
    services.delete_task(tid)
    return {"ok": True}


class BreakdownIn(BaseModel):
    goal: str = Field(..., min_length=3, max_length=4000)
    project: str = Field("", max_length=120)
    model: str | None = None


@api.post("/tasks/breakdown")
async def breakdown(data: BreakdownIn):
    out = await llm.complete(data.model, [
        {"role": "system", "content": "Break goals into concrete next actions. Reply with only a JSON array of short task titles (max 10)."},
        {"role": "user", "content": data.goal},
    ])
    titles = [t for t in (llm.parse_json(out) or []) if isinstance(t, str) and t.strip()][:10]
    if not titles:
        raise HTTPException(502, "The model did not return a task list. Try again or pick another model.")
    return [services.create_task(services.TaskIn(title=t[:500], project=data.project)) for t in titles]


# Calendar
def _calendar_out(c: dict) -> dict:
    return {k: v for k, v in c.items() if k != "password_enc"} | {"has_password": bool(c["password_enc"])}


@api.get("/calendars")
def calendars():
    services.local_calendar_id()
    return [_calendar_out(c) for c in db.all("SELECT * FROM calendars ORDER BY id")]


class CalendarIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    kind: Literal["ics", "caldav", "local"]
    url: str = Field("", max_length=1000)
    username: str = Field("", max_length=200)
    password: str | None = Field(None, max_length=500)
    color: str = Field("#6366f1", pattern=r"^#[0-9a-fA-F]{6}$")
    enabled: bool = True


@api.post("/calendars")
async def add_calendar(data: CalendarIn):
    if data.kind != "local" and not data.url.startswith(("http://", "https://", "webcal://")):
        raise HTTPException(400, "Calendar URL must start with https://, http:// or webcal://")
    cid = db.run(
        "INSERT INTO calendars(name, kind, url, username, password_enc, color, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data.name, data.kind, data.url, data.username, encrypt(data.password or ""), data.color, int(data.enabled),
    )
    result = {"synced": 0}
    if data.kind != "local":
        try:
            result = await asyncio.to_thread(calendar_sync.sync, cid)
        except HTTPException as e:
            result = {"error": e.detail}
    return {**_calendar_out(db.one("SELECT * FROM calendars WHERE id = ?", cid)), **result}


@api.put("/calendars/{cid}")
def edit_calendar(cid: int, data: CalendarIn):
    if not db.one("SELECT id FROM calendars WHERE id = ?", cid):
        raise HTTPException(404, "Calendar not found")
    db.run("UPDATE calendars SET name = ?, url = ?, username = ?, color = ?, enabled = ? WHERE id = ?",
           data.name, data.url, data.username, data.color, int(data.enabled), cid)
    if data.password is not None:
        db.run("UPDATE calendars SET password_enc = ? WHERE id = ?", encrypt(data.password), cid)
    return _calendar_out(db.one("SELECT * FROM calendars WHERE id = ?", cid))


@api.delete("/calendars/{cid}")
def delete_calendar(cid: int):
    db.run("DELETE FROM calendars WHERE id = ?", cid)
    return {"ok": True}


@api.post("/calendars/{cid}/sync")
async def sync_calendar(cid: int):
    return await asyncio.to_thread(calendar_sync.sync, cid)


class DiscoverIn(BaseModel):
    url: str = Field(..., pattern=r"^https?://")
    username: str = ""
    password: str = ""


@api.post("/calendars/discover")
async def discover(data: DiscoverIn):
    return await asyncio.to_thread(calendar_sync.discover, data.url, data.username, data.password)


@api.get("/events")
def events(start: str, end: str):
    return services.list_events(start, end)


@api.post("/events")
async def create_event(data: services.EventIn):
    return await asyncio.to_thread(services.create_event, data)


@api.delete("/events/{eid}")
def delete_event(eid: int):
    services.delete_event(eid)
    return {"ok": True}


# Mail
def _account(aid: int) -> dict:
    a = db.one("SELECT * FROM mail_accounts WHERE id = ?", aid)
    if not a:
        raise HTTPException(404, "Mail account not found")
    return a


@api.get("/mail/accounts")
def mail_accounts():
    return [{k: v for k, v in a.items() if k != "password_enc"} for a in db.all("SELECT * FROM mail_accounts ORDER BY id")]


class MailAccountIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+$")
    imap_host: str = Field(..., min_length=3)
    imap_port: int = 993
    smtp_host: str = Field(..., min_length=3)
    smtp_port: int = 465
    smtp_security: Literal["ssl", "starttls"] = "ssl"
    username: str = Field(..., min_length=1)
    password: str = Field(..., min_length=1)


@api.post("/mail/accounts")
async def add_mail_account(data: MailAccountIn):
    row = data.model_dump(exclude={"password"}) | {"password_enc": encrypt(data.password)}
    await asyncio.to_thread(mail.test, row)
    aid = db.run(
        "INSERT INTO mail_accounts(name, email, imap_host, imap_port, smtp_host, smtp_port, smtp_security, username, password_enc) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        row["name"], row["email"], row["imap_host"], row["imap_port"], row["smtp_host"], row["smtp_port"],
        row["smtp_security"], row["username"], row["password_enc"],
    )
    return {"id": aid}


@api.delete("/mail/accounts/{aid}")
def delete_mail_account(aid: int):
    db.run("DELETE FROM mail_accounts WHERE id = ?", aid)
    return {"ok": True}


@api.get("/mail/{aid}/messages")
async def mail_list(aid: int, folder: str = "INBOX", q: str = ""):
    return await asyncio.to_thread(mail.list_messages, _account(aid), folder, 50, q)


@api.get("/mail/{aid}/messages/{uid}")
async def mail_get(aid: int, uid: str, folder: str = "INBOX"):
    return await asyncio.to_thread(mail.get_message, _account(aid), uid, folder)


class SendIn(BaseModel):
    to: str = Field(..., min_length=3, max_length=2000)
    subject: str = Field("", max_length=500)
    body: str = Field(..., max_length=500_000)
    in_reply_to: str = ""
    draft_id: int | None = None


@api.post("/mail/{aid}/send")
async def mail_send(aid: int, data: SendIn):
    mid = await asyncio.to_thread(mail.send, _account(aid), data.to, data.subject, data.body, data.in_reply_to)
    if data.draft_id:
        db.run("DELETE FROM mail_drafts WHERE id = ?", data.draft_id)
    return {"ok": True, "message_id": mid}


class ReplyIn(BaseModel):
    message: str = Field(..., max_length=100_000)
    intent: str = Field("", max_length=2000, description="What the reply should say")
    model: str | None = None


@api.post("/mail/draft-reply")
def draft_reply(data: ReplyIn):
    return sse(llm.stream(data.model, [
        {"role": "system", "content": (
            "Draft an email reply. Be clear, direct and polite. Match the formality of the original. "
            "Never invent commitments, dates, prices or facts that the user did not provide; use [placeholders] instead. "
            "Output only the reply body, no subject line."
        )},
        {"role": "user", "content": f"Original email:\n{data.message}\n\nWhat my reply should say: {data.intent or 'a suitable reply'}"},
    ]))


@api.get("/mail/drafts")
def drafts():
    return db.all("SELECT * FROM mail_drafts ORDER BY updated_at DESC")


@api.delete("/mail/drafts/{did}")
def delete_draft(did: int):
    db.run("DELETE FROM mail_drafts WHERE id = ?", did)
    return {"ok": True}


# Research
class ResearchIn(BaseModel):
    question: str = Field(..., min_length=5, max_length=4000)
    model: str | None = None
    depth: int = Field(3, ge=1, le=6, description="Number of follow up search queries")


@api.post("/research")
def start_research(data: ResearchIn):
    model = data.model or llm.default_model_ref()
    llm.split_ref(model)
    now = db.now()
    rid = db.run("INSERT INTO research(question, model, created_at, updated_at) VALUES (?, ?, ?, ?)", data.question, model, now, now)
    task = asyncio.get_running_loop().create_task(research.run(rid, data.question, model, data.depth))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return db.one("SELECT * FROM research WHERE id = ?", rid)


@api.get("/research")
def list_research():
    return db.all("SELECT id, question, model, status, progress, created_at FROM research ORDER BY id DESC LIMIT 100")


@api.get("/research/{rid}")
def get_research(rid: int):
    r = db.one("SELECT * FROM research WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    return r | {"sources": json.loads(r["sources"])}


@api.delete("/research/{rid}")
def delete_research(rid: int):
    db.run("DELETE FROM research WHERE id = ?", rid)
    return {"ok": True}


@api.post("/research/{rid}/save")
def save_research(rid: int):
    r = get_research(rid)
    if r["status"] != "done":
        raise HTTPException(400, "Research is not finished")
    return services.create_doc(services.DocIn(kind="document", title=r["question"][:300], body=r["report"], tags="research"))


# Compare
class CompareIn(BaseModel):
    model: str
    messages: list[dict] = Field(..., min_length=1)
    system: str = Field("", max_length=20_000)


@api.post("/compare/stream")
def compare(data: CompareIn):
    msgs = [{"role": m.get("role", "user"), "content": str(m.get("content", ""))} for m in data.messages
            if m.get("role") in ("user", "assistant")]
    if data.system.strip():
        msgs.insert(0, {"role": "system", "content": data.system})
    return sse(llm.stream(data.model, msgs))


async def calendar_loop():
    while True:
        await asyncio.sleep(config.CALENDAR_SYNC_MINUTES * 60)
        await asyncio.to_thread(calendar_sync.sync_all)


def recover_interrupted():
    db.run("UPDATE research SET status = 'error', error = 'Interrupted by a restart', progress = 'Failed' WHERE status = 'running'")

