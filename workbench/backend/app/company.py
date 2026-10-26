"""Company: you are the board. Agents sit in an org chart, work on tasks with the assistant's tools, delegate to
their reports, and stop when their monthly budget runs out.

A single worker runs one queued task at a time (keeps the box light and spend predictable). Delegation is limited
in depth so agents cannot spawn work forever.
"""

import asyncio
import logging

from fastapi import HTTPException

from . import assistant, db, scheduler, usage

log = logging.getLogger("company")
MAX_DEPTH = 3


def team_for(agent: dict | None) -> list[dict]:
    if not agent:
        return []
    return db.all("SELECT id, name, title, status FROM agents WHERE reports_to = ?", agent["id"])


def _depth(task_id: int | None) -> int:
    d = 0
    while task_id and d <= MAX_DEPTH + 1:
        t = db.one("SELECT parent_id FROM agent_tasks WHERE id = ?", task_id)
        task_id = t["parent_id"] if t else None
        d += 1
    return d


def create_task(title: str, description: str = "", agent_id: int | None = None, parent_id: int | None = None,
                created_by: str = "board", queue: bool = False) -> dict:
    if agent_id and not db.one("SELECT id FROM agents WHERE id = ?", agent_id):
        raise HTTPException(404, "Agent not found")
    now = db.now()
    tid = db.run("INSERT INTO agent_tasks(title, description, agent_id, parent_id, created_by, status, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", title.strip()[:300], description.strip()[:10000], agent_id, parent_id,
                 created_by, "queued" if queue and agent_id else "todo", now, now)
    return db.one("SELECT * FROM agent_tasks WHERE id = ?", tid)


def delegate(ctx, agent_name: str, title: str, description: str) -> dict:
    me = ctx.agent
    target = db.one("SELECT * FROM agents WHERE lower(name) = lower(?) AND reports_to = ?", agent_name.strip(), me["id"])
    if not target:
        names = ", ".join(a["name"] for a in team_for(me)) or "nobody"
        raise HTTPException(400, f"{agent_name} does not report to you. Your reports: {names}.")
    if _depth(ctx.task_id) >= MAX_DEPTH:
        raise HTTPException(400, "Delegation depth limit reached; do this part yourself.")
    t = create_task(title, description, target["id"], ctx.task_id, f"agent:{me['id']}", queue=True)
    return {"task_id": t["id"], "assigned_to": target["name"], "status": t["status"]}


async def run_task(task_id: int) -> None:
    t = db.one("SELECT * FROM agent_tasks WHERE id = ?", task_id)
    agent = db.one("SELECT * FROM agents WHERE id = ?", t["agent_id"]) if t and t["agent_id"] else None
    if not t or not agent:
        return
    if agent["status"] != "active":
        db.run("UPDATE agent_tasks SET status = 'blocked', result = ?, updated_at = ? WHERE id = ?",
               f"{agent['name']} is paused: {agent['paused_reason']}", db.now(), task_id)
        return
    conv = t["conversation_id"] or assistant.new_conversation("agent", f"{agent['name']}: {t['title']}", str(task_id), agent["id"])
    db.run("UPDATE agent_tasks SET status = 'running', conversation_id = ?, updated_at = ? WHERE id = ?", conv, db.now(), task_id)
    ctx = assistant.Ctx(channel="agent", conversation_id=conv, agent=agent, task_id=task_id)
    text, events = await assistant.ask(ctx, f"Task #{task_id}: {t['title']}\n\n{t['description'] or ''}".strip())
    budget_stop = any(e.get("budget") for e in events)
    status = "blocked" if budget_stop else ("failed" if text.startswith("Error:") else "done")
    db.run("UPDATE agent_tasks SET status = ?, result = ?, updated_at = ? WHERE id = ?", status, text, db.now(), task_id)
    if t["created_by"] == "board" or status != "done":
        scheduler.deliver(f"{agent['name']} {status}: {t['title']}", text or "(no output)", "inbox", f"task:{task_id}")


async def loop() -> None:
    while True:
        await asyncio.sleep(10)
        try:
            t = db.one("SELECT t.id FROM agent_tasks t JOIN agents a ON a.id = t.agent_id WHERE t.status = 'queued' "
                       "ORDER BY t.id LIMIT 1")
            if t:
                await run_task(t["id"])
        except Exception as e:  # noqa: BLE001 - keep the worker alive
            log.warning("company worker error: %s", e)


def recover() -> None:
    db.run("UPDATE agent_tasks SET status = 'queued', updated_at = ? WHERE status = 'running'", db.now())


def overview() -> dict:
    agents = db.all("SELECT * FROM agents ORDER BY id")
    for a in agents:
        a["month"] = usage.agent_month(a["id"])
    tasks = db.all("SELECT t.*, a.name AS agent_name FROM agent_tasks t LEFT JOIN agents a ON a.id = t.agent_id "
                   "ORDER BY t.id DESC LIMIT 300")
    return {"mission": db.get_setting("company_mission", "") or "", "agents": agents, "tasks": tasks}
