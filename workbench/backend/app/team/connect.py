"""Updates coming in (feat/team-lead): member replies (email, text, Telegram, Slack), and AI Workspace Cloud task sync
for members with a Team seat or a private member link. Reading the inbox, Telegram and texts is in replies.py / sms.py,
Jira/Asana/Trello in trackers.py."""

from __future__ import annotations

import json
import re
import logging

from .. import db
from . import engine, parse, store

log = logging.getLogger("team.connect")


def apply_update(tid: int, source: str, parsed: dict, member_id: int | None, ref: str = "") -> dict | None:
    before = store.task(tid)
    u = store.add_update(tid, source, member_id=member_id, status=parsed.get("status") or "", percent=parsed.get("percent"),
                         note=parsed.get("note") or "", ref=ref)
    if u is None:
        return None
    t = store.task(tid)
    who = store.member(member_id)["first_name"] if member_id else "Someone"
    label = {"done": "finished", "blocked": "is stuck on", "doing": "is working on"}.get(t["status"], "updated")
    pct = f" ({t['percent']}%)" if t["status"] == "doing" and t["percent"] else ""
    store.notice("update", f"{who} {label} “{t['title']}”{pct}", parsed.get("note") or "", task_id=tid, member_id=member_id)
    from . import memory

    memory.note(t, store.member(member_id) if member_id else None,
                f"{store.member(member_id)['name'] if member_id else 'Someone'} {label} the task “{t['title']}”{pct}"
                + (f": {parsed['note']}" if parsed.get("note") else ""), source)
    if t["status"] == "done" and before["status"] != "done" and t["assignee_id"]:
        engine.next_for(t["assignee_id"])
    return {"update": u, "task": t}


def inbound(sender: str, text: str, source: str = "email", ref: str = "") -> dict:
    """A reply from a member. Matched to the member by email/phone/Telegram id and to the task by [T12] or their only
    open task. Replies we can't place are kept for the lead as a notice, never guessed."""
    m = store.find_member(sender)
    p = parse.reply(text)
    if not m:
        return {"applied": False, "reason": "Not from anyone on your team."}
    t = place(m, p, text)
    if not t or not (p["understood"] or p["note"]):
        store.notice("update", f"Message from {m['first_name']}", text[:500], member_id=m["id"])
        return {"applied": False, "reason": "Couldn't tell which task this is about, so it's shown to you as a message.", "parsed": p}
    r = apply_update(t["id"], source, p, m["id"], ref)
    return {"applied": r is not None, "task": (r or {}).get("task"), "parsed": p}


_SMALL = {"the", "and", "for", "with", "new", "job", "task", "from", "into", "onto"}


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2 and w not in _SMALL}


def place(m: dict, p: dict, text: str = "") -> dict | None:
    """Which of the member's tasks a reply is about: the [T12] it carries, else their only open task,
    else the one task whose name the reply clearly mentions ("Smith St quote is 80% done")."""
    if p.get("task_ref"):
        return next((x for x in store.tasks(assignee_id=m["id"]) if x["id"] == p["task_ref"]), None)
    open_tasks = store.tasks(assignee_id=m["id"], include_done=False)
    if len(open_tasks) == 1:
        return open_tasks[0]
    said = _words(text)
    scored = []
    for t in open_tasks:
        w = _words(t["title"])
        hit = len(w & said)
        if w and (hit == len(w) or hit >= 2):
            scored.append((hit / len(w), hit, t))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    if scored and (len(scored) == 1 or scored[0][:2] != scored[1][:2]):
        return scored[0][2]
    return None


# --- AI Workspace Cloud (Team plans) ------------------------------------------------------------------------------------
def _team_id() -> str:
    s = json.loads(db.get_setting("account_summary") or "{}")
    t = s.get("team") or {}
    return t.get("id", "") if t.get("role") in ("owner", "admin") else ""


def push_tasks() -> dict:
    from .. import account

    tid = _team_id()
    if not tid:
        return {"synced": 0, "reason": "Sign in with a Team or Business plan to share tasks."}
    ms = {m["id"]: m for m in store.members()}
    payload = [{"local_id": str(t["id"]), "assignee_key": str(t["assignee_id"]),
                "assignee_email": ms[t["assignee_id"]]["contact"].get("email", "") if ms[t["assignee_id"]]["has_app"] else "",
                "title": t["title"], "notes": t["notes"], "project": t["project"], "due": t["due"] or "", "priority": t["priority"],
                "checklist": t["checklist"], "status": t["status"], "percent": t["percent"]}
               for t in store.tasks(team_only=True) if t["assignee_id"] in ms]
    if payload:
        account._cloud("PUT", f"/v1/teams/{tid}/work/tasks", {"tasks": payload[:500]})
    return {"synced": len(payload)}


def pull_updates() -> int:
    from .. import account

    tid = _team_id()
    if not tid:
        return 0
    after = int(store.setting("cloud_after", "0"))
    ups = account._cloud("GET", f"/v1/teams/{tid}/work/updates?after={after}").get("updates", [])
    n = 0
    for u in ups:
        try:
            t = store.task(int(u["local_id"]))
        except Exception:  # noqa: BLE001 - task deleted here since
            continue
        p = {"status": u.get("status") or "", "percent": u.get("percent"), "note": u.get("note") or ""}
        if apply_update(t["id"], "app" if u["source"] == "app" else "link", p, t["assignee_id"], f"cloud:{u['id']}"):
            n += 1
        after = max(after, int(u["id"]))
    store.set_setting("cloud_after", str(after))
    return n


def member_link(mid: int) -> dict:
    from .. import account

    tid = _team_id()
    m = store.member(mid)
    if not tid:
        return {"url": "", "reason": "Member links need a Team or Business plan (sign in on Your plan)."}
    r = account._cloud("POST", f"/v1/teams/{tid}/work/links", {"member_key": str(mid), "name": m["name"]})
    store.set_member_link(mid, r["url"])
    push_tasks()
    return r
