"""Background jobs board (feat/v02-automations).

Several jobs run at once: chat tasks, browser jobs, computer use, file analysis, saved playbooks and automations. Each job
is a normal assistant conversation, so its trace, approvals and receipts work like any chat. The board shows:

  queued   waiting for a free slot (computer use runs one at a time: the screen has one mouse)
  running  working now
  waiting  needs you: approve a held action, or approve the start of an automation that asks first
  paused   stopped by you, picks up where it left off on Resume
  done / failed / stopped

Approvals reach the paired phone (Expo push, when the owner turned it on) and the desktop (the app polls /api/jobs/notices
and shows a system notification). Approving on the phone uses the existing /api/mobile/approvals route; once nothing in a
job is waiting, the job continues by itself.

Safety: jobs started by automations run on the "automation" channel, where anything that sends, posts, pays, deletes or
changes something outside the app is held for the user's approval (assistant.hold_reason), every time.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import HTTPException

from . import db, features_v02

log = logging.getLogger("jobs")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL DEFAULT 'chat',          -- chat | browser | computer | files | playbook | automation
  title TEXT NOT NULL DEFAULT '',
  prompt TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'queued',      -- queued | running | waiting | paused | done | failed | stopped
  needs TEXT NOT NULL DEFAULT '',             -- when waiting: start (automation asks first) | approval (held actions)
  detail TEXT NOT NULL DEFAULT '',            -- one line for the board: what it is doing or waiting for
  result TEXT NOT NULL DEFAULT '',
  conversation_id INTEGER,
  automation_id INTEGER,
  payload TEXT NOT NULL DEFAULT '{}',         -- kind specific: playbook id and values, the trigger event
  channel TEXT NOT NULL DEFAULT 'job',
  turns INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS job_notices (
  id INTEGER PRIMARY KEY,
  job_id INTEGER NOT NULL,
  kind TEXT NOT NULL,                         -- needs_you | finished | failed
  title TEXT NOT NULL,
  body TEXT NOT NULL DEFAULT '',
  at TEXT NOT NULL
);
"""

KINDS = ("chat", "browser", "computer", "files", "playbook", "automation")
EXCLUSIVE = {"computer"}                      # one at a time; others queue behind it
ACTIVE = ("queued", "running", "waiting", "paused")
MAX_PARALLEL_PRO = 3
MAX_TURNS = 6                                 # continuation turns after approvals or resume
CONTINUE = "[Automatic note, not from the user: the approvals above were answered. Continue the job and finish it. Reply with the result.]"
RESUME = "[Automatic note, not from the user: the user paused this job and has now resumed it. Continue where you left off and finish it.]"

PREFIX = {
    "chat": "",
    "browser": "Do this in the web browser (browse_web): ",
    "computer": "Do this on the user's computer (use_computer): ",
    "files": "Do this with the user's files (list_files, read_file, analyse_spreadsheet, run_python as needed): ",
    "automation": "",
}

_ready: set[str] = set()
_tasks: dict[int, asyncio.Task] = {}
_pausing: set[int] = set()
_wake: asyncio.Event | None = None


def ensure() -> None:
    from . import config

    if str(config.DB_PATH) not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


def max_parallel() -> int:
    """Pro runs several jobs side by side; Free runs one at a time (they still queue and show on the board)."""
    return MAX_PARALLEL_PRO if features_v02.allowed("jobs_board") else 1


def _out(r: dict | None) -> dict:
    if not r:
        raise HTTPException(404, "Job not found")
    r = dict(r)
    r["payload"] = json.loads(r.get("payload") or "{}")
    r["column"] = column(r)
    r["approvals"] = _pending(r["conversation_id"]) if r["status"] == "waiting" and r["needs"] == "approval" else []
    return r


def column(r: dict) -> str:
    return {"queued": "running", "running": "running", "waiting": "you", "paused": "running"}.get(r["status"], "done")


def get(jid: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM jobs WHERE id = ?", jid))


def board(limit_done: int = 30) -> dict:
    ensure()
    active = [_out(r) for r in db.all(f"SELECT * FROM jobs WHERE status IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC", *ACTIVE)]
    done = [_out(r) for r in db.all(f"SELECT * FROM jobs WHERE status NOT IN ({','.join('?' * len(ACTIVE))}) ORDER BY id DESC LIMIT ?",
                                    *ACTIVE, limit_done)]
    return {
        "running": [j for j in active if j["column"] == "running"],
        "you": [j for j in active if j["column"] == "you"],
        "done": done,
        "max_parallel": max_parallel(),
        "pro": features_v02.allowed("jobs_board"),
        "tier": features_v02.tier("jobs_board"),
    }


def _touch(jid: int, **cols) -> None:
    cols["updated_at"] = db.now()
    db.run(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", *cols.values(), jid)


def create(kind: str, prompt: str, title: str = "", payload: dict | None = None, automation_id: int | None = None,
           ask_first: bool = False, channel: str = "") -> dict:
    ensure()
    kind = kind if kind in KINDS else "chat"
    prompt = (prompt or "").strip()
    if not prompt and kind != "playbook":
        raise HTTPException(400, "Say what the job should do.")
    title = (title or (prompt.splitlines()[0] if prompt else "") or "Job").strip()[:120] or "Job"
    status, needs, detail = ("waiting", "start", "Asks you before it starts") if ask_first else ("queued", "", "Waiting for a free slot")
    channel = channel or ("automation" if automation_id or kind == "automation" else "job")
    now = db.now()
    jid = db.run("INSERT INTO jobs(kind, title, prompt, status, needs, detail, automation_id, payload, channel, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kind, title, prompt[:20000], status, needs, detail, automation_id,
                 json.dumps(payload or {}, default=str), channel, now, now)
    if ask_first:
        notice(jid, "needs_you", f"OK to run “{title}”?", (payload or {}).get("event_summary", "") or "An automation wants to start.")
    wake()
    return get(jid)


# --- notices: desktop notifications and phone pushes ---------------------------------------------------------------------
def notice(jid: int, kind: str, title: str, body: str = "") -> int:
    ensure()
    nid = db.run("INSERT INTO job_notices(job_id, kind, title, body, at) VALUES (?, ?, ?, ?, ?)", jid, kind, title[:120], body[:300], db.now())
    from . import push

    push.notify(title, body or "Open the Jobs board", kind="job", id=jid)  # no-op unless phone push is on
    return nid


def notices(after: int = 0) -> list[dict]:
    ensure()
    return db.all("SELECT * FROM job_notices WHERE id > ? ORDER BY id LIMIT 50", after)


# --- controls ------------------------------------------------------------------------------------------------------------
def approve_start(jid: int, approve: bool) -> dict:
    j = get(jid)
    if j["status"] != "waiting" or j["needs"] != "start":
        raise HTTPException(409, "This job is not waiting to start.")
    if approve:
        _touch(jid, status="queued", needs="", detail="Approved, waiting for a free slot")
    else:
        _touch(jid, status="stopped", needs="", detail="You said no, so it did not run", finished_at=db.now())
    wake()
    return get(jid)


def pause(jid: int) -> dict:
    j = get(jid)
    if j["status"] == "queued":
        _touch(jid, status="paused", detail="Paused before it started")
    elif j["status"] == "running":
        _pausing.add(jid)
        t = _tasks.get(jid)
        if t:
            t.cancel()
        _touch(jid, status="paused", detail="Paused. Resume picks up where it left off")
    elif j["status"] != "paused":
        raise HTTPException(409, f"A {j['status']} job cannot be paused.")
    return get(jid)


def resume(jid: int) -> dict:
    j = get(jid)
    if j["status"] != "paused":
        raise HTTPException(409, "Only a paused job can be resumed.")
    _touch(jid, status="queued", detail="Resumed, waiting for a free slot")
    wake()
    return get(jid)


def stop(jid: int) -> dict:
    j = get(jid)
    if j["status"] not in ACTIVE:
        raise HTTPException(409, f"This job already {j['status'] if j['status'] != 'done' else 'finished'}.")
    t = _tasks.get(jid)
    if t:
        t.cancel()
    _deny_pending(j["conversation_id"])  # a stopped job never acts later through an old approval card
    _touch(jid, status="stopped", needs="", detail="Stopped by you", finished_at=db.now())
    _close_turns(j["conversation_id"], "Stopped from the Jobs board")
    return get(jid)


def retry(jid: int) -> dict:
    j = get(jid)
    if j["status"] not in ("failed", "stopped", "done"):
        raise HTTPException(409, "Only a finished job can run again.")
    return create(j["kind"], j["prompt"], j["title"], j["payload"], j["automation_id"], channel=j["channel"])


def clear_done() -> int:
    ensure()
    return db.run(f"DELETE FROM jobs WHERE status IN ('done', 'failed', 'stopped')")


# --- running ---------------------------------------------------------------------------------------------------------------
def _pending(conversation_id: int | None) -> list[dict]:
    if not conversation_id:
        return []
    from . import actions

    return actions.pending(conversation_id)


def _deny_pending(conversation_id: int | None) -> None:
    from . import actions

    for a in _pending(conversation_id):
        try:
            actions.decide(a["id"], False, "jobs")
        except HTTPException:
            pass


def _close_turns(conversation_id: int | None, why: str) -> None:
    if conversation_id:
        db.run("UPDATE actions SET outcome = 'interrupted', failure_reason = ? WHERE kind = 'turn' AND outcome = 'running' "
               "AND conversation_id = ?", why, conversation_id)


def _first_prompt(j: dict) -> str:
    return PREFIX.get(j["kind"], "") + j["prompt"]


async def _run_playbook(j: dict) -> str:
    from .adapt import playbooks

    p = j["payload"]
    pb = playbooks.get(int(p["playbook_id"]))
    run = await playbooks.run(pb["id"], p.get("values") or {}, trigger="automation" if j["automation_id"] else "jobs")
    return f"Started your “{pb.get('name') or 'saved'}” steps ({run.get('status', 'running')}). Follow them on the Show me how page."


async def run_job(jid: int) -> dict:
    """Run one job until it finishes, needs the user, or is paused/stopped."""
    from . import assistant

    j = get(jid)
    first = not j["conversation_id"]
    _touch(jid, status="running", needs="", detail="Working on it", started_at=j["started_at"] or db.now(), turns=j["turns"] + 1)
    try:
        if j["kind"] == "playbook":
            text = await _run_playbook(j)
        else:
            conv = j["conversation_id"] or assistant.new_conversation(j["channel"], f"Job: {j['title']}"[:100], f"job:{jid}")
            if first:
                _touch(jid, conversation_id=conv)
            ctx = assistant.Ctx(channel=j["channel"], conversation_id=conv)
            prompt = _first_prompt(j) if first else (RESUME if j["detail"].startswith("Resumed") else CONTINUE)
            text, _ = await assistant.ask(ctx, prompt)
    except asyncio.CancelledError:
        j = get(jid)
        _close_turns(j["conversation_id"], "Paused from the Jobs board" if jid in _pausing else "Stopped from the Jobs board")
        _pausing.discard(jid)
        return get(jid)
    except Exception as e:  # noqa: BLE001 - one job failing never stops the board
        log.warning("job %s failed: %s", jid, e)
        _touch(jid, status="failed", detail=f"Something went wrong: {str(e)[:200]}", finished_at=db.now())
        notice(jid, "failed", f"“{get(jid)['title']}” didn't finish", str(e)[:200])
        return get(jid)
    j = get(jid)
    if j["status"] != "running":  # paused or stopped while the last step returned
        return j
    held = _pending(j["conversation_id"])
    if held:
        what = ", ".join(sorted({a["tool"].replace("_", " ") for a in held}))
        _touch(jid, status="waiting", needs="approval", detail=f"Needs your OK: {what}", result=text[:20000])
        notice(jid, "needs_you", f"“{j['title']}” needs your OK", what)
    elif text.startswith("Error:"):
        _touch(jid, status="failed", detail=text[:200], result=text[:20000], finished_at=db.now())
        notice(jid, "failed", f"“{j['title']}” didn't finish", text[7:200])
    else:
        _touch(jid, status="done", detail="Finished", result=text[:20000], finished_at=db.now())
        notice(jid, "finished", f"“{j['title']}” is done", text[:160])
    if j["automation_id"]:
        from . import automations

        automations.job_finished(j["automation_id"], get(jid))
    return get(jid)


def exclusive(j: dict) -> bool:
    """Jobs that drive the mouse and keyboard: computer use, and playbooks taught on the desktop."""
    p = j["payload"] if isinstance(j.get("payload"), dict) else json.loads(j.get("payload") or "{}")
    return j["kind"] in EXCLUSIVE or (j["kind"] == "playbook" and p.get("surface") == "desktop")


def _slots_free(running: list[dict], cand: dict) -> bool:
    if len(running) >= max_parallel():
        return False
    return not (exclusive(cand) and any(exclusive(r) for r in running))


def tick() -> list[int]:
    """Continue jobs whose approvals were all answered, then start queued jobs that fit. Returns the started ids."""
    ensure()
    for w in db.all("SELECT * FROM jobs WHERE status = 'waiting' AND needs = 'approval'"):
        if not _pending(w["conversation_id"]):
            if w["turns"] >= MAX_TURNS:
                _touch(w["id"], status="done", needs="", detail="Finished after your answers", finished_at=db.now())
            else:
                _touch(w["id"], status="queued", needs="", detail="Continuing after your answer")
    running = db.all("SELECT * FROM jobs WHERE status = 'running'")
    started = []
    for q in db.all("SELECT * FROM jobs WHERE status = 'queued' ORDER BY id"):
        if not _slots_free(running, q):
            if exclusive(q) and any(exclusive(r) for r in running):
                _touch(q["id"], detail="Waiting: computer use runs one job at a time")
            continue
        _touch(q["id"], status="running", detail="Starting")
        running.append(q)
        started.append(q["id"])
        t = asyncio.get_running_loop().create_task(run_job(q["id"]))
        _tasks[q["id"]] = t
        t.add_done_callback(lambda _t, jid=q["id"]: (_tasks.pop(jid, None), wake()))
    return started


def wake() -> None:
    if _wake is not None:
        try:
            _wake.set()
        except RuntimeError:
            pass


def recover() -> None:
    """After a restart, jobs that were running are paused (nothing restarts silently)."""
    ensure()
    db.run("UPDATE jobs SET status = 'paused', detail = 'Paused because the app restarted. Press Resume' WHERE status = 'running'")


async def loop() -> None:
    global _wake
    _wake = asyncio.Event()
    recover()
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001 - keep the board alive
            log.warning("jobs tick error: %s", e)
        try:
            await asyncio.wait_for(_wake.wait(), 3)
        except asyncio.TimeoutError:
            pass
        _wake.clear()
