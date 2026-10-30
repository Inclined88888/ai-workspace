"""Receipts and the reliability record.

A receipt is a check, made against the database after a tool runs, that the change the model asked for really exists.
A claim check compares the final answer against those receipts: an answer that says something was created, saved or
sent when no verified change backs it is caught, retried once, and flagged if it still is not true.
Both are recorded per model, so how reliable each model is with your tools is measured, not assumed.
"""

import re
import time
from typing import Any, Callable

from . import db

Check = tuple[bool | None, str]  # (verified, evidence); None = nothing to check or not checkable


def ref_of(name: str, result: Any) -> Any:
    """The id of the row a tool changed, so later checks can read what was really saved."""
    if name == "create_task":
        return (result or {}).get("id")
    if name == "update_task":
        return (result or {}).get("id")
    return None


def _task(args: dict, r: Any) -> Check:
    t = db.one("SELECT * FROM tasks WHERE id = ?", (r or {}).get("id"))
    if not t:
        return False, "no task was saved"
    if args.get("due") and t["due"] != str(args["due"])[:10]:
        return False, f"task #{t['id']} was saved with due {t['due']}, not {args['due']}"
    return True, (f"task #{t['id']} \"{t['title']}\" saved" + (f", due {t['due']}" if t["due"] else "") +
                  f", priority {PRIORITY_NAME.get(t['priority'], t['priority'])}")


def _task_update(args: dict, r: Any) -> Check:
    t = db.one("SELECT * FROM tasks WHERE id = ?", args.get("task_id"))
    if not t:
        return False, f"task #{args.get('task_id')} does not exist"
    for field in ("status", "title"):
        if args.get(field) and t[field] != args[field]:
            return False, f"task #{t['id']} {field} is {t[field]!r}, not {args[field]!r}"
    return True, f"task #{t['id']} \"{t['title']}\" is now {t['status']}"


def _note(args: dict, r: Any) -> Check:
    d = db.one("SELECT id, title, kind FROM docs WHERE id = ?", (r or {}).get("id"))
    return (True, f"{d['kind']} #{d['id']} \"{d['title']}\" saved") if d else (False, "no note was saved")


def _append(args: dict, r: Any) -> Check:
    d = db.one("SELECT id, title, body FROM docs WHERE id = ?", args.get("doc_id"))
    if not d or str(args.get("text", "")).strip() not in d["body"]:
        return False, f"the text is not in note #{args.get('doc_id')}"
    return True, f"text added to \"{d['title']}\""


def _event(args: dict, r: Any) -> Check:
    e = db.one("SELECT e.*, c.name AS cal, c.kind FROM events e JOIN calendars c ON c.id = e.calendar_id WHERE e.id = ?",
               (r or {}).get("id"))
    if not e:
        return False, "no event was saved"
    where = f"on {e['cal']} (CalDAV server accepted it)" if e["kind"] == "caldav" else f"in {e['cal']}"
    return True, f"event \"{e['title']}\" {e['start']} saved {where}"


def _draft(args: dict, r: Any) -> Check:
    d = db.one("SELECT * FROM mail_drafts WHERE id = ?", (r or {}).get("draft_id"))
    return (True, f"draft #{d['id']} to {d['to_addr']} saved, not sent") if d else (False, "no draft was saved")


def _memory(args: dict, r: Any) -> Check:
    if (r or {}).get("saved") is None and (r or {}).get("note"):
        return True, "already known, nothing new saved"
    m = db.one("SELECT * FROM adapt_facts WHERE id = ?", (r or {}).get("saved"))
    return (True, f"memory #{m['id']} saved (encrypted)") if m else (False, "no memory was saved")


def _forget(args: dict, r: Any) -> Check:
    gone = not db.one("SELECT id FROM adapt_facts WHERE id = ?", args.get("memory_id"))
    return (True, f"memory #{args.get('memory_id')} deleted") if gone else (False, "the memory still exists")


def _skill(args: dict, r: Any) -> Check:
    name = next(iter((r or {}).values()), "")
    s = db.one("SELECT name FROM skills WHERE name = ?", name)
    return (True, f"skill {name} saved") if s else (False, "no skill was saved")


def _schedule(args: dict, r: Any) -> Check:
    s = db.one("SELECT * FROM schedules WHERE id = ?", (r or {}).get("id"))
    if not s or not s["enabled"]:
        return False, "no active schedule was saved"
    return True, f"schedule \"{s['name']}\" ({s['cron']}) saved, next run {s['next_run']} UTC"


def _delegated(args: dict, r: Any) -> Check:
    t = db.one("SELECT id, status FROM agent_tasks WHERE id = ?", (r or {}).get("task_id"))
    return (True, f"task #{t['id']} assigned, {t['status']}") if t else (False, "no task was assigned")


def _page(args: dict, r: Any) -> Check:
    n = len((r or {}).get("text") or "")
    return (True, f"read {n} characters from {(r or {}).get('url')}") if n else (False, "the page had no readable text")


def _browse(args: dict, r: Any) -> Check:
    return None, "the browser agent's own report; not independently checked"


VERIFIERS: dict[str, Callable[[dict, Any], Check]] = {
    "create_task": _task, "update_task": _task_update, "create_note": _note, "append_to_note": _append,
    "create_event": _event, "save_email_draft": _draft, "remember": _memory, "forget": _forget, "save_skill": _skill,
    "schedule_task": _schedule, "delegate_task": _delegated, "read_page": _page, "browse_web": _browse,
}


def verify(name: str, args: dict, result: Any) -> Check:
    fn = VERIFIERS.get(name)
    if not fn:
        return None, ""
    try:
        return fn(args, result)
    except Exception as e:  # noqa: BLE001 - a broken check is a failed check, never a pass
        return False, f"the check itself failed: {e}"


# Claims of a change, in the forms models actually use. Deliberately narrow: a false alarm costs an extra model call.
_VERBS = r"(created|added|scheduled|saved|booked|drafted|updated|deleted|removed|marked|set up|remembered|completed)"
_CLAIMS = [
    re.compile(rf"\b(?:I|I've|I have|we've|I just)\s+(?:\w+\s+){{0,2}}?{_VERBS}\b", re.I),
    re.compile(rf"\b(?:has|have|had) been\s+(?:\w+\s+)?{_VERBS}\b", re.I),
    re.compile(rf"\b(?:is|are|was|were) now\s+{_VERBS}\b", re.I),
    # headline style: "Task created successfully!", "Added.", "Reminder set for Friday."
    re.compile(rf"^(?:\w+\s+){{0,2}}?(?:{_VERBS[1:-1]}|set)\b(?:\s+(?:successfully|for|on|to)\b|[.!]|$)", re.I),
]
_SENT = [
    re.compile(r"\b(?:I|I've|I have|I just)\s+(?:\w+\s+){0,2}?(?:sent|emailed|forwarded)\b", re.I),
    re.compile(r"\b(?:email|message|reply|mail)\b[^.?!]{0,30}\b(?:has been|was|is)\s+sent\b", re.I),
]
_HEDGE = re.compile(r"\b(?:can|could|would|will|shall|should|may|might|want|like|if|once|after|before|not|never|unable|cannot|"
                    r"haven't|didn't|wasn't|couldn't|won't)\b", re.I)


def claims(text: str) -> list[tuple[str, str]]:
    """Sentences that assert a change was made: [(kind, sentence)], kind 'change' or 'sent'."""
    out = []
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        s = re.sub(r"^[\W_]+", "", raw).strip()  # bullets, emoji, markdown
        if not s or s.endswith("?"):
            continue
        for kind, patterns in (("sent", _SENT), ("change", _CLAIMS)):
            m = next((m for p in patterns if (m := p.search(s))), None)
            if m and not _HEDGE.search(s[max(0, m.start() - 25): m.start()] + " " + m.group(0)):
                out.append((kind, s[:160]))
                break
    return out


NO_CHANGE = "no tool call in this turn made and confirmed that change"
PRIORITY = {"none": 0, "no": 0, "low": 1, "medium": 2, "normal": 2, "high": 3, "urgent": 3}
PRIORITY_NAME = {0: "none", 1: "low", 2: "medium", 3: "high"}
_PRIORITY_RE = re.compile(r"\bpriority\W{0,3}(?:is\s+|of\s+|set\s+to\s+)?(none|low|medium|normal|high|urgent)\b|"
                          r"\b(low|medium|normal|high|urgent)[\s-]priority\b", re.I)
_DUE_RE = re.compile(r"\bdue\b\W{0,4}(?:on\s+|by\s+|date\W{0,3})?(\d{4}-\d{2}-\d{2})", re.I)


def _task_facts(receipts: list[dict]) -> list[dict]:
    rows = []
    for r in receipts:
        if r["verified"] is True and r["tool"] in ("create_task", "update_task") and r.get("ref"):
            t = db.one("SELECT title, priority, due FROM tasks WHERE id = ?", r["ref"])
            if t:
                rows.append(t)
    return rows


def contradiction(text: str, receipts: list[dict]) -> tuple[str, str]:
    """A detail the answer states about a saved task that differs from what was actually saved: (sentence, reason)."""
    facts = _task_facts(receipts)
    if not facts:
        return "", ""
    for raw in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
        for m in _PRIORITY_RE.finditer(raw):
            said = PRIORITY[(m.group(1) or m.group(2)).lower()]
            if all(f["priority"] != said for f in facts):
                saved = ", ".join(f"{PRIORITY_NAME.get(f['priority'], f['priority'])} ({f['title']})" for f in facts)
                return raw.strip()[:160], f"the saved priority is {saved}, not {PRIORITY_NAME[said]}"
        for m in _DUE_RE.finditer(raw):
            if all(f["due"] != m.group(1) for f in facts):
                saved = ", ".join(f"{f['due'] or 'none'} ({f['title']})" for f in facts)
                return raw.strip()[:160], f"the saved due date is {saved}, not {m.group(1)}"
    return "", ""


def summary(receipts: list[dict]) -> str:
    """What this turn really did, from the receipts alone: used when the model gives no usable answer."""
    lines = [f"- {r['evidence']}" for r in receipts if r["verified"] is True and r["effect"] != "read"]
    failed = [f"- {r['tool']} failed ({r['outcome']})" for r in receipts if r["outcome"] not in ("ok", "verified", "duplicate")]
    if not lines and not failed:
        return ""
    return "\n".join(["Here is what was actually done:", *lines, *failed])


def unbacked_claim(text: str, receipts: list[dict]) -> tuple[str, str]:
    """The first statement in the answer that this turn's confirmed changes do not support: (sentence, reason)."""
    changed = any(r["verified"] is True and r["effect"] != "read" for r in receipts)
    for kind, sentence in claims(text):
        if kind == "sent":  # nothing in Workbench sends mail, so a send claim is never backed
            return sentence, "Workbench cannot send email, only save drafts"
        if not changed:
            return sentence, NO_CHANGE
    return contradiction(text, receipts)


# The ledger: tool calls and turns are rows in `actions` (actions.py), next to the model calls llm.py records
def record_tool(ctx, tool: str, outcome: str, verified: bool | None, evidence: str, from_text: bool, ms: int, *,
                effect: str = "", args: str = "", payload: str | None = None, detail: str = "", failure: str = "",
                request_id: str | None = None) -> int:
    from . import actions

    return actions.add("tool_call", outcome, trace=ctx.trace(), payload=payload, model=ctx.model, tool=tool, effect=effect,
                       args=actions.args_preview(args), verified=None if verified is None else int(verified), evidence=evidence,
                       from_text=int(from_text), latency_ms=ms, detail=detail, failure_reason=failure,
                       request_id=request_id)


def record_turn(ctx, outcome: str, detail: str = "") -> None:
    """Close the turn's row (opened when the turn started) with how it ended."""
    from . import actions

    ms = int((time.monotonic() - ctx.started) * 1000) if ctx.started else 0
    if ctx.turn_action_id:
        actions.finish(ctx.turn_action_id, outcome, failure_reason=detail, latency_ms=ms)
    else:
        actions.add("turn", outcome, trace=ctx.trace(), model=ctx.model, failure_reason=detail)


MODEL_FAULTS = ("bad_args", "unknown_tool", "rejected")


def reliability() -> dict[str, dict]:
    """Per model: how its tool calls and its claims held up. Policy holds and tool side outages are not its fault."""
    out: dict[str, dict] = {}
    for r in db.all(
        "SELECT model, COUNT(*) AS calls, SUM(outcome IN ('ok', 'verified')) AS ok, "
        f"SUM(outcome IN ({','.join('?' * len(MODEL_FAULTS))})) AS model_errors, SUM(outcome = 'check_failed') AS check_failed, "
        "SUM(outcome = 'verified') AS verified, SUM(from_text) AS from_text FROM actions "
        "WHERE kind = 'tool_call' AND model != '' AND outcome NOT IN ('held', 'denied') GROUP BY model",
        *MODEL_FAULTS,
    ):
        judged = r["ok"] + r["model_errors"] + r["check_failed"]
        out[r["model"]] = {**r, "success_rate": round(r["ok"] / judged, 3) if judged else None}
    for t in db.all("SELECT model, COUNT(*) AS turns, SUM(outcome = 'unbacked') AS unbacked, SUM(outcome = 'fixed') AS fixed, "
                    "SUM(outcome = 'model_error') AS model_failures FROM actions WHERE kind = 'turn' AND model != '' GROUP BY model"):
        out.setdefault(t["model"], {"model": t["model"], "calls": 0, "success_rate": None}).update(
            turns=t["turns"], false_claims_caught=t["fixed"] + t["unbacked"], false_claims_left=t["unbacked"],
            model_failures=t["model_failures"])
    return out
