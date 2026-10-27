"""OpenAPI tool server for Open WebUI. Mounted at /tools, spec at /tools/openapi.json.

Models can read and write notes, tasks and calendar events, search mail and save drafts.
Sending email is deliberately not exposed: a model can only prepare drafts that you review and send in ROOKS.
"""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, mail, permissions, research, services
from .security import require_tool_key


def _permission(request: Request) -> None:
    from fastapi.routing import APIRoute

    route = request.scope.get("route")
    if not isinstance(route, APIRoute):
        return  # the OpenAPI description, which holds no data
    # Fail closed: an operation that is not in the permission table is treated as a write that needs approval
    permissions.check_external(route.operation_id or route.name, "openwebui")

tools = FastAPI(
    title="ROOKS",
    version="1.0.0",
    description="Personal workspace tools: notes and documents, tasks, calendar, email search and drafts, web search.",
    dependencies=[Depends(require_tool_key), Depends(_permission)],
)


@tools.get("/notes/search", operation_id="search_notes", summary="Search the user's notes and documents by keywords")
def search_notes(query: str = "", kind: str | None = None, limit: int = 10) -> list[dict]:
    return services.list_docs(kind, query, limit=min(limit, 25))


@tools.get("/notes/{doc_id}", operation_id="read_note", summary="Read the full text of a note or document by id")
def read_note(doc_id: int) -> dict:
    return services.get_doc(doc_id)


@tools.post("/notes", operation_id="create_note", summary="Create a new note (kind 'note') or document (kind 'document')")
def create_note(data: services.DocIn) -> dict:
    d = services.create_doc(data)
    return {"id": d["id"], "title": d["title"], "kind": d["kind"]}


class AppendIn(BaseModel):
    text: str = Field(..., max_length=200_000, description="Markdown to append to the end of the note")


@tools.post("/notes/{doc_id}/append", operation_id="append_to_note", summary="Append text to an existing note or document")
def append_note(doc_id: int, data: AppendIn) -> dict:
    d = services.get_doc(doc_id)
    body = (d["body"].rstrip() + "\n\n" + data.text.strip()).strip()
    services.update_doc(doc_id, services.DocPatch(body=body))
    return {"id": doc_id, "ok": True}


@tools.get("/tasks", operation_id="list_tasks", summary="List open tasks (set include_done to also list completed ones)")
def list_tasks(include_done: bool = False, project: str | None = None) -> list[dict]:
    return services.list_tasks(project=project, include_done=include_done)[:100]


@tools.post("/tasks", operation_id="create_task", summary="Create a task with optional due date (YYYY-MM-DD), priority 0 to 3 and project")
def create_task(data: services.TaskIn) -> dict:
    return services.create_task(data)


@tools.patch("/tasks/{task_id}", operation_id="update_task", summary="Update a task, e.g. set status to 'done' to complete it")
def update_task(task_id: int, data: services.TaskPatch) -> dict:
    return services.update_task(task_id, data)


@tools.get("/calendar/events", operation_id="list_events",
           summary="List calendar events between two ISO dates. Defaults to the next 7 days. Times are UTC.")
def list_events(start: str | None = None, end: str | None = None) -> list[dict]:
    now = datetime.now(timezone.utc)
    s = start or now.isoformat(timespec="seconds")
    e = end or (now + timedelta(days=7)).isoformat(timespec="seconds")
    return [{k: ev[k] for k in ("id", "title", "start", "end", "all_day", "location", "calendar_name")}
            for ev in services.list_events(s, e)][:200]


@tools.post("/calendar/events", operation_id="create_event",
            summary="Create a calendar event. Timed events need an ISO datetime with a UTC offset.")
async def create_event(data: services.EventIn) -> dict:
    return await asyncio.to_thread(services.create_event, data)


@tools.get("/calendar/calendars", operation_id="list_calendars", summary="List calendars and whether they accept new events")
def list_calendars() -> list[dict]:
    services.local_calendar_id()
    return [{"id": c["id"], "name": c["name"], "writable": c["kind"] != "ics"} for c in db.all("SELECT * FROM calendars WHERE enabled = 1")]


@tools.get("/mail/search", operation_id="search_email", summary="Search recent email in the user's inbox (first account unless account_id is given)")
async def search_email(query: str = "", account_id: int | None = None) -> list[dict]:
    acct = db.one("SELECT * FROM mail_accounts WHERE id = ?", account_id) if account_id else db.one("SELECT * FROM mail_accounts ORDER BY id LIMIT 1")
    if not acct:
        raise HTTPException(404, "No mail account connected in ROOKS")
    messages = await asyncio.to_thread(mail.list_messages, acct, "INBOX", 20, query)
    return [m | {"account_id": acct["id"]} for m in messages]


@tools.get("/mail/{account_id}/messages/{uid}", operation_id="read_email", summary="Read one email by account id and uid")
async def read_email(account_id: int, uid: str) -> dict:
    acct = db.one("SELECT * FROM mail_accounts WHERE id = ?", account_id)
    if not acct:
        raise HTTPException(404, "Mail account not found")
    return await asyncio.to_thread(mail.get_message, acct, uid)


class DraftIn(BaseModel):
    to: str = Field(..., max_length=2000)
    subject: str = Field("", max_length=500)
    body: str = Field(..., max_length=200_000)
    in_reply_to: str = Field("", description="Message-ID being replied to, if any")
    account_id: int | None = None


@tools.post("/mail/drafts", operation_id="save_email_draft",
            summary="Save an email draft for the user to review and send in ROOKS. This never sends email.")
def save_draft(data: DraftIn) -> dict:
    acct = db.one("SELECT id FROM mail_accounts WHERE id = ?", data.account_id) if data.account_id else db.one("SELECT id FROM mail_accounts ORDER BY id LIMIT 1")
    now = db.now()
    did = db.run(
        "INSERT INTO mail_drafts(account_id, to_addr, subject, body, in_reply_to, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        acct["id"] if acct else None, data.to, data.subject, data.body, data.in_reply_to, now, now,
    )
    return {"draft_id": did, "status": "saved, not sent. The user reviews it under Mail > Drafts."}


@tools.get("/agenda", operation_id="get_agenda", summary="Get the user's agenda: upcoming events plus due and overdue tasks")
def get_agenda(days: int = 7) -> dict:
    return services.agenda(max(1, min(days, 60)))


@tools.get("/web/search", operation_id="web_search", summary="Search the web and return result titles, URLs and snippets")
async def web_search(query: str, count: int = 6) -> list[dict]:
    return await research.web_search(query, max(1, min(count, 10)))
