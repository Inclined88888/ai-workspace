"""Team lead over AI sub-agents (B1): split a goal into sub-tasks, run each on its own read-only sub-agent in parallel on
the Jobs board, then write one synthesis back into the conversation that asked.

Pattern: orchestrator and workers (Anthropic's multi-agent research system; LangGraph's supervisor). Built on what is
already here instead of a second engine: every sub-agent is a jobs.py job (its own conversation, trace, receipts, Stop
and Pause on the board), so the run is visible from the web app, the app window, the phone and the terminal alike.

Safety: sub-agents run on the "subagent" channel, where assistant.available_tools offers only effect="read" tools. They
can look things up; they cannot send, write, pay or change anything. The synthesis is one plain model call with no tools.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException

from .. import db

log = logging.getLogger("team.lead")
CHANNEL = "subagent"
MAX_SUBTASKS = 6
POLL_S = 1.0
SCHEMA = """
CREATE TABLE IF NOT EXISTS team_lead_runs (
  id INTEGER PRIMARY KEY,
  goal TEXT NOT NULL,
  conversation_id INTEGER,                 -- where the user asked; the synthesis is posted there
  status TEXT NOT NULL DEFAULT 'running',  -- running | done | failed
  synthesis TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS team_lead_tasks (
  run_id INTEGER NOT NULL,
  position INTEGER NOT NULL,
  task TEXT NOT NULL,
  job_id INTEGER NOT NULL,
  PRIMARY KEY (run_id, position)
);
"""
WORKER = ("You are sub-agent {n} of {total} working for a team lead. Overall goal: {goal}\n\nYour sub-task: {task}\n\n"
          "You can only look things up (read-only tools). Reply with a dense result: facts, figures, sources, and what is "
          "still unknown. No preamble.")
SYNTH = ("You are the team lead. Goal: {goal}\n\nYour sub-agents reported:\n\n{reports}\n\nWrite one answer for the user "
         "that combines these results. Say which sub-task each point came from (S1, S2, ...). If a sub-agent failed or "
         "found nothing, say so plainly. Do not invent anything that is not in the reports.")
_watchers: set[asyncio.Task] = set()
_ready: set[str] = set()


def ensure() -> None:
    from .. import config

    if str(config.DB_PATH) not in _ready:  # per database file, like jobs.ensure (tests swap the DB)
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


def dispatch(goal: str, subtasks: list[str], conversation_id: int | None = None) -> dict:
    from .. import jobs

    ensure()
    goal = (goal or "").strip()
    tasks = [t.strip() for t in (subtasks or []) if isinstance(t, str) and t.strip()]
    if not goal:
        raise HTTPException(400, "Say what the overall goal is.")
    if not 2 <= len(tasks) <= MAX_SUBTASKS:
        raise HTTPException(400, f"Give between 2 and {MAX_SUBTASKS} sub-tasks.")
    rid = db.run("INSERT INTO team_lead_runs(goal, conversation_id, created_at) VALUES (?, ?, ?)", goal[:2000], conversation_id, db.now())
    for i, task in enumerate(tasks, 1):
        prompt = WORKER.format(n=i, total=len(tasks), goal=goal, task=task)
        job = jobs.create("chat", prompt, title=f"S{i}: {task}"[:120], channel=CHANNEL, payload={"team_lead_run": rid, "position": i})
        db.run("INSERT INTO team_lead_tasks(run_id, position, task, job_id) VALUES (?, ?, ?, ?)", rid, i, task[:2000], job["id"])
    try:
        t = asyncio.get_running_loop().create_task(watch(rid))
        _watchers.add(t)
        t.add_done_callback(_watchers.discard)
    except RuntimeError:  # no running loop (a sync caller): tick() or the next get() finishes it
        pass
    return tree(rid)


def tree(rid: int) -> dict:
    from .. import jobs

    ensure()
    run = db.one("SELECT * FROM team_lead_runs WHERE id = ?", rid)
    if not run:
        raise HTTPException(404, "No such team run")
    subs = []
    for t in db.all("SELECT * FROM team_lead_tasks WHERE run_id = ? ORDER BY position", rid):
        j = jobs.get(t["job_id"])
        subs.append({"id": f"S{t['position']}", "task": t["task"], "job_id": j["id"], "status": j["status"],
                     "conversation_id": j["conversation_id"], "result": j["result"]})
    return {"id": run["id"], "goal": run["goal"], "status": run["status"], "conversation_id": run["conversation_id"],
            "subtasks": subs, "synthesis": run["synthesis"], "created_at": run["created_at"], "finished_at": run["finished_at"]}


def runs(limit: int = 20) -> list[dict]:
    ensure()
    return [tree(r["id"]) for r in db.all("SELECT id FROM team_lead_runs ORDER BY id DESC LIMIT ?", limit)]


FINISHED = ("done", "failed", "stopped")


async def finish_if_ready(rid: int) -> bool:
    """Synthesize once every sub-task has finished (done, failed or stopped). Returns whether the run is over."""
    from .. import assistant, llm

    t = tree(rid)
    if t["status"] != "running":
        return True
    if not all(s["status"] in FINISHED for s in t["subtasks"]):
        return False
    # one synthesis per run, however many watchers or callers race here
    if db.conn().execute("UPDATE team_lead_runs SET status = 'synthesizing' WHERE id = ? AND status = 'running'", (rid,)).rowcount != 1:
        return True
    reports = "\n\n".join(f"[{s['id']}] {s['task']} ({s['status']})\n{s['result'] or '(no result)'}" for s in t["subtasks"])
    try:
        res = await llm.chat(None, [{"role": "user", "content": SYNTH.format(goal=t["goal"], reports=reports)}], [],
                             trace={"channel": CHANNEL, "conversation_id": t["conversation_id"]})
        text = (res["message"].get("content") or "").strip() or "(The team lead returned an empty synthesis.)"
        status = "done"
    except Exception as e:  # noqa: BLE001 - the sub-results stay on the board; say what failed
        text, status = f"The sub-agents finished but the synthesis failed: {str(e)[:300]}", "failed"
    db.run("UPDATE team_lead_runs SET status = ?, synthesis = ?, finished_at = ? WHERE id = ?", status, text, db.now(), rid)
    if t["conversation_id"]:
        lines = "\n".join(f"- {s['id']} {s['task']}: {s['status']}" for s in t["subtasks"])
        assistant._save(t["conversation_id"], "assistant", f"**Team run #{rid}: {t['goal']}**\n\n{lines}\n\n{text}", name="team_lead")
    return True


async def watch(rid: int, timeout_s: float = 3600) -> None:
    end = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < end:
        try:
            if await finish_if_ready(rid):
                return
        except Exception as e:  # noqa: BLE001 - keep watching; one bad poll is not the end of the run
            log.warning("team run %s: %s", rid, e)
        await asyncio.sleep(POLL_S)
