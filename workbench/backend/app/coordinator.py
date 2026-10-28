"""The workflow coordinator: work the assistant starts in a conversation reports back into that conversation.

Research, missions and coding tasks already run on their own engines (research.py, missions.py, the companion). The
coordinator only remembers which conversation asked for each one (a job) and, when the work reaches a stage you need to
see, posts one message there: the research report, a mission plan waiting for your approval, a mission's result, a
coding task's result. It never starts, approves or spends anything itself. Approving a plan or stopping work from the
conversation calls the same endpoints, with the same checks, as the Missions and Research pages.

A message is never posted into a conversation while a turn is running in it, so a delivery cannot land between a tool
call and its result.
"""

import asyncio
import json

from . import db

MAX_REPORT = 24_000  # characters of a research report posted into the conversation; the full report stays on its page
MISSION_FINAL = ("done", "partial", "failed", "cancelled")
RESEARCH_FINAL = ("done", "error", "cancelled")
_wake = asyncio.Event()


def register(conversation_id: int | None, kind: str, ref_id: int, title: str = "") -> int | None:
    """Remember that this conversation started this work. Unattended channels (missions, schedules) have no one to
    report to, so they get no job."""
    if not conversation_id:
        return None
    conv = db.one("SELECT channel FROM conversations WHERE id = ?", conversation_id)
    if not conv or conv["channel"] not in ("web", "cli", "telegram"):
        return None
    jid = db.run("INSERT INTO jobs(conversation_id, kind, ref_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                 conversation_id, kind, ref_id, (title or "")[:300], db.now(), db.now())
    return jid


def wake() -> None:
    _wake.set()


# What each kind of work looks like now
def _research(rid: int) -> dict | None:
    return db.one("SELECT id, question, status, progress, report, error, sources, project_id FROM research WHERE id = ?", rid)


def _mission(mid: int) -> dict | None:
    m = db.one("SELECT id, goal, status, plan, report, error, project_id, estimate_cents, budget_cents FROM missions WHERE id = ?", mid)
    if m:
        m["plan"] = json.loads(m["plan"] or "[]")
    return m


def _coding(aid: int) -> dict | None:
    from . import coding

    return coding.view(aid)


def state(job: dict) -> dict:
    """The job's current state as a block for the conversation: what it is, where it is, what you can do."""
    kind, ref = job["kind"], job["ref_id"]
    b: dict = {"type": "job", "kind": kind, "id": ref, "job_id": job["id"], "title": job["title"][:300]}
    if kind == "research":
        r = _research(ref)
        if not r:
            return {**b, "status": "deleted", "live": False}
        b.update(title=r["question"][:300], status=r["status"], progress=(r["progress"] or "")[:200],
                 sources=len(json.loads(r["sources"] or "[]")), error=(r["error"] or "")[:300],
                 live=r["status"] not in RESEARCH_FINAL, project_id=r["project_id"])
    elif kind == "mission":
        m = _mission(ref)
        if not m:
            return {**b, "status": "deleted", "live": False}
        b.update(title=m["goal"][:300], status=m["status"], error=(m["error"] or "")[:300], project_id=m["project_id"],
                 steps=[{"title": s["title"][:200], "status": s["status"]} for s in m["plan"]][:20],
                 estimate_cents=m["estimate_cents"], budget_cents=m["budget_cents"] or None, live=m["status"] not in MISSION_FINAL)
    elif kind == "coding":
        c = _coding(ref)
        if not c:
            return {**b, "status": "deleted", "live": False}
        b.update(title=c["task"][:300], status=c["status"], agent=c["agent"], folder=c["folder"], progress=c["progress"][-6:],
                 billing=c["billing"], error=c["error"][:300], live=c["status"] not in ("done", "failed", "cancelled"))
    if b.get("status") and b["status"] != "deleted":  # the same words every client shows (work.py)
        from . import work

        try:
            phase, outcome = work.phase_of({"mission": "mission", "research": "research", "coding": "coding"}[kind], b["status"])
            b.update(phase=str(phase), status_text=work.OUTCOME_WORDS.get(outcome) or work.WORDS[phase], work_id=f"{kind}:{ref}")
        except (KeyError, ValueError):
            pass
    return b


def _due(job: dict) -> tuple[str, str] | None:
    """(stage, message) when the job has reached a stage not yet delivered, else None."""
    kind, ref, stage = job["kind"], job["ref_id"], job["stage"]
    if kind == "research":
        r = _research(ref)
        if not r or r["status"] not in RESEARCH_FINAL:
            return None
        if r["status"] == "done":
            report = r["report"] or ""
            if len(report) > MAX_REPORT:
                report = report[:MAX_REPORT] + "\n\n… The full report is on the research page."
            return "final", f"**Research finished:** {r['question'][:200]}\n\n{report}"
        if r["status"] == "cancelled":
            return "final", f"Research stopped before it finished: {r['question'][:200]}"
        return "final", f"Research could not finish: {r['question'][:200]}\n\n{(r['error'] or 'No reason was recorded.')[:600]}"
    if kind == "mission":
        m = _mission(ref)
        if not m:
            return None
        if m["status"] in MISSION_FINAL:
            if m["status"] == "cancelled":
                return "final", f"Mission stopped: {m['goal'][:200]}"
            head = {"done": "Mission finished", "partial": "Mission partly finished", "failed": "Mission failed"}[m["status"]]
            return "final", f"**{head}:** {m['goal'][:200]}\n\n{(m['report'] or m['error'] or '')[:MAX_REPORT]}"
        if stage == "" and m["status"] == "review":
            lines = "\n".join(f"{i}. {s['title']}" for i, s in enumerate(m["plan"], 1))
            cost = (f" Estimated cost up to {m['estimate_cents']:.0f} cents." if m.get("estimate_cents") else "")
            return "plan", (f"**The plan is ready:** {m['goal'][:200]}\n\n{lines}\n\nNothing has run yet.{cost} "
                            "Approve it to start, or open the mission to change the steps or limits.")
        return None
    if kind == "coding":
        c = _coding(ref)
        if not c or c["status"] not in ("done", "failed", "cancelled"):
            return None
        if c["status"] == "done":
            cost = f"\n\n{c['cost']} (billed by the agent's provider, not counted in ROOKS's spending)." if c.get("cost") else ""
            return "final", f"**{c['agent']} finished:** {c['task'][:200]}\n\n{c['result'][:MAX_REPORT] or '(No final message.)'}{cost}"
        if c["status"] == "cancelled":
            return "final", f"The coding task was stopped: {c['task'][:200]}"
        return "final", f"The coding task did not finish: {c['task'][:200]}\n\n{c['error'][:800]}"
    return None


def deliver_due() -> int:
    """Post every message that is due. Returns how many were posted."""
    from .assistant import busy

    n = 0
    for job in db.all("SELECT * FROM jobs WHERE stage != 'final' ORDER BY id"):
        if busy(job["conversation_id"]):
            continue  # wait for the turn to end
        due = _due(job)
        if not due:
            continue
        stage, text = due
        with db.tx():
            # claimed in the same transaction as the post, so two loops cannot deliver it twice
            if not db.changed("UPDATE jobs SET stage = ?, updated_at = ? WHERE id = ? AND stage = ?", stage, db.now(), job["id"], job["stage"]):
                continue
            db.run("INSERT INTO messages(conversation_id, role, content, name, job_id, created_at) VALUES (?, 'assistant', ?, 'rooks_update', ?, ?)",
                   job["conversation_id"], text, job["id"], db.now())
            db.run("UPDATE conversations SET updated_at = ? WHERE id = ?", db.now(), job["conversation_id"])
        n += 1
    return n


def for_conversation(cid: int) -> dict:
    """Job blocks for a conversation: message id -> blocks for delivered messages, plus the jobs still open."""
    jobs = {j["id"]: j for j in db.all("SELECT * FROM jobs WHERE conversation_id = ? ORDER BY id", cid)}
    by_msg = {r["id"]: [state(jobs[r["job_id"]])] for r in
              db.all("SELECT id, job_id FROM messages WHERE conversation_id = ? AND job_id IS NOT NULL", cid) if r["job_id"] in jobs}
    open_ = [state(j) for j in jobs.values() if j["stage"] != "final"]
    return {"messages": by_msg, "open": [b for b in open_ if b.get("status") != "deleted"]}


def open_jobs(cid: int) -> list[dict]:
    return for_conversation(cid)["open"]


async def loop() -> None:
    while True:
        try:
            await asyncio.wait_for(_wake.wait(), 3)
        except asyncio.TimeoutError:
            pass
        _wake.clear()
        try:
            deliver_due()
        except Exception as e:  # noqa: BLE001 - one bad job must not stop delivery for the others
            print(f"coordinator: {e}")
