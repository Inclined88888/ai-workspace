"""/api/learn: What I know about you, Show me how, suggestions and feedback."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import require_user
from . import feedback, jobs, knowledge, playbooks, recorder, store

router = APIRouter(prefix="/api/learn", dependencies=[Depends(require_user), Depends(lambda: store.ensure())])


# Settings ---------------------------------------------------------------------------------------------------------
SETTINGS = {"memory": "adapt_memory", "memory_in_chat": "adapt_memory_context", "notice_patterns": "adapt_jobs",
            "suggest": "adapt_suggest"}


class SettingsIn(BaseModel):
    memory: bool | None = None
    memory_in_chat: bool | None = None
    notice_patterns: bool | None = None
    suggest: bool | None = None


@router.get("/settings")
def get_settings():
    return {k: store.flag(v, True) for k, v in SETTINGS.items()}


@router.put("/settings")
def put_settings(data: SettingsIn):
    for k, v in data.model_dump(exclude_none=True).items():
        db.set_setting(SETTINGS[k], "1" if v else "0")
    return get_settings()


# What I know about you ------------------------------------------------------------------------------------------------
class FactIn(BaseModel):
    content: str = Field(min_length=4, max_length=500)
    category: str = "fact"
    pinned: bool = False


class FactEdit(BaseModel):
    content: str | None = Field(default=None, max_length=500)
    category: str | None = None
    pinned: bool | None = None


@router.get("/facts")
def facts(q: str = ""):
    items = knowledge.search(q, 50) if q.strip() else knowledge.all_facts()
    legacy = db.all("SELECT id, content, kind, source, updated_at FROM memories ORDER BY updated_at DESC LIMIT 200")
    return {"facts": items, "categories": knowledge.CATEGORIES, "legacy": legacy, "work": jobs.profile()}


@router.post("/facts")
def add_fact(data: FactIn):
    f = knowledge.add(data.content, data.category, "user", confidence=1.0, pinned=data.pinned, dedupe=False)
    return f


@router.put("/facts/{fid}")
def edit_fact(fid: int, data: FactEdit):
    return knowledge.update(fid, data.content, data.category, data.pinned)


@router.delete("/facts/{fid}")
def delete_fact(fid: int):
    return knowledge.delete(fid)


@router.post("/facts/forget-all")
def forget_all():
    return knowledge.forget_everything()


@router.delete("/legacy-memories/{mid}")
def delete_legacy(mid: int):
    db.run("DELETE FROM memories WHERE id = ?", mid)
    return {"deleted": mid}


class ObserveIn(BaseModel):
    source: str = Field(pattern="^(file|task|site|note|chat|other)$")
    text: str = Field(min_length=1, max_length=40000)
    name: str = ""
    learn_facts: bool = True


@router.post("/observe")
async def observe(data: ObserveIn):
    """Another feature (a file just read, a site checked, a task done) tells the assistant about the person's work."""
    jobs.observe(None, data.source, (data.name + ": " if data.name else "") + data.text[:300], data.name)
    added = await knowledge.learn_from(data.text, data.source, data.name) if data.learn_facts else []
    jobs.refresh_suggestions()
    return {"learned": added}


# Show me how ---------------------------------------------------------------------------------------------------------
def _rec(r: dict) -> dict:
    return {"id": r["id"], "surface": r["surface"], "status": r["status"], "start_url": r["start_url"], "error": r["error"],
            "events": store.open_json(r["events_enc"], []), "narration": store.open_text(r["narration_enc"]),
            "playbook_id": r["playbook_id"], "created_at": r["created_at"]}


def _get_rec(rid: int) -> dict:
    r = db.one("SELECT * FROM adapt_recordings WHERE id = ?", rid)
    if not r:
        raise HTTPException(404, "Not found")
    return r


class RecordIn(BaseModel):
    surface: str = Field(default="browser", pattern="^(browser|desktop|narration)$")
    start_url: str = ""


@router.get("/recorders")
def recorders():
    return recorder.availability()


@router.post("/recordings")
async def start_recording(data: RecordIn):
    url = data.start_url.strip()
    if url and "://" not in url:
        url = "https://" + url
    remote, status, error = "", "recording", ""
    try:
        remote = await recorder.get(data.surface).start(url)
    except recorder.NotAvailable as e:
        status, error = "failed", str(e)
    rid = db.run("INSERT INTO adapt_recordings(surface, status, remote_id, start_url, error, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 data.surface, status, remote, url, error, db.now(), db.now())
    return _rec(_get_rec(rid))


def _merge(rid: int, new_events: list[dict], surface: str) -> list[dict]:
    r = _get_rec(rid)
    have = store.open_json(r["events_enc"], []) or []
    norm = [e for e in (recorder.normalise(x, surface) for x in new_events) if e]
    events = have + norm
    db.run("UPDATE adapt_recordings SET events_enc = ?, updated_at = ? WHERE id = ?", store.seal(events), db.now(), rid)
    return events


@router.get("/recordings/{rid}")
async def recording(rid: int):
    r = _get_rec(rid)
    if r["status"] == "recording" and r["remote_id"]:
        try:
            remote = await recorder.get(r["surface"]).events(r["remote_id"])
            events = [e for e in (recorder.normalise(x, r["surface"]) for x in remote) if e]
            db.run("UPDATE adapt_recordings SET events_enc = ?, updated_at = ? WHERE id = ?", store.seal(events), db.now(), rid)
        except recorder.NotAvailable as e:
            db.run("UPDATE adapt_recordings SET error = ? WHERE id = ?", str(e), rid)
    return _rec(_get_rec(rid))


class EventsIn(BaseModel):
    events: list[dict]


@router.post("/recordings/{rid}/events")
def push_events(rid: int, data: EventsIn):
    """Any recorder (browser extension, phone, a future Rooks Desk stream) can push events here."""
    r = _get_rec(rid)
    if r["status"] != "recording":
        raise HTTPException(409, "This recording has finished.")
    return {"events": len(_merge(rid, data.events[:500], r["surface"]))}


class StopIn(BaseModel):
    narration: str = Field(default="", max_length=8000)
    refine: bool = True


@router.post("/recordings/{rid}/stop")
async def stop_recording(rid: int, data: StopIn):
    """Stop, then turn what was shown (and said) into a draft playbook for the person to review."""
    r = _get_rec(rid)
    events = store.open_json(r["events_enc"], []) or []
    if r["status"] == "recording" and r["remote_id"]:
        try:
            remote = await recorder.get(r["surface"]).stop(r["remote_id"])
            got = [e for e in (recorder.normalise(x, r["surface"]) for x in remote) if e]
            if len(got) >= len(events):
                events = got
        except recorder.NotAvailable as e:
            db.run("UPDATE adapt_recordings SET error = ? WHERE id = ?", str(e), rid)
    if r["start_url"] and not any(e["kind"] == "navigate" for e in events[:1]):
        events = [{"kind": "navigate", "url": r["start_url"], "target": {}, "value": "", "secret": False, "surface": r["surface"], "t": 0}] + events
    if not [e for e in events if e["kind"] != "navigate"] and not data.narration.strip():
        raise HTTPException(400, "I didn't see any steps. Do the task in the window that opened, or describe the steps in your own words.")
    pb = playbooks.compile_steps(events, data.narration)
    if data.refine:
        pb = await playbooks.refine(pb, data.narration)
    saved = playbooks.save(pb, recording_id=rid)
    db.run("UPDATE adapt_recordings SET status = 'stopped', events_enc = ?, narration_enc = ?, playbook_id = ?, updated_at = ? WHERE id = ?",
           store.seal(events), store.seal(data.narration), saved["id"], db.now(), rid)
    jobs.observe(saved.get("job_kind"), "playbook", f"Showed me {saved['name']}", f"playbook:{saved['id']}")
    if data.narration.strip() and knowledge.learning_on():
        try:
            await knowledge.learn_from(data.narration, "teach", saved["name"])
        except Exception:  # noqa: BLE001
            pass
    return saved


# Playbooks ------------------------------------------------------------------------------------------------------------
@router.get("/playbooks")
def list_playbooks():
    return playbooks.all_playbooks()


@router.get("/playbooks/{pid}")
def get_playbook(pid: int):
    return playbooks.get(pid) | {"runs_list": playbooks.runs_for(pid), "last_values": playbooks.last_values(pid)}


class PlaybookEdit(BaseModel):
    name: str | None = None
    summary: str | None = None
    steps: list[dict] | None = None
    params: list[dict] | None = None
    notes: list[str] | None = None
    ready: bool | None = None


@router.put("/playbooks/{pid}")
def edit_playbook(pid: int, data: PlaybookEdit):
    cur = playbooks.get(pid)
    before_steps = [s["text"] for s in cur.get("steps", [])]
    for k, v in data.model_dump(exclude_none=True).items():
        if k != "ready":
            cur[k] = v
    if data.steps is not None:
        cur["steps"] = [{**s, "text": str(s.get("text", ""))[:300]} for s in data.steps if isinstance(s, dict)]
    saved = playbooks.save(cur, pid=pid, status="ready" if data.ready else None)
    changed = data.steps is not None and [s["text"] for s in saved["steps"]] != before_steps
    return saved | {"note": "Learned from your change: I'll follow your edited steps from now on." if changed else ""}


@router.delete("/playbooks/{pid}")
def delete_playbook(pid: int):
    return playbooks.delete(pid)


class RunIn(BaseModel):
    values: dict = {}


@router.post("/playbooks/{pid}/run")
async def run_playbook(pid: int, data: RunIn):
    return await playbooks.run(pid, data.values)


@router.get("/runs/{rid}")
async def get_run(rid: int):
    return await playbooks.sync(rid)


# Suggestions ------------------------------------------------------------------------------------------------------------
@router.get("/suggestions")
def get_suggestions():
    jobs.refresh_suggestions()
    return jobs.suggestions()


class AcceptIn(BaseModel):
    cron: str | None = None


@router.post("/suggestions/{sid}/accept")
def accept_suggestion(sid: int, data: AcceptIn):
    return jobs.accept(sid, data.cron)


@router.post("/suggestions/{sid}/dismiss")
def dismiss_suggestion(sid: int):
    return jobs.dismiss(sid)


# Feedback -------------------------------------------------------------------------------------------------------------
class FeedbackIn(BaseModel):
    target_kind: str
    target_id: str = ""
    rating: int = 0
    original: str = Field(default="", max_length=20000)
    edited: str = Field(default="", max_length=20000)
    comment: str = Field(default="", max_length=1000)


@router.post("/feedback")
async def give_feedback(data: FeedbackIn):
    return await feedback.record(data.target_kind, data.target_id, data.rating, data.original, data.edited, data.comment)


@router.get("/lessons")
def lessons():
    return feedback.recent()
