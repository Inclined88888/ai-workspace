"""Suggestions, risk detection, today's plan, workload and the digest (feat/team-lead).

Scoring is deterministic and explained in plain English, so the lead can see why someone was suggested:
skills that match the task, how booked they are this week, and whether they work on the day it's due.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

from .. import db
from . import store

STOP = set("the a an and or of for to in on at by with from my our your this that job task please do get up".split())


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(w) > 2 and w not in STOP}


def _stem(w: str) -> str:
    return re.sub(r"(ing|ers|er|es|s|ed|e)$", "", w)


def works_on(m: dict, d: date) -> bool:
    if store.DAYS[d.weekday()] not in m["work_days"]:
        return False
    for a in m["away"]:
        if a.get("from") and a["from"] <= d.isoformat() <= (a.get("to") or a["from"]):
            return False
    return True


def next_workday(m: dict, d: date, limit: int = 21) -> date | None:
    for i in range(limit):
        if works_on(m, d + timedelta(days=i)):
            return d + timedelta(days=i)
    return None


def workload(m: dict, open_tasks: list[dict] | None = None) -> dict:
    ts = open_tasks if open_tasks is not None else store.tasks(assignee_id=m["id"], include_done=False)
    hours = sum(t["estimate_hours"] * (1 - t["percent"] / 100) for t in ts)
    week_hours = m["hours_per_week"] * (len(m["work_days"]) or 5) / 5 if m["work_days"] else 0
    pct = round(100 * hours / week_hours) if week_hours else 100
    return {"member_id": m["id"], "open": len(ts), "hours": round(hours, 1), "capacity": round(week_hours, 1), "percent": pct,
            "label": "Free" if not ts else "Light" if pct < 50 else "Busy" if pct <= 100 else "Overloaded"}


def _fit(t: dict, m: dict) -> tuple[float, list[str]]:
    need = {_stem(w) for w in _words(" ".join([t["title"], t["notes"], t["project"], " ".join(t["skills"])]))}
    have = {_stem(w) for s in m["skills"] + [m["role"]] for w in _words(s)}
    hit = sorted(need & have)
    return float(len(hit)), hit


def score(t: dict, m: dict, loads: dict[int, dict]) -> tuple[float, str]:
    fit, hit = _fit(t, m)
    load = loads[m["id"]]
    due = date.fromisoformat(t["due"]) if t["due"] else None
    day = due or store.today()
    avail = works_on(m, day) if due else works_on(m, store.today()) or bool(next_workday(m, store.today(), 3))
    s = fit * 3 - load["percent"] / 40 + (1.5 if avail else -4)
    why = []
    if hit:
        why.append(f"knows {', '.join(hit[:3])}")
    why.append(f"{load['label'].lower()} this week ({load['open']} open task{'s' if load['open'] != 1 else ''})")
    if not avail:
        why.append("not working " + (f"on {day:%a %d %b}" if due else "in the next few days"))
    elif due:
        why.append(f"works {day:%a}")
    return s, f"{m['first_name']}: " + ", ".join(why)


def suggest_assignee(t: dict, exclude: set[int] | None = None) -> dict | None:
    ms = [m for m in store.members() if m["id"] not in (exclude or set())]
    if not ms:
        return None
    loads = {m["id"]: workload(m) for m in ms}
    ranked = sorted(((score(t, m, loads), m) for m in ms), key=lambda x: -x[0][0])
    (s, why), m = ranked[0]
    return {"member": m, "score": round(s, 2), "reason": why,
            "others": [{"member_id": o["id"], "name": o["name"], "reason": w} for (_, w), o in ranked[1:4]]}


def _pending(kind: str, task_id: int | None, member_id: int | None) -> bool:
    return bool(db.one("SELECT 1 FROM team_suggestions WHERE kind = ? AND task_id IS ? AND member_id IS ? AND status = 'pending'",
                       kind, task_id, member_id))


def add_suggestion(kind: str, task_id: int | None, member_id: int | None, reason: str, from_member_id: int | None = None) -> dict | None:
    store.ensure()
    if _pending(kind, task_id, member_id):
        return None
    sid = db.run("INSERT INTO team_suggestions(kind, task_id, member_id, from_member_id, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 kind, task_id, member_id, from_member_id, reason[:500], store.now())
    s = suggestion(sid)
    if store.setting("auto_assign") == "1" and kind in ("assign", "next"):
        return decide(sid, True, auto=True)
    return s


def suggestion(sid: int) -> dict:
    s = db.one("SELECT * FROM team_suggestions WHERE id = ?", int(sid))
    if not s:
        from fastapi import HTTPException

        raise HTTPException(404, "Suggestion not found")
    return _sug_out(s)


def _sug_out(s: dict) -> dict:
    s = dict(s)
    try:
        s["task"] = store.task(s["task_id"]) if s["task_id"] else None
    except Exception:  # noqa: BLE001
        s["task"] = None
    names = {m["id"]: m["name"] for m in store.members(include_inactive=True)}
    s["member_name"] = names.get(s["member_id"], "")
    s["from_member_name"] = names.get(s["from_member_id"], "")
    verbs = {"assign": "Give", "rebalance": "Move", "next": "Next up:"}
    title = (s["task"] or {}).get("title", "a task")
    s["headline"] = (f"Move “{title}” from {s['from_member_name']} to {s['member_name']}" if s["kind"] == "rebalance"
                     else f"{verbs.get(s['kind'], 'Give')} “{title}” to {s['member_name']}" if s["kind"] == "assign"
                     else f"Next for {s['member_name']}: “{title}”")
    return s


def suggestions(status: str = "pending") -> list[dict]:
    store.ensure()
    return [_sug_out(s) for s in db.all("SELECT * FROM team_suggestions WHERE status = ? ORDER BY id DESC LIMIT 100", status)]


def decide(sid: int, approve: bool, auto: bool = False) -> dict:
    s = suggestion(sid)
    if s["status"] != "pending":
        return s
    db.run("UPDATE team_suggestions SET status = ?, decided_at = ? WHERE id = ?",
           ("auto" if auto else "approved") if approve else "dismissed", store.now(), int(sid))
    if approve and s["task_id"] and s["member_id"]:
        from . import outbox

        store.update_task(s["task_id"], {"assignee_id": s["member_id"]})
        outbox.assignment(s["task_id"])
    return suggestion(sid)


def suggest_for_unassigned() -> list[dict]:
    out = []
    for t in store.tasks(include_done=False, mine=False):
        if t["assignee_id"] is None and store.members():
            s = suggest_assignee(t)
            if s:
                r = add_suggestion("assign", t["id"], s["member"]["id"], s["reason"])
                if r:
                    out.append(r)
    return out


def next_for(member_id: int) -> dict | None:
    """When someone finishes: the best waiting task for them (unassigned first, then from someone overloaded)."""
    m = store.member(member_id)
    loads = {x["id"]: workload(x) for x in store.members()}
    pool = [t for t in store.tasks(include_done=False, mine=False) if t["assignee_id"] is None]
    from_other = None
    if not pool:
        heavy = [x for x in store.members() if x["id"] != m["id"] and loads[x["id"]]["percent"] > 100]
        for h in sorted(heavy, key=lambda x: -loads[x["id"]]["percent"]):
            pool = [t for t in store.tasks(assignee_id=h["id"], status="todo")]
            if pool:
                from_other = h["id"]
                break
    if not pool:
        return None
    best = max(pool, key=lambda t: (score(t, m, loads)[0] + t["priority"] - (0 if not t["due"] else
                                     (date.fromisoformat(t["due"]) - store.today()).days / 7)))
    reason = score(best, m, loads)[1] + (" · freed up" if True else "")
    return add_suggestion("rebalance" if from_other else "next", best["id"], m["id"], reason, from_other)


def rebalance() -> list[dict]:
    ms = store.members()
    if len(ms) < 2:
        return []
    loads = {m["id"]: workload(m) for m in ms}
    out = []
    for h in [m for m in ms if loads[m["id"]]["percent"] > 100]:
        movable = sorted(store.tasks(assignee_id=h["id"], status="todo"), key=lambda t: (t["priority"], t["due"] or "9999"))
        if not movable:
            continue
        t = movable[0]
        light = [m for m in ms if m["id"] != h["id"] and loads[m["id"]]["percent"] < 70]
        if not light:
            continue
        best = max(light, key=lambda m: score(t, m, loads)[0])
        r = add_suggestion("rebalance", t["id"], best["id"],
                           f"{h['first_name']} is {loads[h['id']]['percent']}% booked; " + score(t, best, loads)[1], h["id"])
        if r:
            out.append(r)
    return out


# --- risk ------------------------------------------------------------------------------------------------------------
def _days_since(iso: str | None) -> float:
    if not iso:
        return 0.0
    try:
        return (store.utcnow() - datetime.fromisoformat(iso)).total_seconds() / 86400
    except ValueError:
        return 0.0


def risk(t: dict) -> dict | None:
    if t["status"] == "done":
        return None
    today = store.today()
    stuck_days = float(store.setting("stuck_days", "3"))
    if t["due"] and t["due"] < today.isoformat():
        n = (today - date.fromisoformat(t["due"])).days
        return {"level": "late", "label": f"{n} day{'s' if n != 1 else ''} late"}
    if t["status"] == "blocked":
        return {"level": "stuck", "label": "Stuck: " + (t["blocked_reason"] or "no reason given")[:80]}
    if t["due"]:
        left = (date.fromisoformat(t["due"]) - today).days
        if left <= 1 and t["percent"] < 50:
            return {"level": "at_risk", "label": "Due " + ("today" if left == 0 else "tomorrow") + f", {t['percent']}% done"}
        if left <= 3 and t["status"] == "todo" and t["assignee_id"]:
            return {"level": "at_risk", "label": f"Due in {left} days and not started"}
    quiet = _days_since(t["last_progress_at"] or t["assigned_at"])
    if t["status"] == "doing" and quiet >= stuck_days:
        return {"level": "stuck", "label": f"No update for {int(quiet)} days"}
    return None


def check(notify: bool = True) -> dict:
    """Find late, at-risk and stuck tasks and idle people; notify the lead once a day per item; queue nudges."""
    from . import outbox

    found = {"late": [], "at_risk": [], "stuck": [], "idle": [], "nudges": []}
    day = store.today().isoformat()
    names = {m["id"]: m for m in store.members()}
    for t in store.tasks(include_done=False, team_only=True):
        r = risk(t)
        if not r:
            continue
        found[r["level"]].append({"task_id": t["id"], "title": t["title"], "label": r["label"], "member_id": t["assignee_id"]})
        who = names.get(t["assignee_id"], {}).get("name", "Nobody")
        if notify:
            store.notice(r["level"], f"{t['title']}: {r['label']}", f"Assigned to {who}.", task_id=t["id"],
                         member_id=t["assignee_id"], dedupe=f"{r['level']}:{t['id']}:{day}")
        if t["assignee_id"] and r["level"] in ("late", "at_risk", "stuck") and not t["status"] == "blocked":
            n = outbox.nudge(t["id"], r["label"], dedupe_day=day)
            if n:
                found["nudges"].append(n["id"])
    for m in names.values():
        if works_on(m, store.today()) and not store.tasks(assignee_id=m["id"], include_done=False):
            found["idle"].append({"member_id": m["id"], "name": m["name"]})
            if notify:
                store.notice("idle", f"{m['first_name']} has nothing on today", "Open the plan to give them something.",
                             member_id=m["id"], dedupe=f"idle:{m['id']}:{day}")
            next_for(m["id"])
    suggest_for_unassigned()
    rebalance()
    return found


# --- plans and digests -------------------------------------------------------------------------------------------------
def today_plan(d: date | None = None) -> dict:
    d = d or store.today()
    people = []
    for m in store.members():
        if not works_on(m, d):
            people.append({"member": _brief(m), "working": False, "tasks": [], "message": ""})
            continue
        ts = store.tasks(assignee_id=m["id"], include_done=False)
        ts.sort(key=lambda t: (0 if t["due"] and t["due"] < d.isoformat() else 1 if t["due"] == d.isoformat() else
                               2 if t["status"] == "doing" else 3, -t["priority"], t["due"] or "9999"))
        budget, chosen = m["hours_per_week"] / max(1, len(m["work_days"])), []
        for t in ts:
            if budget <= 0 and chosen:
                break
            chosen.append(t)
            budget -= t["estimate_hours"] * (1 - t["percent"] / 100)
        lines = [f"• {t['title']}" + (f" (due {_nice(t['due'])})" if t["due"] else "") + (f" [T{t['id']}]") for t in chosen]
        msg = (f"Morning {m['first_name']}, here's today:\n" + "\n".join(lines)) if chosen else ""
        people.append({"member": _brief(m), "working": True, "tasks": [_tbrief(t) for t in chosen], "message": msg,
                       "load": workload(m, ts)})
    unassigned = [_tbrief(t) for t in store.tasks(include_done=False, mine=False) if t["assignee_id"] is None]
    return {"date": d.isoformat(), "people": people, "unassigned": unassigned}


def _nice(iso: str | None) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    t = store.today()
    return "today" if d == t else "tomorrow" if d == t + timedelta(days=1) else f"{d:%a %d %b}"


def _brief(m: dict) -> dict:
    return {"id": m["id"], "name": m["name"], "first_name": m["first_name"], "role": m["role"], "channel": m["channel"]}


def _tbrief(t: dict) -> dict:
    return {"id": t["id"], "title": t["title"], "due": t["due"], "status": t["status"], "percent": t["percent"],
            "priority": t["priority"], "project": t["project"], "risk": risk(t)}


def board() -> dict:
    ms = store.members()
    ts = store.tasks(team_only=True)
    loads = {m["id"]: workload(m, [t for t in ts if t["assignee_id"] == m["id"] and t["status"] != "done"]) for m in ms}
    for t in ts:
        t["risk"] = risk(t)
    counts = {s: sum(1 for t in ts if t["status"] == s) for s in store.STATUSES}
    return {"members": [{**m, "load": loads[m["id"]]} for m in ms], "tasks": ts, "counts": counts,
            "at_risk": sum(1 for t in ts if t["risk"]), "unassigned": sum(1 for t in ts if t["assignee_id"] is None and t["status"] != "done")}


def digest(period: str = "daily") -> dict:
    days = 7 if period == "weekly" else 1
    since = (store.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")
    ts = store.tasks(team_only=True)
    done = [t for t in ts if t["done_at"] and t["done_at"] >= since]
    late = [t for t in ts if (risk(t) or {}).get("level") == "late"]
    stuck = [t for t in ts if (risk(t) or {}).get("level") == "stuck"]
    upd = [u for u in store.updates(limit=200) if u["created_at"] >= since]
    lines = [f"{'This week' if days == 7 else 'Today'}: {len(done)} finished, {len(upd)} updates, {len(late)} late, {len(stuck)} stuck."]
    lines += [f"✓ {t['title']}" for t in done[:10]]
    lines += [f"⚠ Late: {t['title']} ({risk(t)['label']})" for t in late[:10]]
    lines += [f"⏸ Stuck: {t['title']} ({risk(t)['label']})" for t in stuck[:10]]
    return {"period": period, "text": "\n".join(lines), "done": len(done), "late": len(late), "stuck": len(stuck), "updates": len(upd)}


def maybe_digest() -> dict | None:
    mode = store.setting("digest", "daily")
    if mode == "off":
        return None
    now = datetime.now()
    hour = int(store.setting("digest_hour", "17"))
    key = now.strftime("%G-W%V") if mode == "weekly" else now.date().isoformat()
    if now.hour < hour or (mode == "weekly" and now.weekday() != 4) or store.setting("last_digest") == key:
        return None
    store.set_setting("last_digest", key)
    d = digest(mode)
    store.notice("digest", f"Team {'weekly' if mode == 'weekly' else 'daily'} summary", d["text"], dedupe=f"digest:{key}")
    return d
