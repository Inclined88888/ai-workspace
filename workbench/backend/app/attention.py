"""Needs You: everything waiting for a decision from you, in one list.

Each item says what happened, why it needs you, the options with what each one does, the risks and costs, and what
happens next. Options are single, specific actions on that one item (approve this request, accept this result, run
this again); there is never a blanket grant. Deferring an item hides it for a while without deciding anything.

Items are read live from where they already live (approvals, workers, missions, research, check-ins, schedules, calls,
tasks, projects), so nothing here can drift out of step with the real state.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from . import audit, db, permissions

PRIORITY = {"approval": 0, "spending": 1, "call": 1, "worker": 2, "mission": 2, "automation": 3, "research": 3, "reminder": 4, "follow_up": 5}


def _deferred() -> dict:
    try:
        d = json.loads(db.get_setting("attention_deferred", "") or "{}")
    except ValueError:
        d = {}
    now = db.now()
    return {k: v for k, v in d.items() if v > now}


def defer(item_id: str, hours: float) -> dict:
    if not 0 < hours <= 24 * 14:
        raise HTTPException(400, "Defer for between a few minutes and two weeks.")
    d = _deferred()
    d[item_id[:80]] = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")
    db.set_setting("attention_deferred", json.dumps(d))
    audit.record("user", "needs you deferred", item_id[:80], f"{hours:g} hours")
    return {"deferred_until": d[item_id[:80]]}


def _act(label: str, method: str, path: str, effect: str, body: dict | None = None, tone: str = "", confirm: str = "") -> dict:
    return {"label": label, "method": method, "path": path, "body": body, "effect": effect, "tone": tone, "confirm": confirm}


def _item(kind: str, key: str, title: str, what: str, why: str, actions: list[dict], at: str, risk: str = "", cost: str = "",
          after: str = "", link: str = "") -> dict:
    return {"id": f"{kind}:{key}", "type": kind, "title": title, "what": what, "why": why, "actions": actions, "risk": risk,
            "cost": cost, "after": after, "link": link, "at": at, "priority": PRIORITY[kind]}


def _approvals() -> list[dict]:
    from . import computer_tools
    from .assistant import approval_view

    out = []
    for a in db.all("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id"):
        a = approval_view(a)
        where, link = "a chat", f"/assistant/{a['conversation_id']}" if a["conversation_id"] else "/assistant"
        if a["conversation_id"]:
            m = db.one("SELECT id, goal FROM missions WHERE conversation_id = ?", a["conversation_id"])
            w = db.one("SELECT id, mission_id, goal FROM worker_tasks WHERE conversation_id = ?", a["conversation_id"])
            if m:
                where, link = f"mission #{m['id']} ({m['goal'][:60]})", f"/missions/{m['id']}"
            elif w:
                where, link = f"worker #{w['id']} of mission #{w['mission_id']} ({w['goal'][:60]})", f"/missions/{w['mission_id']}"
            elif a["channel"] in ("telegram", "schedule", "agent"):
                where = {"telegram": "a Telegram chat", "schedule": "a scheduled job", "agent": "an agent"}[a["channel"]]
        risk = permissions.TOOL_RISK.get(a["tool"], permissions.WRITE)
        computer = a["tool"] in computer_tools.KIND
        out.append(_item(
            "approval", str(a["id"]), f"Approve {a['tool']}?", f"In {where}, ROOKS wants to run {a['tool']} with: {a['args'][:600]}",
            a["reason"] or "You asked to approve this kind of action.",
            [_act("Approve once", "POST", f"/approvals/{a['id']}", "Runs exactly this action, once. Nothing else is allowed by it.", {"approve": True}, "primary"),
             _act("Deny", "POST", f"/approvals/{a['id']}", "It is not run; the work it belongs to is told and carries on without it.", {"approve": False})],
            a["created_at"],
            risk=("It acts on your computer." if computer else "It acts outside ROOKS (websites, GitHub or schedules)." if risk == permissions.CONSEQUENTIAL
                  else "It changes something in your workspace."),
            after="Approving runs it now and the waiting work continues; denying lets the work continue without it.", link=link))
    return out


def _workers() -> list[dict]:
    out = []
    for w in db.all("SELECT id, mission_id, role, goal, status, attention, used_cents, budget_cents, result, verification, updated_at, attempts, "
                    "max_attempts, user_started FROM worker_tasks WHERE status IN ('needs_you', 'failed') ORDER BY id"):
        link = f"/missions/{w['mission_id']}"
        has_result = bool(w["result"] and json.loads(w["result"]).get("summary"))
        title = f"Worker #{w['id']}: {w['goal'][:80]}"
        att = w["attention"] or ""
        if w["role"] == "computer" and not w["user_started"] and w["status"] == "needs_you":
            out.append(_item("worker", str(w["id"]), title, "This worker uses apps on your computer.", "Computer work never starts on its own.",
                             [_act("Start it now", "POST", f"/workers/{w['id']}/start", "It runs once, using your computer, within its tools and approvals.", None, "primary"),
                              _act("Cancel it", "POST", f"/workers/{w['id']}/cancel", "It never runs; work that waits for it will not run either.")],
                             w["updated_at"], risk="It controls apps on your computer; actions still follow your Computer Use permissions.",
                             cost=f"Up to {w['budget_cents']:g} cents of model use." if w["budget_cents"] else "", link=link))
        elif att.startswith("Budget"):
            out.append(_item("spending", f"worker-{w['id']}", title, att, "Spending limits stop work instead of going over.",
                             [_act("Run it again", "POST", f"/workers/{w['id']}/retry", "It tries to start again; it still cannot go over any limit.", {}),
                              *([_act("Accept what it has", "POST", f"/workers/{w['id']}/accept", "Its partial result counts as done, marked as accepted by you.", {})] if has_result else []),
                              _act("Cancel it", "POST", f"/workers/{w['id']}/cancel", "It stops for good.")],
                             w["updated_at"], cost=f"{w['used_cents']:.2f} of {w['budget_cents']:g} cents used." if w["budget_cents"] else f"{w['used_cents']:.2f} cents used.",
                             after="To give it more room, raise its cap on the mission page first.", link=link))
        else:
            failed = w["status"] == "failed"
            out.append(_item("worker", str(w["id"]), title, att or ("It failed." if failed else "It needs your decision."),
                             "It could not be verified or completed on its own." if not failed else f"It failed after {w['attempts']} attempt(s).",
                             [*([_act("Accept the result", "POST", f"/workers/{w['id']}/accept", "The result counts as done, marked as accepted by you, not verified.", {}, "primary")]
                                if has_result and not failed else []),
                              _act("Run it again", "POST", f"/workers/{w['id']}/retry", "It starts over with its retries reset; the reason is carried into the next attempt.", {}),
                              _act("Cancel it", "POST", f"/workers/{w['id']}/cancel", "It stops for good; work that waits for it will not run.")],
                             w["updated_at"], risk="Accepting an unverified result means relying on something no check confirmed." if has_result else "",
                             after="Work waiting for this worker continues once it is done.", link=link))
    return out


def _missions() -> list[dict]:
    out = []
    for m in db.all("SELECT id, goal, status, error, updated_at FROM missions WHERE status IN ('review', 'review_step', 'paused', 'failed', 'partial') "
                    "ORDER BY id DESC LIMIT 30"):
        link = f"/missions/{m['id']}"
        if m["status"] == "review":
            out.append(_item("mission", str(m["id"]), f"Check the plan: {m['goal'][:80]}", "ROOKS planned this mission. Nothing has run.",
                             "A mission only starts after you approve its plan.",
                             [_act("Approve the plan and start", "POST", f"/missions/{m['id']}/start", "It works through the plan; actions you set to ask still ask.", None, "primary")],
                             m["updated_at"], cost="Its spending cap is reserved from your monthly limit when it starts.", link=link))
        elif m["status"] == "review_step":
            out.append(_item("mission", str(m["id"]), f"Check a step: {m['goal'][:80]}", m["error"], "The step finished, but no check could confirm it.",
                             [], m["updated_at"], after="Accept or redo the step on the mission page.", link=link))
        elif m["status"] == "paused" and ("limit" in m["error"] or "budget" in m["error"].lower()):
            out.append(_item("spending", f"mission-{m['id']}", f"Paused by a limit: {m['goal'][:80]}", m["error"], "Limits stop work instead of going over.",
                             [], m["updated_at"], after="Raise the limit on the mission page and resume, or leave it paused.", link=link))
        elif m["status"] in ("failed", "partial"):
            out.append(_item("mission", str(m["id"]), f"{'Stopped' if m['status'] == 'failed' else 'Finished with gaps'}: {m['goal'][:80]}",
                             m["error"] or "Some steps could not be verified.", "Its result is incomplete.",
                             [_act("Run again from the first unfinished step", "POST", f"/missions/{m['id']}/start", "Finished steps are kept; write actions done before are replayed, not repeated.")],
                             m["updated_at"], link=link))
    return out


def _research() -> list[dict]:
    out = []
    for r in db.all("SELECT id, question, status, error, updated_at FROM research WHERE status = 'error' ORDER BY id DESC LIMIT 10"):
        out.append(_item("research", str(r["id"]), f"Research failed: {r['question'][:80]}", r["error"], "It stopped before writing a report.",
                         [_act("Try again", "POST", f"/research/{r['id']}/retry", "It runs again from the start with the same question and depth.", None, "primary")],
                         r["updated_at"], link=f"/research/{r['id']}"))
    for r in db.all("SELECT r.id, r.question, r.updated_at, COUNT(e.id) AS n FROM research r JOIN evidence e ON e.research_id = r.id "
                    "WHERE r.status = 'done' AND e.verification = 'not_supported' AND r.updated_at >= ? GROUP BY r.id ORDER BY r.id DESC LIMIT 5",
                    (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds")):
        out.append(_item("research", f"doubt-{r['id']}", f"Check before relying on: {r['question'][:80]}",
                         f"{r['n']} claim(s) in this report were not supported by their own source on an independent re-read.",
                         "A report should not be relied on for claims its sources do not support.", [], r["updated_at"],
                         after="Open the report's evidence table; uncertain claims are marked.", link=f"/research/{r['id']}"))
    return out


def _automations() -> list[dict]:
    out = []
    for t in db.all("SELECT id, name, last_error, failures, enabled, updated_at FROM topics WHERE last_error != '' ORDER BY id"):
        out.append(_item("automation", f"checkin-{t['id']}", f"Check-in failing: {t['name']}", t["last_error"],
                         f"{t['failures']} failure(s) in a row." + ("" if t["enabled"] else " It was turned off after repeated failures."),
                         [_act("Check now", "POST", f"/checkins/{t['id']}/run", "Runs it once now; a success clears the error.", None, "primary")],
                         t["updated_at"], link="/automations"))
    for s in db.all("SELECT id, name, last_status, last_run FROM schedules WHERE last_status != '' AND last_status != 'ok' ORDER BY id"):
        out.append(_item("automation", f"schedule-{s['id']}", f"Scheduled job failed: {s['name']}", s["last_status"], "Its last run did not finish.",
                         [_act("Run now", "POST", f"/schedules/{s['id']}/run", "Runs it once now and delivers the result to the inbox.", None, "primary")],
                         s["last_run"] or "", link="/automations"))
    return out


def _calls() -> list[dict]:
    from . import calls

    out = []
    for i in calls.pending():
        out.append(_item("call", str(i["id"]), f"ROOKS would like to talk: {i['title']}", i["briefing"][:400],
                         "A check-in you set to call found something new.",
                         [_act("Decline", "POST", f"/calls/invites/{i['id']}/decline", "No call; the briefing arrives in writing, once.")],
                         i["created_at"], after=f"Accept from the banner to talk. If you do nothing it becomes a written briefing at {i['expires_at'][11:16]} UTC.",
                         link="/assistant"))
    return out


def _spending() -> list[dict]:
    from .usage import month_start

    limit = float(db.get_setting("monthly_limit_cents", "0") or 0)
    if limit <= 0:
        return []
    spent = db.one("SELECT COALESCE(SUM(cost_cents), 0) AS c FROM usage WHERE ts >= ?", month_start())["c"]
    if spent < 0.9 * limit:
        return []
    return [_item("spending", f"month-{month_start()[:7]}", "This month's model spending is near its limit",
                  f"{spent:.0f} of {limit:.0f} cents used this month.", "At the limit, model calls stop until next month or until you raise it.",
                  [], db.now(), after="Raise or keep the limit under Settings, Usage.", link="/settings#usage")]


def _reminders() -> list[dict]:
    from .assistant import local_now

    today = local_now().date().isoformat()
    return [_item("reminder", str(t["id"]), f"{'Overdue' if t['due'] < today else 'Due today'}: {t['title']}", t["notes"][:300] or "A task on your list.",
                  f"It was due {t['due']}.",
                  [_act("Mark done", "PATCH", f"/tasks/{t['id']}", "The task is completed.", {"status": "done"}, "primary")],
                  t["updated_at"], link="/tasks")
            for t in db.all("SELECT id, title, notes, due, updated_at FROM tasks WHERE status != 'done' AND due IS NOT NULL AND due <= ? ORDER BY due LIMIT 20", today)]


def _follow_ups() -> list[dict]:
    return [_item("follow_up", str(n["id"]), f"Open question in {n['name']}", n["text"], "Recorded as open in the project.",
                  [], n["created_at"], after="Answer it on the project page; the answer is kept with the project.", link=f"/projects/{n['project_id']}")
            for n in db.all("SELECT n.id, n.text, n.created_at, n.project_id, p.name FROM project_notes n JOIN projects p ON p.id = n.project_id "
                            "WHERE n.kind = 'question' AND n.resolved = 0 AND p.status = 'active' ORDER BY n.id DESC LIMIT 15")]


def items(include_deferred: bool = False) -> dict:
    all_ = [*_approvals(), *_workers(), *_missions(), *_research(), *_automations(), *_calls(), *_spending(), *_reminders(), *_follow_ups()]
    deferred = _deferred()
    shown = [i for i in all_ if include_deferred or i["id"] not in deferred]
    for i in shown:
        i["deferred_until"] = deferred.get(i["id"])
    shown.sort(key=lambda i: (i["priority"], i["at"] or ""))
    return {"items": shown, "deferred": sum(1 for i in all_ if i["id"] in deferred),
            "counts": {k: sum(1 for i in shown if i["type"] == k) for k in PRIORITY}}


def count() -> int:
    return len(items()["items"])
