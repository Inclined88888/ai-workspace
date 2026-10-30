"""Every message to a team member goes through here (feat/team-lead).

Rules: the first message to a member on each channel waits for the lead's approval. After that, assignment messages go
out on their own only if the lead turned on "Auto-notify my team for assignments" (team_auto_notify), and nudges only if
"Auto-nudge" (team_auto_nudge) is on. The daily plan always waits for approval. Channels without a sender set up
(SMS, a member link) become "copy": the lead copies the text and sends it themselves.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from fastapi import HTTPException

from .. import db
from . import store

log = logging.getLogger("team.outbox")
AUTO_SETTING = {"assign": "auto_notify", "nudge": "auto_nudge"}


def _footer(m: dict, tid: int | None) -> str:
    ref = f" [T{tid}]" if tid else ""
    link = f"\nYour tasks: {m['link_url']}" if m.get("link_url") else ""
    return f"\n\nReply with 'done', '50%' or 'blocked: reason'{ref}.{link}"


def mail_account() -> dict | None:
    """The mailbox team messages go from: the one picked in team settings, else the first connected one."""
    pick = store.setting("mail_account")
    return (db.one("SELECT * FROM mail_accounts WHERE id = ?", int(pick)) if pick.isdigit() else None) or \
        db.one("SELECT * FROM mail_accounts ORDER BY id LIMIT 1")


def channel_ok(mid: int, channel: str) -> bool:
    return bool(db.one("SELECT 1 FROM team_channel_ok WHERE member_id = ? AND channel = ?", int(mid), channel))


def queue(mid: int, kind: str, subject: str, body: str, task_id: int | None = None) -> dict:
    store.ensure()
    m = store.member(mid)
    ch = m["channel"]
    auto = AUTO_SETTING.get(kind)
    if not channel_ok(mid, ch):
        why = f"First message to {m['first_name']} by {ch}: needs your OK once."
    elif auto and store.setting(auto) == "1":
        why = ""
    else:
        why = "Waiting for your OK." if not auto else f"Turn on auto-{'notify' if kind == 'assign' else 'nudge'} to skip this."
    oid = db.run("INSERT INTO team_outbox(member_id, task_id, kind, channel, subject, body, status, why, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?)", mid, task_id, kind, ch, subject[:200],
                 body + _footer(m, task_id), why or "Sent automatically (your setting).", store.now())
    if not why:
        return send(oid)
    return item(oid)


def item(oid: int) -> dict:
    r = db.one("SELECT o.*, m.name AS member_name FROM team_outbox o LEFT JOIN team_members m ON m.id = o.member_id WHERE o.id = ?", int(oid))
    if not r:
        raise HTTPException(404, "Message not found")
    return r


def listing(status: str = "waiting") -> list[dict]:
    store.ensure()
    q = "SELECT o.*, m.name AS member_name FROM team_outbox o LEFT JOIN team_members m ON m.id = o.member_id"
    return db.all(q + (" WHERE o.status = ?" if status else " WHERE 1 = ?") + " ORDER BY o.id DESC LIMIT 100", status or 1)


def approve(oid: int, body: str | None = None) -> dict:
    o = item(oid)
    if o["status"] != "waiting":
        return o
    if body is not None:
        db.run("UPDATE team_outbox SET body = ? WHERE id = ?", body[:5000], int(oid))
    db.run("INSERT OR IGNORE INTO team_channel_ok(member_id, channel, approved_at) VALUES (?, ?, ?)", o["member_id"], o["channel"], store.now())
    return send(oid)


def cancel(oid: int) -> dict:
    db.run("UPDATE team_outbox SET status = 'cancelled' WHERE id = ? AND status = 'waiting'", int(oid))
    return item(oid)


def _mark(oid: int, status: str, error: str = "") -> dict:
    db.run("UPDATE team_outbox SET status = ?, error = ?, sent_at = ? WHERE id = ?", status, error[:300],
           store.now() if status == "sent" else None, int(oid))
    o = item(oid)
    if status == "sent" and o["task_id"] and o["kind"] == "assign":
        db.run("UPDATE team_tasks SET notified_at = ? WHERE id = ?", store.now(), o["task_id"])
    return o


def send(oid: int) -> dict:
    o = item(oid)
    m = store.member(o["member_id"])
    c = m["contact"]
    try:
        if o["channel"] == "email" and c.get("email"):
            from .. import mail

            acct = mail_account()
            if not acct:
                return _mark(oid, "copy", "No mailbox connected: copy the text and send it yourself.")
            mail.send(acct, c["email"], o["subject"] or "New task", o["body"])
        elif o["channel"] == "telegram" and c.get("telegram"):
            from .. import telegram

            tok = telegram.token()
            if not tok:
                return _mark(oid, "copy", "Telegram isn't set up: copy the text instead.")
            httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": c["telegram"], "text": o["body"]},
                       timeout=15).raise_for_status()
        elif o["channel"] == "slack" and str(c.get("slack", "")).startswith("https://hooks.slack.com/"):
            httpx.post(c["slack"], json={"text": o["body"]}, timeout=15).raise_for_status()
        elif o["channel"] == "app" and m["has_app"]:
            from . import connect

            connect.push_tasks()
        else:  # sms without a provider, member link, or missing contact details
            return _mark(oid, "copy", "Copy this and send it yourself (no sender set up for this channel).")
    except Exception as e:  # noqa: BLE001 - one failed message never breaks the board
        log.warning("team message %s failed: %s", oid, e.__class__.__name__)
        return _mark(oid, "failed", f"Couldn't send: {e.__class__.__name__}")
    return _mark(oid, "sent")


def assignment(tid: int) -> dict | None:
    t = store.task(tid)
    if not t["assignee_id"]:
        return None
    due = f" by {t['due']}" if t["due"] else ""
    body = f"Hi {store.member(t['assignee_id'])['first_name']}, new task for you: {t['title']}{due}."
    if t["notes"]:
        body += f"\n{t['notes']}"
    if t["checklist"]:
        body += "\n" + "\n".join(f"- {c['text']}" for c in t["checklist"])
    return queue(t["assignee_id"], "assign", f"New task: {t['title']}", body, tid)


def nudge(tid: int, why: str, dedupe_day: str = "") -> dict | None:
    t = store.task(tid)
    if not t["assignee_id"]:
        return None
    since = (store.utcnow() - timedelta(hours=20)).isoformat(timespec="seconds")
    if db.one("SELECT 1 FROM team_outbox WHERE task_id = ? AND kind = 'nudge' AND created_at >= ? AND status != 'cancelled'",
              tid, since):  # at most one nudge a day per task (dedupe_day kept for callers)
        return None
    m = store.member(t["assignee_id"])
    return queue(m["id"], "nudge", f"Checking in: {t['title']}",
                 f"Hi {m['first_name']}, how's “{t['title']}” going? ({why})", tid)
