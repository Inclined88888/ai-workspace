"""Data operations shared by the web API and the chat tool server."""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

from . import calendar_sync, db

DocKind = Literal["note", "document"]
TaskStatus = Literal["todo", "doing", "done"]


def _fts_query(q: str) -> str:
    tokens = re.findall(r"\w+", q, re.UNICODE)
    return " ".join(f'"{t}"*' for t in tokens[:12])


def _date(v: str | None) -> str | None:
    if v in (None, ""):
        return None
    try:
        return date.fromisoformat(v[:10]).isoformat()
    except ValueError as e:
        raise ValueError("Use YYYY-MM-DD") from e


# Docs (notes and documents)
class DocIn(BaseModel):
    kind: DocKind = "note"
    title: str = Field("", max_length=300)
    body: str = Field("", max_length=2_000_000)
    tags: str = Field("", max_length=500, description="Comma separated tags")
    pinned: bool = False


class DocPatch(BaseModel):
    title: str | None = Field(None, max_length=300)
    body: str | None = Field(None, max_length=2_000_000)
    tags: str | None = Field(None, max_length=500)
    pinned: bool | None = None
    kind: DocKind | None = None


def list_docs(kind: str | None = None, q: str = "", tag: str = "", limit: int = 200) -> list[dict]:
    where, args = [], []
    if kind:
        where.append("d.kind = ?")
        args.append(kind)
    if tag:
        where.append("(',' || REPLACE(d.tags, ' ', '') || ',') LIKE ?")
        args.append(f"%,{tag.strip()},%")
    fts = _fts_query(q) if q else ""
    if fts:
        where.append("d.id IN (SELECT rowid FROM docs_fts WHERE docs_fts MATCH ?)")
        args.append(fts)
    sql = (
        "SELECT d.id, d.kind, d.title, d.tags, d.pinned, d.created_at, d.updated_at, substr(d.body, 1, 240) AS preview "
        "FROM docs d" + (" WHERE " + " AND ".join(where) if where else "") +
        " ORDER BY d.pinned DESC, d.updated_at DESC LIMIT ?"
    )
    return db.all(sql, *args, min(limit, 500))


def get_doc(did: int) -> dict:
    d = db.one("SELECT * FROM docs WHERE id = ?", did)
    if not d:
        raise HTTPException(404, "Not found")
    return d


def create_doc(data: DocIn) -> dict:
    now = db.now()
    did = db.run(
        "INSERT INTO docs(kind, title, body, tags, pinned, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        data.kind, data.title.strip() or "Untitled", data.body, data.tags, int(data.pinned), now, now,
    )
    return get_doc(did)


def update_doc(did: int, patch: DocPatch) -> dict:
    get_doc(did)
    fields = patch.model_dump(exclude_none=True)
    if "pinned" in fields:
        fields["pinned"] = int(fields["pinned"])
    if fields:
        fields["updated_at"] = db.now()
        db.run(f"UPDATE docs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), did)
    return get_doc(did)


def delete_doc(did: int) -> None:
    get_doc(did)
    db.run("DELETE FROM docs WHERE id = ?", did)


# Tasks
class TaskIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    notes: str = Field("", max_length=20_000)
    status: TaskStatus = "todo"
    priority: int = Field(0, ge=0, le=3, description="0 none, 1 low, 2 medium, 3 high")
    due: str | None = Field(None, description="Due date YYYY-MM-DD")
    project: str = Field("", max_length=120)

    @field_validator("due")
    @classmethod
    def _v_due(cls, v: str | None) -> str | None:
        return _date(v)


class TaskPatch(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=500)
    notes: str | None = Field(None, max_length=20_000)
    status: TaskStatus | None = None
    priority: int | None = Field(None, ge=0, le=3)
    due: str | None = None
    project: str | None = Field(None, max_length=120)
    clear_due: bool = False

    @field_validator("due")
    @classmethod
    def _v_due(cls, v: str | None) -> str | None:
        return _date(v)


def list_tasks(status: str | None = None, project: str | None = None, include_done: bool = True) -> list[dict]:
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    elif not include_done:
        where.append("status != 'done'")
    if project:
        where.append("project = ?")
        args.append(project)
    sql = (
        "SELECT * FROM tasks" + (" WHERE " + " AND ".join(where) if where else "") +
        " ORDER BY status = 'done', due IS NULL, due, priority DESC, created_at DESC LIMIT 1000"
    )
    return db.all(sql, *args)


def get_task(tid: int) -> dict:
    t = db.one("SELECT * FROM tasks WHERE id = ?", tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


def create_task(data: TaskIn) -> dict:
    now = db.now()
    tid = db.run(
        "INSERT INTO tasks(title, notes, status, priority, due, project, created_at, updated_at, completed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        data.title.strip(), data.notes, data.status, data.priority, data.due, data.project.strip(), now, now,
        now if data.status == "done" else None,
    )
    return get_task(tid)


def update_task(tid: int, patch: TaskPatch) -> dict:
    current = get_task(tid)
    fields = patch.model_dump(exclude_none=True, exclude={"clear_due"})
    if patch.clear_due:
        fields["due"] = None
    if "status" in fields and fields["status"] != current["status"]:
        fields["completed_at"] = db.now() if fields["status"] == "done" else None
    if fields:
        fields["updated_at"] = db.now()
        db.run(f"UPDATE tasks SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), tid)
    return get_task(tid)


def delete_task(tid: int) -> None:
    get_task(tid)
    db.run("DELETE FROM tasks WHERE id = ?", tid)


# Events
class EventIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    start: str = Field(..., description="ISO 8601 datetime with offset (2026-05-01T09:00:00+10:00) or YYYY-MM-DD for all day")
    end: str | None = Field(None, description="Same format as start. Defaults to 1 hour (or 1 day) after start")
    all_day: bool = False
    location: str = Field("", max_length=300)
    description: str = Field("", max_length=5000)
    calendar_id: int | None = Field(None, description="Target calendar. Defaults to the local calendar")
    attendees: list[str] = Field(default_factory=list, max_length=50,
                                 description="Email addresses to invite. Google and Microsoft calendars email them an invitation")


def _norm_time(v: str, all_day: bool) -> str:
    if all_day:
        return date.fromisoformat(v[:10]).isoformat()
    dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise HTTPException(400, "Timed events need a UTC offset, e.g. 2026-05-01T09:00:00+10:00")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def local_calendar_id() -> int:
    c = db.one("SELECT id FROM calendars WHERE kind = 'local' ORDER BY id LIMIT 1")
    if c:
        return c["id"]
    return db.run("INSERT INTO calendars(name, kind, color) VALUES ('Personal', 'local', '#6366f1')")


def list_events(start: str, end: str) -> list[dict]:
    return db.all(
        "SELECT e.*, c.name AS calendar_name, c.color, c.kind AS calendar_kind FROM events e "
        "JOIN calendars c ON c.id = e.calendar_id WHERE c.enabled = 1 AND e.end >= ? AND e.start < ? ORDER BY e.start",
        start, end,
    )


def create_event(data: EventIn) -> dict:
    try:
        start = _norm_time(data.start, data.all_day)
        if data.end:
            end = _norm_time(data.end, data.all_day)
        elif data.all_day:
            end = (date.fromisoformat(start) + timedelta(days=1)).isoformat()
        else:
            end = (datetime.fromisoformat(start) + timedelta(hours=1)).isoformat(timespec="seconds")
    except ValueError as e:
        raise HTTPException(400, f"Invalid date: {e}") from e
    if end < start:
        raise HTTPException(400, "End is before start")
    cid = data.calendar_id
    if not cid and data.attendees:  # an invitation goes out from a real account, never the local calendar
        pick = db.one("SELECT id FROM calendars WHERE enabled = 1 AND kind IN ('google', 'microsoft', 'caldav') "
                      "ORDER BY CASE kind WHEN 'caldav' THEN 1 ELSE 0 END, id LIMIT 1")
        cid = pick["id"] if pick else None
    cid = cid or local_calendar_id()
    cal = db.one("SELECT * FROM calendars WHERE id = ?", cid)
    if not cal:
        raise HTTPException(404, "Calendar not found")
    if cal["kind"] == "ics":
        raise HTTPException(400, "ICS subscriptions are read only. Pick a local, CalDAV, Google or Microsoft calendar.")
    if data.attendees and cal["kind"] == "local":
        raise HTTPException(400, "The local calendar can't send invitations. Connect Google or Microsoft, or pick a CalDAV calendar.")
    if cal["kind"] in ("caldav", "google", "microsoft"):
        uid = calendar_sync.create_remote(cal, data.title, start, end, data.all_day, data.location, data.description,
                                          data.attendees)
        uid = f"{uid}|{start}"
    else:
        uid = f"{uuid.uuid4()}@local"
    eid = db.run(
        "INSERT INTO events(calendar_id, uid, title, start, end, all_day, location, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        cid, uid, data.title, start, end, int(data.all_day), data.location, data.description,
    )
    return db.one("SELECT * FROM events WHERE id = ?", eid)


def delete_event(eid: int) -> None:
    e = db.one("SELECT e.*, c.kind FROM events e JOIN calendars c ON c.id = e.calendar_id WHERE e.id = ?", eid)
    if not e:
        raise HTTPException(404, "Event not found")
    if e["kind"] != "local":
        raise HTTPException(400, "Only local events can be deleted here. Delete synced events in their source calendar.")
    db.run("DELETE FROM events WHERE id = ?", eid)


def agenda(days: int = 7) -> dict:
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=12)).isoformat(timespec="seconds")
    end = (now + timedelta(days=days)).isoformat(timespec="seconds")
    today = date.today().isoformat()
    return {
        "events": list_events(start, end),
        "tasks_due": db.all(
            "SELECT * FROM tasks WHERE status != 'done' AND due IS NOT NULL AND due <= ? ORDER BY due, priority DESC LIMIT 50",
            (date.today() + timedelta(days=days)).isoformat(),
        ),
        "overdue": db.one("SELECT COUNT(*) AS n FROM tasks WHERE status != 'done' AND due < ?", today)["n"],
        "open_tasks": db.one("SELECT COUNT(*) AS n FROM tasks WHERE status != 'done'")["n"],
    }
