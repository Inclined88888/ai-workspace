"""Reading member replies (feat/team-lead): the lead's connected inbox, Telegram and text messages (Twilio).

Approval-safe by design:
- Only messages from people on the team are read; everything else in the inbox is left alone (only the sender line is
  looked at, the message isn't opened).
- Nothing is ever sent because of a reply. A finished task can lead to a "next up" *suggestion*, which still waits for
  the lead (or the lead's own auto-assign rule).
- Replies read automatically are held for the lead's OK by default ("Check replies before they change tasks", on),
  because an email "From" line can be faked. The lead can switch that off for trusted channels.
- Each message is handled once (remembered by mailbox + message id).
"""

from __future__ import annotations

import json
import logging
import re
from email.utils import parseaddr

from fastapi import HTTPException

from .. import db
from . import connect, parse, store

log = logging.getLogger("team.replies")

SCHEMA = """
CREATE TABLE IF NOT EXISTS team_seen (
  source TEXT NOT NULL,
  ref TEXT NOT NULL,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (source, ref)
);
CREATE TABLE IF NOT EXISTS team_pending (
  id INTEGER PRIMARY KEY,
  member_id INTEGER REFERENCES team_members(id) ON DELETE CASCADE,
  task_id INTEGER REFERENCES team_tasks(id) ON DELETE CASCADE,
  source TEXT NOT NULL,
  ref TEXT NOT NULL DEFAULT '',
  text TEXT NOT NULL,
  parsed TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'waiting',     -- waiting | applied | dismissed
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""
_ready: set[str] = set()


def ensure() -> None:
    store.ensure()
    from .. import config

    if str(config.DB_PATH) not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


def review_on() -> bool:
    return store.setting("replies_review", "1") != "0"


def seen(source: str, ref: str) -> bool:
    ensure()
    return bool(db.one("SELECT 1 FROM team_seen WHERE source = ? AND ref = ?", source, ref))


def mark(source: str, ref: str) -> None:
    db.run("INSERT OR IGNORE INTO team_seen(source, ref, seen_at) VALUES (?, ?, ?)", source, ref, store.now())


def receive(sender: str, text: str, source: str, ref: str) -> dict:
    """One reply read automatically. Applied straight away, or held for the lead when review is on."""
    ensure()
    if ref and seen(source, ref):
        return {"applied": False, "reason": "Already handled."}
    if ref:
        mark(source, ref)
    m = store.find_member(sender)
    if not m:
        return {"applied": False, "reason": "Not from anyone on your team."}
    store.touch_member(m["id"], last_heard_at=store.now())
    if not review_on():
        return connect.inbound(sender, text, source, ref)
    p = parse.reply(text)
    t = connect.place(m, p)
    pid = db.run("INSERT INTO team_pending(member_id, task_id, source, ref, text, parsed, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 m["id"], t["id"] if t else None, source, ref, text[:4000], json.dumps(p), store.now())
    what = f"“{t['title']}”: {describe(p)}" if t else "a message (not sure which task)"
    store.notice("reply", f"{m['first_name']} replied about {what}", "Check it before it changes the task.",
                 task_id=t["id"] if t else None, member_id=m["id"], dedupe=f"pending:{pid}")
    return {"applied": False, "pending": pid, "reason": "Waiting for your OK."}


def describe(p: dict) -> str:
    if p.get("status") == "done":
        return "says it's done"
    if p.get("status") == "blocked":
        return "is stuck" + (f" ({p['note']})" if p.get("note") else "")
    if p.get("percent") is not None:
        return f"{p['percent']}% done"
    if p.get("status") == "doing":
        return "has started"
    return "sent a note"


def pending(status: str = "waiting") -> list[dict]:
    ensure()
    out = []
    for r in db.all("SELECT p.*, m.name AS member_name, t.title AS task_title FROM team_pending p "
                    "LEFT JOIN team_members m ON m.id = p.member_id LEFT JOIN team_tasks t ON t.id = p.task_id "
                    "WHERE p.status = ? ORDER BY p.id DESC LIMIT 100", status):
        r = dict(r)
        r["parsed"] = json.loads(r["parsed"] or "{}")
        r["summary"] = describe(r["parsed"])
        out.append(r)
    return out


def decide(pid: int, approve: bool, task_id: int | None = None) -> dict:
    ensure()
    r = db.one("SELECT * FROM team_pending WHERE id = ?", int(pid))
    if not r:
        raise HTTPException(404, "Reply not found")
    if r["status"] != "waiting":
        raise HTTPException(409, "Already decided")
    res = None
    if approve:
        tid = task_id or r["task_id"]
        if not tid:
            raise HTTPException(400, "Pick which task this reply is about.")
        if store.task(tid)["assignee_id"] != r["member_id"]:
            raise HTTPException(400, "That task isn't assigned to this person.")
        res = connect.apply_update(tid, r["source"], json.loads(r["parsed"]), r["member_id"], r["ref"])
    db.run("UPDATE team_pending SET status = ?, decided_at = ?, task_id = COALESCE(?, task_id) WHERE id = ?",
           "applied" if approve else "dismissed", store.now(), task_id, int(pid))
    return {"ok": True, "task": (res or {}).get("task")}


# --- the lead's inbox ----------------------------------------------------------------------------------------------------
def poll_inbox(limit: int = 40) -> dict:
    """New mail from team members in the lead's connected mailbox. First run only remembers what's there already."""
    from .. import mail
    from . import outbox

    ensure()
    acct = outbox.mail_account()
    if not acct:
        return {"read": 0, "reason": "No mailbox connected."}
    src = f"mail:{acct['id']}"
    msgs = mail.list_messages(acct, "INBOX", limit)
    first = store.setting(f"inbox_started_{acct['id']}") != "1"
    n = 0
    for x in msgs:
        ref = str(x.get("uid") or "")
        if not ref or seen(src, ref):
            continue
        addr = parseaddr(x.get("from") or "")[1].lower()
        if first or not addr or not store.find_member(addr):
            mark(src, ref)
            continue
        try:
            full = mail.get_message(acct, ref)
        except Exception as e:  # noqa: BLE001 - one bad message never stops the rest
            log.warning("team inbox read failed: %s", e.__class__.__name__)
            continue
        text = f"{full.get('subject', '')}\n{full.get('body', '')}"
        receive(addr, text, "email", f"{src}:{ref}")
        mark(src, ref)
        n += 1
    store.set_setting(f"inbox_started_{acct['id']}", "1")
    return {"read": n}


# --- Telegram ----------------------------------------------------------------------------------------------------------
def from_telegram(msg: dict) -> bool:
    """Called by the Telegram gateway for chats that aren't the lead's own. True when it was a team member's reply."""
    try:
        ensure()
        chat = str((msg.get("chat") or {}).get("id", ""))
        user = str((msg.get("from") or {}).get("id", ""))
        uname = "@" + str((msg.get("from") or {}).get("username", "")).lower()
        text = (msg.get("text") or "").strip()
        m = next((x for x in store.members() if str(x["contact"].get("telegram", "")).lower() in (chat, user, uname)
                  and x["contact"].get("telegram")), None)
        if not m or not text:
            return False
        receive(str(m["contact"]["telegram"]).lower(), text, "telegram", f"tg:{chat}:{msg.get('message_id', '')}")
        return True
    except Exception as e:  # noqa: BLE001 - never break the lead's own Telegram chat
        log.warning("team telegram reply failed: %s", e.__class__.__name__)
        return False


def strip_sig(text: str) -> str:
    return re.split(r"\n--\s*\n", text or "", maxsplit=1)[0]
