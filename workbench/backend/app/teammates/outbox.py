"""Outbox: every outgoing message is a draft card first (feat/grok-style).

An assistant (any teammate, any channel, any routine) can only *draft* a message with `draft_message`. The chat shows
an editable card with the recipients and body; the message leaves only when the person presses Send in the app (or
approves it on the phone). Nothing inbound - a Slack DM, a Telegram message, an email, a site change - can send it.
Channels: email (the connected mail account), slack (the Slack bot), telegram (the Telegram bot).
"""

from __future__ import annotations

import httpx
from fastapi import HTTPException

from .. import config, db

SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox (
  id INTEGER PRIMARY KEY,
  channel TEXT NOT NULL,             -- email | slack | telegram
  recipients TEXT NOT NULL,          -- email addresses, a Slack channel/user id, or a Telegram chat id
  subject TEXT NOT NULL DEFAULT '',
  body TEXT NOT NULL,
  teammate_id INTEGER,
  conversation_id INTEGER,
  status TEXT NOT NULL DEFAULT 'draft',  -- draft | sent | discarded | failed
  detail TEXT NOT NULL DEFAULT '',
  created_by TEXT NOT NULL DEFAULT 'assistant',
  created_at TEXT NOT NULL,
  sent_at TEXT
);
"""
CHANNELS = {"email": "Email", "slack": "Slack", "telegram": "Telegram"}
_ready: set[str] = set()


def ensure() -> None:
    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def _out(r: dict | None) -> dict:
    if not r:
        raise HTTPException(404, "Draft not found")
    return {**r, "channel_label": CHANNELS.get(r["channel"], r["channel"])}


def get(did: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM outbox WHERE id = ?", did))


def listing(status: str = "draft", conversation_id: int | None = None) -> list[dict]:
    ensure()
    return [_out(r) for r in db.all("SELECT * FROM outbox WHERE (? = '' OR status = ?) AND (? IS NULL OR conversation_id = ?) "
                                     "ORDER BY id DESC LIMIT 100", status, status, conversation_id, conversation_id)]


def draft(channel: str, recipients: str, body: str, subject: str = "", teammate_id: int | None = None,
          conversation_id: int | None = None, created_by: str = "assistant") -> dict:
    ensure()
    if channel not in CHANNELS:
        raise HTTPException(400, "Draft for email, slack or telegram")
    recipients, body = (recipients or "").strip()[:2000], (body or "").strip()[:20000]
    if not recipients or not body:
        raise HTTPException(400, "A draft needs who it's to and what it says")
    did = db.run("INSERT INTO outbox(channel, recipients, subject, body, teammate_id, conversation_id, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", channel, recipients, (subject or "").strip()[:300], body, teammate_id,
                 conversation_id, created_by, db.now())
    return get(did)


def edit(did: int, recipients: str | None, subject: str | None, body: str | None) -> dict:
    d = get(did)
    if d["status"] != "draft":
        raise HTTPException(409, "Only a draft can be edited")
    db.run("UPDATE outbox SET recipients = ?, subject = ?, body = ? WHERE id = ?",
           (recipients if recipients is not None else d["recipients"]).strip()[:2000],
           (subject if subject is not None else d["subject"]).strip()[:300],
           (body if body is not None else d["body"]).strip()[:20000], did)
    return get(did)


def discard(did: int) -> dict:
    d = get(did)
    if d["status"] == "draft":
        db.run("UPDATE outbox SET status = 'discarded' WHERE id = ?", did)
    return get(did)


async def _deliver(d: dict) -> str:
    if d["channel"] == "email":
        from ..assistant import _send_email

        r = await _send_email(d["recipients"], d["subject"] or "(no subject)", d["body"])
        return f"Sent from {r.get('from', '')}"
    if d["channel"] == "telegram":
        from .. import telegram

        if not telegram.token():
            raise HTTPException(400, "Telegram isn't connected. Set it up in Channels.")
        async with httpx.AsyncClient(timeout=30) as c:
            await telegram.send(c, int(d["recipients"]), d["body"])
        return "Sent on Telegram"
    if d["channel"] == "slack":
        from . import channels

        await channels.slack_post(d["recipients"], d["body"])
        return "Sent on Slack"
    raise HTTPException(400, "Unknown channel")


async def send(did: int, origin: str) -> dict:
    """Send one draft. Only the person's own surfaces call this (web app, desktop, paired phone): origin is recorded."""
    if origin not in ("web", "desktop", "mobile"):
        raise HTTPException(403, "Only you can send a draft, from the app or your phone")
    d = get(did)
    if d["status"] != "draft":
        raise HTTPException(409, "That draft was already sent or discarded")
    # claim it first so a double click can't send twice
    with db.tx() as c:
        if c.execute("UPDATE outbox SET status = 'sending' WHERE id = ? AND status = 'draft'", (did,)).rowcount != 1:
            raise HTTPException(409, "That draft is already being sent")
    try:
        detail = await _deliver(d)
    except HTTPException as e:
        db.run("UPDATE outbox SET status = 'draft', detail = ? WHERE id = ?", str(e.detail)[:300], did)
        raise
    except Exception as e:  # noqa: BLE001
        db.run("UPDATE outbox SET status = 'draft', detail = ? WHERE id = ?", f"Couldn't send: {e.__class__.__name__}", did)
        raise HTTPException(502, "Couldn't send it. It's still in your drafts.") from e
    db.run("UPDATE outbox SET status = 'sent', detail = ?, sent_at = ? WHERE id = ?", f"{detail} (sent from {origin})", db.now(), did)
    try:
        from .. import history  # feat/v02-core action history: the send shows on the timeline

        history.record("email" if d["channel"] == "email" else "chat", f"Sent {CHANNELS[d['channel']]} message to {d['recipients']}"[:200],
                       detail=(d["subject"] or d["body"])[:200], source="you", conversation_id=d["conversation_id"],
                       fix="A sent message can't be unsent. Send a follow-up if something was wrong.")
    except Exception:  # noqa: BLE001
        pass
    return get(did)

