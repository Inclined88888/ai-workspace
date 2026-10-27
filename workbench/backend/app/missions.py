"""Missions: a substantial task that ROOKS plans, the user approves, and ROOKS works through step by step.

Lifecycle: planning -> review (the user checks the plan) -> queued -> running -> done | partial | failed | cancelled,
with paused (by the user or a budget) and waiting_approval along the way.

Each step is an ordinary assistant turn, so every permission and approval rule applies. After a step, a separate
check compares what happened against the step's "done when" criteria, and the code adds rules the model cannot
talk its way past: a step that needs a tool and had no successful tool call is never marked done, and an attempt
that repeats the last one without progress counts as stalled. State is saved after every step, so a restart resumes
at the step that was running; write actions are recorded per mission and replayed, not repeated, on a retry.
If a computer action was in flight at the restart, the resumed step is told to look at the screen before acting.

Steps can depend on earlier steps ("after"): a step whose prerequisite failed or was skipped is not run. With
"review unverified" on, a step that finished without evidence pauses the mission for the user to accept or redo it,
instead of carrying on. Retries per step are configurable (1 to 5). A mission in a project uses the project's
brief, model rules (local only) and budget, and leaves a short episode in the project's memory when it ends.
Before a mission starts, its spending cap is reserved against the monthly limit, so two missions cannot both
plan to use the same remaining budget.
"""

import asyncio
import json
import logging
import time

from fastapi import HTTPException

from . import audit, db, llm, usage

log = logging.getLogger("missions")

MAX_STEPS = 10
MAX_ATTEMPTS = 2
FINAL = ("done", "partial", "failed", "cancelled")
EDITABLE = ("review", "paused", "failed", "partial")
DEFAULT_BUDGET = {"budget_tool_calls": 40, "budget_minutes": 30, "budget_cents": 0}

_wake = asyncio.Event()
_running: set[int] = set()
_tasks: set[asyncio.Task] = set()

PLAN_PROMPT = (
    "You plan missions for a personal AI assistant that has tools for notes, tasks, calendar, email drafts, files, web "
    "search, page reading and memory. Break the user's goal into 2 to {max} concrete steps, in order. Each step must "
    "have a checkable completion criterion that names the evidence (for example 'a note titled X exists' or 'three "
    "sources with URLs are listed'). needs_tool is true when the step must use a tool (search, read, create, update), "
    "false for pure writing or reasoning over earlier results. Do not plan to send email: the assistant can only save "
    'drafts. after lists the numbers (1 based) of earlier steps this step needs; omit it when none. '
    'Reply with only JSON: {{"steps": [{{"title": "...", "done_when": "...", "needs_tool": true, "after": []}}]}}'
)
VERIFY_PROMPT = (
    "You check whether one step of a task was really completed. You get the step, its completion criterion, the "
    "tool calls made (with success or failure and a preview of each result) and the assistant's reply. Judge only from "
    "this evidence; a claim in the reply that no tool result supports does not count. Reply with only JSON: "
    '{"verdict": "done" | "not_done" | "cannot_tell", "evidence": "one sentence citing the tool results", '
    '"missing": "what is still missing, or empty"}'
)
REPORT_PROMPT = (
    "Write the final answer for a completed mission, for the user. Use only the step results given. Start with the "
    "outcome in one or two sentences, then the useful details (Markdown, short). Do not claim anything a step result "
    "does not show. Never say a step succeeded if its status is failed or unverified."
)


# Storage
def _get(mid: int) -> dict:
    m = db.one("SELECT * FROM missions WHERE id = ?", mid)
    if not m:
        raise HTTPException(404, "Mission not found")
    m["plan"] = json.loads(m["plan"] or "[]")
    return m


def _planner(m: dict) -> str | None:
    """The mission's own model, else the Planning role's model, else the default (with its backups)."""
    from .roles import model_for

    return m["model"] or model_for("planning")


def _local(m: dict) -> bool:
    from .projects import local_only

    return local_only(m.get("project_id"))


def _set(mid: int, **fields) -> None:
    if "plan" in fields:
        fields["plan"] = json.dumps(fields["plan"])
    fields["updated_at"] = db.now()
    db.run(f"UPDATE missions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", *fields.values(), mid)


def event(mid: int, kind: str, text: str, step: int | None = None) -> None:
    db.run("INSERT INTO mission_events(mission_id, at, step, kind, text) VALUES (?, ?, ?, ?, ?)", mid, db.now(), step, kind, text[:1000])


def view(mid: int) -> dict:
    m = _get(mid)
    m["events"] = db.all("SELECT at, step, kind, text FROM mission_events WHERE mission_id = ? ORDER BY id DESC LIMIT 200", mid)[::-1]
    from .assistant import approval_view

    m["approvals"] = db.all("SELECT id, tool, args, reason, status, result FROM approvals WHERE conversation_id = ? "
                            "AND status = 'pending' ORDER BY id", m["conversation_id"]) if m["conversation_id"] else []
    m["approvals"] = [approval_view(a) for a in m["approvals"]]
    m["counts"] = {s: sum(1 for st in m["plan"] if st["status"] == s) for s in ("done", "unverified", "failed", "pending", "skipped")}
    return m


def listing(project_id: int | None = None) -> list[dict]:
    where, args = ("WHERE project_id = ?", [project_id]) if project_id else ("", [])
    rows = db.all(f"SELECT id, goal, status, plan, project_id, template, created_at, updated_at FROM missions {where} ORDER BY id DESC LIMIT 100", *args)
    for r in rows:
        plan = json.loads(r.pop("plan") or "[]")
        r["steps"], r["done"] = len(plan), sum(1 for s in plan if s["status"] == "done")
    return rows


# Planning
def _clean_steps(raw) -> list[dict]:
    steps = raw.get("steps") if isinstance(raw, dict) else raw
    out = []
    for s in steps if isinstance(steps, list) else []:
        if isinstance(s, dict) and isinstance(s.get("title"), str) and s["title"].strip():
            n = len(out) + 1
            after = s.get("after") if isinstance(s.get("after"), list) else []
            # only earlier steps can be prerequisites, so a plan can never wait on itself
            after = sorted({int(a) for a in after if isinstance(a, (int, float)) and 1 <= int(a) < n})
            out.append(_new_step(s["title"], str(s.get("done_when") or ""), s.get("needs_tool") is not False, after))
    return out[:MAX_STEPS]


def _new_step(title: str, done_when: str, needs_tool: bool = True, after: list[int] | None = None) -> dict:
    return {"title": title.strip()[:300], "done_when": done_when.strip()[:500] or "The step's result is stated in the reply.",
            "needs_tool": bool(needs_tool), "after": after or [], "status": "pending", "attempts": 0, "result": "", "evidence": "", "tools": []}


def create(goal: str, model: str | None = None, auto_start: bool = False, project_id: int | None = None, steps: list[dict] | None = None,
           template: str = "", max_attempts: int = MAX_ATTEMPTS, review_unverified: bool = False, **budget) -> dict:
    """steps: a predefined plan (from a template); otherwise the model plans. Either way the user reviews it first
    unless auto_start."""
    from . import projects
    from .assistant import new_conversation

    goal = goal.strip()
    if len(goal) < 5:
        raise HTTPException(400, "Describe the mission in a sentence or more.")
    if project_id:
        p = projects.get(project_id)
        if not p:
            raise HTTPException(404, "Project not found")
        if p["status"] != "active":
            raise HTTPException(409, "That project is archived. Restore it to work in it.")
    b = {k: int(budget.get(k) if budget.get(k) is not None else v) for k, v in DEFAULT_BUDGET.items()}
    conv = new_conversation("mission", f"Mission: {goal[:100]}", project_id=project_id)
    now = db.now()
    mid = db.run("INSERT INTO missions(goal, model, status, auto_start, conversation_id, budget_tool_calls, budget_minutes, budget_cents, "
                 "project_id, template, max_attempts, review_unverified, created_at, updated_at) VALUES (?, ?, 'planning', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 goal[:4000], model or "", int(auto_start), conv, b["budget_tool_calls"], b["budget_minutes"], b["budget_cents"],
                 project_id, template[:60], max(1, min(int(max_attempts or MAX_ATTEMPTS), 5)), int(bool(review_unverified)), now, now)
    event(mid, "created", "Mission created. Planning the steps." if steps is None else f"Mission created from the {template or 'given'} plan.")
    audit.record("user", "mission created", f"#{mid}", goal[:200])
    if steps is not None:
        cleaned = _clean_steps({"steps": steps})
        if not cleaned:
            raise HTTPException(400, "A mission needs at least one step.")
        _planned(mid, cleaned)
    else:
        _spawn(plan(mid))
    return view(mid)


def estimate(steps: int) -> float | None:
    """Expected cost from your own past missions (average spend per finished step), or None with no history."""
    rows = db.all("SELECT used_cents, plan FROM missions WHERE status IN ('done', 'partial') AND used_cents > 0 ORDER BY id DESC LIMIT 20")
    n = sum(len(json.loads(r["plan"] or "[]")) for r in rows)
    if not rows or not n:
        return None
    return round(sum(r["used_cents"] for r in rows) / n * steps, 2)


def _planned(mid: int, steps: list[dict]) -> None:
    m = _get(mid)
    est = estimate(len(steps))
    if m["auto_start"]:
        try:
            # Planning does not reserve money until the same transaction
            # that transitions the mission into the active queue.
            with db.tx():
                _set(mid, plan=steps, status="review", estimate_cents=est)
                reserve_check(_get(mid))
                _set(mid, status="queued")
        except HTTPException as e:
            _set(mid, plan=steps, status="review", estimate_cents=est, error=str(e.detail))
            event(mid, "budget", str(e.detail))
            return
        event(mid, "planned", f"Plan ready: {len(steps)} steps.")
        _wake.set()
    else:
        _set(mid, plan=steps, status="review", estimate_cents=est)
        event(mid, "planned", f"Plan ready: {len(steps)} steps. Review it, then start.")


ACTIVE = ("queued", "running", "waiting_approval")


def reserved_cents(exclude: int | None = None) -> float:
    """Spend still promised to active missions: what is left of each one's cap."""
    rows = db.all(f"SELECT id, budget_cents, used_cents FROM missions WHERE status IN ({','.join('?' * len(ACTIVE))})", *ACTIVE)
    return sum(max(0.0, (r["budget_cents"] or 0) - (r["used_cents"] or 0)) for r in rows if r["id"] != exclude)


def reserve_check(m: dict) -> None:
    """Refuse to start when this mission's cap does not fit in what is left of the monthly limit (or the project's
    budget) after other active missions' reservations. Without a cap, the monthly limit still stops it while it runs."""
    from . import projects
    from .usage import month_start

    if problem := projects.budget_problem(m.get("project_id")):
        raise HTTPException(402, problem)
    limit = float(db.get_setting("monthly_limit_cents", "0") or 0)
    need = max(0.0, (m["budget_cents"] or 0) - (m["used_cents"] or 0))
    if limit <= 0 or not need:
        return
    spent = db.one("SELECT COALESCE(SUM(cost_cents), 0) AS c FROM usage WHERE ts >= ?", month_start())["c"]
    others = reserved_cents(exclude=m["id"])
    if spent + others + need > limit:
        raise HTTPException(402, f"Not started: this mission may spend up to {need:.0f} cents, but only {max(0, limit - spent - others):.0f} "
                                 f"of this month's limit ({limit:.0f} cents) is left after {spent:.0f} spent and {others:.0f} reserved by "
                                 "other running missions. Lower its spending cap, wait, or raise the limit in Settings.")


async def plan(mid: int) -> None:
    from .projects import brief

    m = _get(mid)
    steps: list[dict] = []
    context = brief(m["project_id"]) if m.get("project_id") else ""
    try:
        res = await llm.chat(_planner(m), [{"role": "system", "content": PLAN_PROMPT.format(max=MAX_STEPS)},
                                                 {"role": "user", "content": (f"Project context:\n{context}\n\nGoal: " if context else "") + m["goal"]}],
                             local_only=_local(m))
        _spend(mid, usage.record(res, channel="mission", conversation_id=m["conversation_id"]))
        steps = _clean_steps(llm.parse_json(res["message"].get("content") or ""))
    except Exception as e:  # noqa: BLE001 - a planning failure must leave a usable mission, not a stuck one
        event(mid, "warning", f"Planning failed ({str(getattr(e, 'detail', e))[:200]}). Using the goal as a single step.")
    if not steps:
        steps = [_new_step(m["goal"][:300], "The goal is answered with evidence from tool results.")]
        event(mid, "warning", "The model did not return a usable plan, so the mission has one step: the whole goal.")
    _planned(mid, steps)


# Control (called from the API)
def update_plan(mid: int, steps: list[dict]) -> dict:
    m = _get(mid)
    if m["status"] not in EDITABLE:
        raise HTTPException(409, f"The plan cannot be changed while the mission is {m['status']}.")
    cleaned = _clean_steps(steps)
    if not cleaned:
        raise HTTPException(400, "A mission needs at least one step.")
    # Steps already completed keep their results; edited or new steps start fresh
    old = {(s["title"], s["done_when"]): s for s in m["plan"] if s["status"] == "done"}
    plan_ = [old.get((s["title"], s["done_when"]), s) for s in cleaned]
    _set(mid, plan=plan_, current_step=next((i for i, s in enumerate(plan_) if s["status"] != "done"), len(plan_)))
    event(mid, "edited", "Plan edited by you.")
    return view(mid)


def update_budget(mid: int, **budget) -> dict:
    _get(mid)
    fields = {k: max(0, int(v)) for k, v in budget.items() if k in DEFAULT_BUDGET and v is not None}
    if fields:
        _set(mid, **fields)
        event(mid, "edited", "Limits changed: " + ", ".join(f"{k.replace('budget_', '')} {v}" for k, v in fields.items()))
    return view(mid)


def start(mid: int) -> dict:
    # The budget check and transition into the reserved/queued state are one
    # SQLite write transaction. Concurrent mission starts cannot both spend
    # the same unreserved monthly balance.
    with db.tx():
        m = _get(mid)
        if m["status"] == "review_step":
            raise HTTPException(409, "A step is waiting for your review: accept it or redo it.")
        if m["status"] not in ("review", "paused", "failed", "partial"):
            raise HTTPException(409, "Answer the approval request first." if m["status"] == "waiting_approval"
                                else f"Cannot start a mission that is {m['status']}.")
        plan_ = m["plan"]
        for step in plan_:
            if step["status"] in ("failed", "running", "waiting_approval", "skipped") or (m["status"] == "partial" and step["status"] == "unverified"):
                step["status"], step["attempts"] = "pending", 0
        first = next((i for i, step in enumerate(plan_) if step["status"] != "done"), len(plan_))
        reserve_check(m)
        _set(mid, plan=plan_, status="queued", control="", error="", current_step=first)
    event(mid, "started", "Started." if m["status"] == "review" else "Resumed.")
    audit.record("user", "mission started", f"#{mid}")
    _wake.set()
    return view(mid)


def review_step(mid: int, i: int, accept: bool, note: str = "") -> dict:
    """The user's call on a step that finished without evidence: accept it as done, or run it again."""
    m = _get(mid)
    plan_ = m["plan"]
    if not 0 <= i < len(plan_) or plan_[i]["status"] != "unverified":
        raise HTTPException(409, "That step is not waiting for review.")
    s = plan_[i]
    if accept:
        s["status"], s["evidence"] = "done", f"Accepted by you. {note}".strip()[:600]
        s["reviewed"] = "accepted"
    else:
        s["status"], s["attempts"], s["reviewed"] = "pending", 0, "redo"
        s.setdefault("notes", []).append(f"The user checked the last attempt and asked for it to be redone. {note}".strip())
    nxt = next((n for n, st in enumerate(plan_) if st["status"] != "done"), len(plan_))
    _set(mid, plan=plan_, current_step=nxt, error="", status="queued" if m["status"] in ("review_step", "partial") else m["status"])
    event(mid, "review", f"Step {i + 1} {'accepted' if accept else 'sent back to be redone'} by you.", i)
    audit.record("user", "mission step " + ("accepted" if accept else "redo"), f"#{mid} step {i + 1}")
    if m["status"] in ("review_step", "partial"):
        _wake.set()
    return view(mid)


def pause(mid: int) -> dict:
    return _control(mid, "pause")


def cancel(mid: int) -> dict:
    return _control(mid, "cancel")


def _control(mid: int, what: str) -> dict:
    from .assistant import request_stop

    m = _get(mid)
    if m["status"] in FINAL and what == "cancel":
        raise HTTPException(409, f"The mission is already {m['status']}.")
    if m["status"] in ("running",):
        _set(mid, control=what)
        request_stop(m["conversation_id"])  # ends the current turn at its next checkpoint
        event(mid, what, ("Pausing" if what == "pause" else "Cancelling") + " after the current action.")
    elif what == "cancel":
        _finish_cancel(mid)
    elif m["status"] in ("queued", "waiting_approval", "review", "planning", "review_step"):
        _set(mid, status="paused")
        event(mid, "pause", "Paused.")
    audit.record("user", f"mission {what}", f"#{mid}")
    return view(mid)


def _finish_cancel(mid: int) -> None:
    m = _get(mid)
    plan_ = m["plan"]
    for s in plan_:
        if s["status"] in ("pending", "running", "waiting_approval"):
            s["status"] = "skipped"
    db.run("UPDATE approvals SET status = 'denied', result = 'Mission cancelled' WHERE conversation_id = ? AND status = 'pending'",
           m["conversation_id"])
    _set(mid, plan=plan_, status="cancelled", control="")
    event(mid, "cancelled", "Cancelled. Nothing further will run.")


def delete(mid: int) -> None:
    m = _get(mid)
    if m["status"] in ("running", "planning"):
        raise HTTPException(409, "Cancel the mission first.")
    db.run("DELETE FROM mission_events WHERE mission_id = ?", mid)
    db.run("DELETE FROM mission_actions WHERE mission_id = ?", mid)
    db.run("DELETE FROM missions WHERE id = ?", mid)


def on_approval_decided(conversation_id: int) -> None:
    """An approval for a waiting mission was answered: once none are pending, the step runs again (actions approved
    and already done are replayed from the record, not repeated)."""
    m = db.one("SELECT id, status FROM missions WHERE conversation_id = ?", conversation_id)
    if not m or m["status"] != "waiting_approval":
        return
    if not db.one("SELECT 1 FROM approvals WHERE conversation_id = ? AND status IN ('pending', 'running')", conversation_id):
        _set(m["id"], status="queued")
        event(m["id"], "resumed", "Your decision is in. Continuing.")
        _wake.set()


# Idempotency: write actions done in this mission
def replay(mid: int, tool: str, key: str) -> str | None:
    r = db.one("SELECT result FROM mission_actions WHERE mission_id = ? AND tool = ? AND args_key = ? AND ok = 1", mid, tool, key)
    return r["result"] if r else None


def record_action(mid: int, tool: str, key: str, ok: bool, result: str) -> None:
    db.run("INSERT INTO mission_actions(mission_id, tool, args_key, ok, result, at) VALUES (?, ?, ?, ?, ?, ?)",
           mid, tool, key, int(ok), result[:4000], db.now())


# Execution
def _spend(mid: int, cents: float) -> None:
    if cents:
        db.run("UPDATE missions SET used_cents = used_cents + ? WHERE id = ?", cents, mid)


def _over_budget(m: dict) -> str:
    elapsed = (m["elapsed_seconds"] or 0) + (time.time() - m["_run_started"] if m.get("_run_started") else 0)
    if m["budget_tool_calls"] and m["used_tool_calls"] >= m["budget_tool_calls"]:
        return f"the tool call limit ({m['budget_tool_calls']}) was reached"
    if m["budget_minutes"] and elapsed >= m["budget_minutes"] * 60:
        return f"the time limit ({m['budget_minutes']} minutes) was reached"
    if m["budget_cents"] and m["used_cents"] >= m["budget_cents"]:
        return f"the spending limit ({m['budget_cents']} cents) was reached"
    from .projects import budget_problem

    return budget_problem(m.get("project_id"))


def _step_prompt(m: dict, i: int, notes: list[str]) -> str:
    plan_ = m["plan"]
    lines = [f"{n + 1}. [{s['status']}] {s['title']}" + (f" -> {s['result'][:300]}" if s["result"] else "") for n, s in enumerate(plan_)]
    s = plan_[i]
    return "\n".join([
        f"You are carrying out a mission for the user, one step at a time. Mission goal: {m['goal']}",
        "", "Plan so far:", *lines, "",
        f"Now do step {i + 1}: {s['title']}",
        f"It is done when: {s['done_when']}",
        *(["", "Notes from earlier attempts:", *[f"- {n}" for n in notes]] if notes else []),
        "", "Use tools to do the work; never invent results. Do only this step. If something you need is missing or not "
        "allowed, say so plainly instead of guessing. End with a short summary of what you did and the evidence.",
    ])


async def _verify(m: dict, step: dict, reply: str, tools: list[dict]) -> dict:
    log_ = "\n".join(f"- {t['name']}: {'OK' if t['ok'] else 'FAILED'}: {t['preview'][:300]}" for t in tools) or "(no tool calls)"
    try:
        res = await llm.chat(_planner(m), [
            {"role": "system", "content": VERIFY_PROMPT},
            {"role": "user", "content": f"Step: {step['title']}\nDone when: {step['done_when']}\n\nTool calls:\n{log_}\n\nReply:\n{reply[:3000]}"}],
            local_only=_local(m))
        _spend(m["id"], usage.record(res, channel="mission", conversation_id=m["conversation_id"]))
        out = llm.parse_json(res["message"].get("content") or "")
    except Exception as e:  # noqa: BLE001
        return {"verdict": "cannot_tell", "evidence": "", "missing": f"The check itself failed: {str(getattr(e, 'detail', e))[:160]}"}
    if not isinstance(out, dict) or out.get("verdict") not in ("done", "not_done", "cannot_tell"):
        return {"verdict": "cannot_tell", "evidence": "", "missing": "The check gave no clear answer."}
    return {k: str(out.get(k, ""))[:600] for k in ("verdict", "evidence", "missing")}


def judge(step: dict, verdict: dict, tools: list[dict]) -> str:
    """The step's status from the check plus rules the model cannot override. Returns done, unverified or retry."""
    ok_tools = [t for t in tools if t["ok"]]
    if step["needs_tool"] and not ok_tools:
        return "retry" if verdict["verdict"] == "not_done" or tools else "unverified"
    if verdict["verdict"] == "done":
        return "done"
    if verdict["verdict"] == "not_done":
        return "retry"
    return "unverified"


async def run(mid: int) -> None:
    from .assistant import Ctx, run_turn

    m = _get(mid)
    if m["status"] != "queued":
        return
    m["_run_started"] = time.time()
    _set(mid, status="running", heartbeat_at=db.now(), started_at=m["started_at"] or db.now())
    plan_ = m["plan"]
    i = m["current_step"]
    outcome = ""
    try:
        while i < len(plan_):
            m.update(_get(mid), _run_started=m["_run_started"])
            plan_ = m["plan"]
            if m["control"]:
                outcome = m["control"]
                break
            if reason := _over_budget(m):
                _set(mid, status="paused", error=f"Paused: {reason}. Raise the limit and resume to continue.")
                event(mid, "budget", f"Paused: {reason}.", i)
                return
            step = plan_[i]
            if step["status"] == "done":
                i += 1
                continue
            blocked = [a for a in step.get("after", []) if 1 <= a <= len(plan_) and plan_[a - 1]["status"] in ("failed", "skipped")]
            if blocked:
                step["status"], step["evidence"] = "skipped", f"Not run: it needs step {', '.join(map(str, blocked))}, which did not complete."
                _set(mid, plan=_merge(plan_, i, step), current_step=i + 1)
                event(mid, "skipped", step["evidence"], i)
                i += 1
                continue
            step["status"], step["attempts"] = "running", step["attempts"] + 1
            step.setdefault("started_at", db.now())
            _set(mid, plan=plan_, current_step=i)
            event(mid, "step", f"Step {i + 1}: {step['title']}" + (f" (attempt {step['attempts']})" if step["attempts"] > 1 else ""), i)

            ctx = Ctx(channel="mission", conversation_id=m["conversation_id"], mission_id=mid)
            tools: list[dict] = []
            approvals: list[int] = []
            reply, error, stopped, cost = "", "", False, 0.0
            async for e in run_turn(ctx, _step_prompt(m, i, step.get("notes", [])), m["model"] or None):
                t = e["type"]
                if t == "tool_call":
                    db.run("UPDATE missions SET used_tool_calls = used_tool_calls + 1, heartbeat_at = ? WHERE id = ?", db.now(), mid)
                elif t == "tool_result":
                    tools.append({"name": e["name"], "ok": e["ok"], "preview": e["preview"], "ms": e.get("ms", 0)})
                    event(mid, "tool", f"{e['name']}: {'ok' if e['ok'] else 'failed'}. {e['preview'][:200]}", i)
                elif t == "approval":
                    approvals.append(e["id"])
                    event(mid, "approval", f"Waiting for your approval: {e['name']} {e['args'][:200]}", i)
                elif t == "message":
                    reply = e["content"]
                elif t == "error":
                    error = e["error"]
                elif t == "done":
                    stopped, cost = e["stopped"], e["cost_cents"]
            _spend(mid, cost)
            step["tools"] = (step.get("tools") or []) + tools
            m.update(_get(mid), _run_started=m["_run_started"])

            if m["control"] or stopped:
                step["status"], step["attempts"] = "pending", step["attempts"] - 1  # an interrupted attempt does not count
                _set(mid, plan=_merge(m["plan"], i, step))
                outcome = m["control"] or "pause"
                break
            if approvals:
                step["status"] = "waiting_approval"
                step["attempts"] -= 1  # waiting is not a failed attempt
                step.setdefault("notes", []).append("Some actions needed the user's approval; see what was approved or denied before acting again.")
                _set(mid, plan=_merge(m["plan"], i, step), status="waiting_approval")
                event(mid, "waiting", "Waiting for your approval before this step can finish.", i)
                on_approval_decided(m["conversation_id"])  # in case they were all decided already
                return
            if error:
                step["result"] = f"Error: {error}"
                verdict = {"verdict": "not_done", "evidence": "", "missing": error}
            else:
                step["result"] = reply[:2000]
                verdict = await _verify(m, step, reply, tools)
            status = judge(step, verdict, tools)
            step["evidence"] = verdict["evidence"] or verdict["missing"]
            prev = step.get("last_reply", "")
            step["last_reply"] = reply[:500]
            stalled = status == "retry" and prev and _similar(prev, reply) and not any(t["ok"] for t in tools)
            if "kept repeating the same step" in reply:
                stalled = True
            if status == "retry" and (stalled or step["attempts"] >= (m.get("max_attempts") or MAX_ATTEMPTS)):
                step["status"] = "failed"
                why = "it stopped making progress" if stalled else f"it was not completed after {step['attempts']} attempts"
                step["evidence"] = f"Failed: {why}. {verdict['missing']}".strip()
                _set(mid, plan=_merge(m["plan"], i, step), error=f"Step {i + 1} failed: {why}.")
                event(mid, "failed", f"Step {i + 1} failed: {why}. {verdict['missing'][:200]}", i)
                await _report(mid, "failed")
                return
            if status == "retry":
                step["status"] = "pending"
                step.setdefault("notes", []).append(f"Attempt {step['attempts']} was not complete: {verdict['missing'] or 'no evidence of completion'}")
                _set(mid, plan=_merge(m["plan"], i, step))
                event(mid, "retry", f"Step {i + 1} not complete yet ({verdict['missing'][:160] or 'no evidence'}). Trying again.", i)
                continue
            step["status"] = status
            step["finished_at"] = db.now()
            if status == "unverified" and m.get("review_unverified"):
                _set(mid, plan=_merge(m["plan"], i, step), current_step=i, status="review_step",
                     error=f"Step {i + 1} finished but could not be verified. Check it, then accept it or have it redone.")
                event(mid, "review", f"Step {i + 1} needs your review: {step['evidence'][:200]}", i)
                return
            _set(mid, plan=_merge(m["plan"], i, step), current_step=i + 1, heartbeat_at=db.now())
            event(mid, status, f"Step {i + 1} {'done' if status == 'done' else 'finished but not verified'}: {step['evidence'][:200]}", i)
            i += 1
        else:
            plan_ = _get(mid)["plan"]
            final = "done" if all(s["status"] == "done" for s in plan_) else "partial"
            if final == "partial" and any(s["status"] == "failed" for s in plan_):
                final = "failed"
            _set(mid, current_step=len(plan_))
            await _report(mid, final)
            event(mid, final, "Mission complete." if final == "done" else "Mission finished, but some steps could not be verified.")
            return
        if outcome == "cancel":
            _finish_cancel(mid)
        elif outcome == "pause":
            _set(mid, status="paused", control="")
            event(mid, "pause", "Paused. Resume when you are ready.")
    except Exception as e:  # noqa: BLE001 - a crash must leave a visible, resumable state
        log.exception("mission %s crashed", mid)
        _set(mid, status="failed", error=f"Stopped by an internal error: {str(e)[:300]}. You can resume it.")
        event(mid, "failed", f"Internal error: {str(e)[:300]}")
    finally:
        started = m.get("_run_started")
        if started:
            db.run("UPDATE missions SET elapsed_seconds = elapsed_seconds + ? WHERE id = ?", time.time() - started, mid)


def _merge(plan_: list[dict], i: int, step: dict) -> list[dict]:
    plan_ = list(plan_)
    plan_[i] = step
    return plan_


def _similar(a: str, b: str) -> bool:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) > 0.85


async def _report(mid: int, final: str) -> None:
    """Write the report, then set the final status in the same update, so a finished mission always has its report."""
    m = _get(mid)
    lines = [f"Step {n + 1} [{s['status']}]: {s['title']}\nResult: {s['result'][:1200]}\nEvidence: {s['evidence'][:300]}"
             for n, s in enumerate(m["plan"])]
    summary = ""
    try:
        res = await llm.chat(_planner(m), [{"role": "system", "content": REPORT_PROMPT},
                                                 {"role": "user", "content": f"Mission goal: {m['goal']}\n\n" + "\n\n".join(lines)}],
                             local_only=_local(m))
        _spend(mid, usage.record(res, channel="mission", conversation_id=m["conversation_id"]))
        summary = (res["message"].get("content") or "").strip()
    except Exception as e:  # noqa: BLE001
        summary = f"(The written summary could not be produced: {str(getattr(e, 'detail', e))[:200]})"
    marks = {"done": "Done", "unverified": "Not verified", "failed": "Failed", "skipped": "Not run", "pending": "Not run"}
    table = "\n".join(f"- **{marks.get(s['status'], s['status'])}**: {s['title']}" + (f" ({s['evidence'][:200]})" if s["evidence"] else "")
                      for s in m["plan"])
    _set(mid, report=f"{summary}\n\n### Steps\n{table}", status=final)
    if m.get("project_id"):
        # Only verified machine-generated status is written into durable memory.
        # Model-generated reports may quote webpages or other untrusted content,
        # so they remain in the mission report, never injected into project prompts.

        from .sensitive import redact

        done = sum(1 for s in m["plan"] if s["status"] == "done")
        db.run("INSERT INTO memories(content, kind, source, confidence, conversation_id, project_id, created_at, updated_at) "
               "VALUES (?, 'episode', 'mission', 0.9, ?, ?, ?, ?)", redact(f"Mission #{mid} ({final}, {done}/{len(m['plan'])} steps done): "
               f"{m['goal'][:160]}. Review mission #{mid} to inspect its report."), m["conversation_id"], m["project_id"], db.now(), db.now())
    from .scheduler import deliver

    deliver(f"Mission {final}: {m['goal'][:80]}", f"{summary}\n\n{table}", "inbox", f"mission:{mid}")


# Worker
def _spawn(coro) -> None:
    try:
        t = asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


def recover() -> None:
    """After a restart: a running mission resumes at its current step; one still planning is planned again."""
    for m in db.all("SELECT id, status FROM missions WHERE status IN ('running', 'planning')"):
        if m["status"] == "running":
            plan_ = _get(m["id"])["plan"]
            in_flight = db.one("SELECT id, kind FROM computer_actions WHERE mission_id = ? AND status IN ('dispatched', 'uncertain') "
                               "ORDER BY id DESC LIMIT 1", m["id"])
            for s in plan_:
                if s["status"] == "running":
                    s["status"], s["attempts"] = "pending", max(0, s["attempts"] - 1)
                    if in_flight:
                        s.setdefault("notes", []).append(
                            f"ROOKS restarted while a computer action ({in_flight['kind']}, #{in_flight['id']}) was in progress; it may or "
                            "may not have happened. Observe the screen first and check the current state before doing anything again.")
            _set(m["id"], plan=plan_, status="queued", control="")
            event(m["id"], "recovered", "ROOKS restarted during this mission. Resuming at the step that was running."
                  + (" A computer action was in progress, so the step will look at the screen before acting." if in_flight else ""))
        else:
            _spawn(plan(m["id"]))


async def loop() -> None:
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), 5)
        except asyncio.TimeoutError:
            pass
        _wake.clear()
        try:
            for r in db.all("SELECT id FROM missions WHERE status = 'queued' ORDER BY id"):
                if r["id"] not in _running:
                    _running.add(r["id"])
                    try:
                        await run(r["id"])
                    finally:
                        _running.discard(r["id"])
        except Exception as e:  # noqa: BLE001 - keep the worker alive
            log.warning("mission worker error: %s", e)
