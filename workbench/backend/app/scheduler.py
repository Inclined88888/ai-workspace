"""Scheduled tasks: cron expressions in the workspace timezone, run by the assistant, delivered to the inbox and
optionally Telegram. One job runs at a time to keep the box light; a late job runs once, not once per missed slot."""

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from croniter import croniter
from fastapi import HTTPException

from . import assistant, db

log = logging.getLogger("scheduler")


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(assistant.timezone_name())
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def next_run(cron: str, after: datetime | None = None) -> str:
    base = (after or datetime.now(timezone.utc)).astimezone(_tz())
    nxt = croniter(cron, base).get_next(datetime)
    return nxt.astimezone(timezone.utc).isoformat(timespec="seconds")


def validate(cron: str) -> str:
    cron = " ".join(cron.split())
    if len(cron.split()) != 5 or not croniter.is_valid(cron):
        raise HTTPException(400, "Use a 5 field cron expression, e.g. '0 8 * * *' (8:00 every day) or '0 17 * * 5' (Fridays 17:00).")
    return cron


def create_schedule(name: str, cron: str, prompt: str, deliver: str = "inbox", agent_id: int | None = None) -> dict:
    cron = validate(cron)
    deliver = deliver if deliver in ("inbox", "telegram") else "inbox"
    if not prompt.strip():
        raise HTTPException(400, "A schedule needs a prompt.")
    sid = db.run("INSERT INTO schedules(name, cron, prompt, deliver, agent_id, next_run, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 name.strip()[:100] or "Scheduled task", cron, prompt.strip()[:8000], deliver, agent_id, next_run(cron), db.now())
    return db.one("SELECT * FROM schedules WHERE id = ?", sid)


def deliver(title: str, body: str, target: str, source: str) -> None:
    db.run("INSERT INTO inbox(title, body, source, created_at) VALUES (?, ?, ?, ?)", title[:200], body, source, db.now())
    if target == "telegram":
        from . import telegram

        telegram.notify(f"{title}\n\n{body}")


async def run_schedule(s: dict) -> str:
    # Book the next slot first: a failing job must never retry in a tight (and paid) loop
    db.run("UPDATE schedules SET next_run = ? WHERE id = ?", next_run(s["cron"]), s["id"])
    agent = db.one("SELECT * FROM agents WHERE id = ?", s["agent_id"]) if s["agent_id"] else None
    conv = assistant.new_conversation("schedule", f"{s['name']} ({assistant.local_now():%d %b %H:%M})", str(s["id"]), s["agent_id"])
    ctx = assistant.Ctx(channel="schedule", conversation_id=conv, agent=agent)
    text, _ = await assistant.ask(ctx, f"[Scheduled task: {s['name']}]\n{s['prompt']}")
    status = "error" if text.startswith("Error:") else "ok"
    db.run("UPDATE schedules SET last_run = ?, last_status = ? WHERE id = ?", db.now(), status if status == "ok" else text[:300], s["id"])
    deliver(s["name"], text or "(no output)", s["deliver"], f"schedule:{s['id']}")
    return text


async def loop() -> None:
    while True:
        await asyncio.sleep(30)
        try:
            due = db.all("SELECT * FROM schedules WHERE enabled = 1 AND next_run <= ? ORDER BY next_run", db.now())
            for s in due:
                try:
                    await run_schedule(s)
                except Exception as e:  # noqa: BLE001
                    db.run("UPDATE schedules SET last_run = ?, last_status = ? WHERE id = ?", db.now(), f"error: {e}"[:300], s["id"])
        except Exception as e:  # noqa: BLE001 - keep the scheduler alive
            log.warning("scheduler error: %s", e)
