"""Job understanding and proactive suggestions.

Every piece of work is logged as activity (chat turns, playbook runs, files read, sites checked) with its kind and the
local weekday and hour. Kinds come from a small keyword taxonomy that spans fields (trades, office, law, science,
study, studios); anything else is 'other' and is still grouped by playbook. When the same kind recurs on a pattern
(same weekday in 2+ different weeks, or 3+ different days within a fortnight) a suggestion is offered once:
"I noticed you do X every Monday; want me to do it for you?". Accepting creates a routine in one click. Routines are
unattended, so hooks.guard refuses anything that would send or change data outside the workspace: they prepare drafts
and reports, and the person approves the rest.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from .. import db
from . import knowledge, store

# kind: (label, what a routine does, keywords). Order matters: the first kind with the most hits wins.
KINDS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "weekly_report": ("weekly report", "Prepare this week's report as a draft for you to check",
                      ("weekly report", "status report", "progress report", "report", "summary of the week", "wrap up")),
    "invoice_chasing": ("invoice chasing", "List unpaid and overdue invoices and draft polite reminders (not sent)",
                        ("invoice", "overdue", "unpaid", "payment reminder", "accounts receivable", "chase")),
    "quotes": ("quotes and estimates", "Draft quotes from new enquiries for you to check",
               ("quote", "estimate", "pricing", "tender", "bid")),
    "site_checks": ("site checks", "Check your work sites and portals and tell you what changed",
                    ("site check", "portal", "job site", "project portal", "servicem8", "procore", "check site", "inspection")),
    "timesheets": ("timesheets", "Fill in your timesheet as a draft for you to submit",
                   ("timesheet", "hours worked", "clock in", "roster", "shift")),
    "inbox_triage": ("inbox triage", "Sort your inbox and draft replies to what needs you (not sent)",
                     ("inbox", "email", "reply to", "unread", "mail")),
    "scheduling": ("scheduling", "Plan your week from your calendar and tasks",
                   ("schedule", "calendar", "meeting", "appointment", "book a", "plan my week", "availability")),
    "bookkeeping": ("bookkeeping", "Reconcile this week's transactions and flag anything odd",
                    ("expense", "receipt", "reconcile", "bookkeeping", "xero", "myob", "quickbooks", "bas", "gst")),
    "legal_review": ("legal review", "Review new contracts or documents and list the clauses that matter",
                     ("contract", "clause", "lease", "legislation", "agreement", "terms and conditions", "compliance")),
    "research": ("research", "Gather new sources on your topic and summarise them with links",
                 ("research", "papers", "literature", "sources", "study on", "journal")),
    "data_analysis": ("data analysis", "Refresh the analysis on your latest data and chart what changed",
                      ("spreadsheet", "excel", "csv", "dataset", "analyse", "analyze", "chart", "pivot", "statistics")),
    "study": ("study", "Make a revision plan and practice questions for what is coming up",
              ("assignment", "exam", "homework", "lecture", "revision", "study", "course", "uni")),
    "maths": ("maths", "Work through the next problem set step by step",
              ("equation", "integral", "derivative", "proof", "algebra", "calculus", "matrix", "maths", "math")),
    "coding": ("coding", "Check your repositories and summarise what needs attention",
               ("code", "bug", "pull request", "repository", "python", "javascript", "function", "deploy")),
    "creative": ("studio work", "Gather this week's briefs and draft a production plan",
                 ("mix", "track", "render", "design", "brief", "studio", "shoot", "edit video", "autocad", "drawing")),
    "social_posts": ("social posts", "Draft this week's posts for you to approve",
                     ("post on", "instagram", "linkedin post", "facebook", "tweet", "newsletter")),
}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def classify(text: str) -> str:
    t = " " + (text or "").lower() + " "
    best, hits = "other", 0
    for kind, (_, _, kws) in KINDS.items():
        n = sum(1 for k in kws if k in t)
        if n > hits:
            best, hits = kind, n
    return best


def label(kind: str) -> str:
    return KINDS[kind][0] if kind in KINDS else "this task"


def _tz() -> ZoneInfo:
    from ..assistant import timezone_name

    try:
        return ZoneInfo(timezone_name())
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def observe(kind: str | None, source: str, summary: str = "", ref: str = "", when: datetime | None = None) -> int:
    """Log one piece of work. kind=None classifies the summary."""
    store.ensure()
    if not store.flag("adapt_jobs", True):
        return 0
    kind = kind or classify(summary)
    utc = when or datetime.now(timezone.utc)
    local = utc.astimezone(_tz())
    return db.run("INSERT INTO adapt_activity(kind, source, ref, summary_enc, local_weekday, local_hour, local_day, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", kind, source, ref, store.seal(summary[:400]), local.weekday(), local.hour,
                  local.date().isoformat(), utc.isoformat(timespec="seconds"))


def profile() -> list[dict]:
    """What kinds of work this person does, most frequent first (for the 'Your work' summary)."""
    store.ensure()
    rows = db.all("SELECT kind, COUNT(*) AS n, COUNT(DISTINCT local_day) AS days, MAX(created_at) AS last FROM adapt_activity "
                  "GROUP BY kind ORDER BY n DESC")
    return [{"kind": r["kind"], "label": label(r["kind"]) if r["kind"] != "other" else "other work", "times": r["n"],
             "days": r["days"], "last": r["last"]} for r in rows]


def _pattern(rows: list[dict]) -> tuple[str, str, str] | None:
    """(key part, plain English when, cron) for a recurring pattern in these activity rows, if any."""
    by_day = defaultdict(set)
    hours = defaultdict(list)
    for r in rows:
        week = datetime.fromisoformat(r["local_day"]).isocalendar()[:2]
        by_day[r["local_weekday"]].add(week)
        hours[r["local_weekday"]].append(r["local_hour"])
    days = sorted({r["local_day"] for r in rows})
    for wd, weeks in sorted(by_day.items(), key=lambda kv: -len(kv[1])):
        if len(weeks) >= 2:
            h = sorted(hours[wd])[len(hours[wd]) // 2]
            return f"weekly-{wd}", f"every {DAYS[wd]} around {_hour(h)}", f"0 {h} * * {(wd + 1) % 7}"
    recent = [d for d in days if d >= (datetime.now(timezone.utc) - timedelta(days=14)).date().isoformat()]
    weekdays = {datetime.fromisoformat(d).weekday() for d in recent}
    if len(recent) >= 3 and weekdays <= set(range(5)):
        h = sorted(r["local_hour"] for r in rows)[len(rows) // 2]
        return "weekdays", f"most weekdays around {_hour(h)}", f"0 {h} * * 1-5"
    if len(recent) >= 3:
        h = sorted(r["local_hour"] for r in rows)[len(rows) // 2]
        return "daily", f"most days around {_hour(h)}", f"0 {h} * * *"
    return None


def _hour(h: int) -> str:
    return f"{h % 12 or 12}{'am' if h < 12 else 'pm'}"


def refresh_suggestions() -> list[dict]:
    """Look for new recurring patterns and add suggestions (each offered once)."""
    store.ensure()
    if not store.flag("adapt_suggest", True):
        return []
    since = (datetime.now(timezone.utc) - timedelta(days=42)).isoformat(timespec="seconds")
    rows = db.all("SELECT * FROM adapt_activity WHERE created_at >= ? ORDER BY created_at", since)
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r["ref"].startswith("playbook:"):
            groups[("playbook", r["ref"])].append(r)
        elif r["kind"] != "other":
            groups[("kind", r["kind"])].append(r)
    made = []
    for (gtype, gid), items in groups.items():
        p = _pattern(items)
        if not p:
            continue
        part, when, cron = p
        key = f"{gid}:{part}"
        if db.one("SELECT 1 FROM adapt_suggestions WHERE key = ?", key):
            continue
        playbook_id = None
        if gtype == "playbook":
            playbook_id = int(gid.split(":")[1])
            pb = db.one("SELECT name FROM adapt_playbooks WHERE id = ?", playbook_id)
            if not pb:
                continue
            title = f"I noticed you run \u201c{pb['name']}\u201d {when}. Want me to do it for you?"
            detail = "I'll run it on that schedule and stop before anything is sent or submitted, so you can check it first."
            prompt = ""
            kind = items[-1]["kind"]
        else:
            kind = gid
            title = f"I noticed you do this {when}: {label(kind)}. Want me to take it on?"
            detail = KINDS[kind][1] + ". Nothing is sent or changed outside the app without your OK."
            examples = [store.open_text(i["summary_enc"]) for i in items[-3:]]
            prompt = (f"{KINDS[kind][1]}. This is a routine the user accepted after doing {label(kind)} themselves; "
                      "here is what they asked for before, so do it the same way:\n" + "\n".join(f"- {e}" for e in examples if e) +
                      "\nPrepare everything as notes, drafts or a report. Do not send, submit, book or delete anything; "
                      "list what needs the user's OK at the end.")
        sid = db.run("INSERT INTO adapt_suggestions(key, kind, title, detail, cron, prompt, playbook_id, created_at, updated_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", key, kind, title, detail, cron, prompt, playbook_id, db.now(), db.now())
        made.append(db.one("SELECT * FROM adapt_suggestions WHERE id = ?", sid))
    return made


def suggestions(status: str = "open") -> list[dict]:
    store.ensure()
    return db.all("SELECT * FROM adapt_suggestions WHERE status = ? ORDER BY id DESC", status)


def accept(sid: int, cron: str | None = None) -> dict:
    from .. import scheduler

    store.ensure()
    s = db.one("SELECT * FROM adapt_suggestions WHERE id = ?", sid)
    if not s:
        raise HTTPException(404, "Not found")
    if s["status"] == "accepted":
        return s
    if s["playbook_id"]:
        pb = db.one("SELECT name FROM adapt_playbooks WHERE id = ?", s["playbook_id"])
        prompt = (f"Run the playbook {s['playbook_id']} (\u201c{pb['name'] if pb else 'playbook'}\u201d) with run_playbook, "
                  "using the values from its last run. Report what it found and anything waiting for the user's OK.")
        name = pb["name"] if pb else "Playbook"
    else:
        prompt, name = s["prompt"], label(s["kind"]).capitalize()
    sched = scheduler.create_schedule(name, cron or s["cron"], prompt)
    db.run("INSERT OR REPLACE INTO adapt_routines(schedule_id, suggestion_id, playbook_id, created_at) VALUES (?, ?, ?, ?)",
           sched["id"], sid, s["playbook_id"], db.now())
    db.run("UPDATE adapt_suggestions SET status = 'accepted', schedule_id = ?, updated_at = ? WHERE id = ?", sched["id"], db.now(), sid)
    if s["playbook_id"]:
        db.run("UPDATE adapt_playbooks SET schedule_id = ? WHERE id = ?", sched["id"], s["playbook_id"])
    knowledge.add(f"Asked the assistant to take over {label(s['kind'])} on a schedule", "work", "task")
    return db.one("SELECT * FROM adapt_suggestions WHERE id = ?", sid) | {"schedule": sched}


def dismiss(sid: int) -> dict:
    store.ensure()
    db.run("UPDATE adapt_suggestions SET status = 'dismissed', updated_at = ? WHERE id = ?", db.now(), sid)
    return {"dismissed": sid}


def is_learned_routine(schedule_id: int) -> bool:
    store.ensure()
    return bool(db.one("SELECT 1 FROM adapt_routines WHERE schedule_id = ?", schedule_id))
