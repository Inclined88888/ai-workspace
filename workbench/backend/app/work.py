"""One lifecycle for all work, over the engines that already run it.

Research, missions, workers, coding tasks and company-agent tasks each keep their own state machine (see
docs/ARCHITECTURE.md); nothing here renames or replaces them. This module is the one place that says what each native
status means, so every client (browser, CLI, desktop) sees the same typed phase and the same allowed actions:

    created → planning → awaiting_approval → running → verifying → completed
                                   paused, cancelled and failed where they apply

Every native status change is recorded in work_events by database triggers (installed by db.py), in the same
transaction as the change, so no engine can move work without leaving a trace. TRANSITIONS is the contract those
recorded changes must follow; test_zz_work_transitions.py checks every change the whole test suite produced against it.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from fastapi import HTTPException

from . import db


class Phase(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    VERIFYING = "verifying"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


P = Phase
TERMINAL = frozenset({P.COMPLETED, P.FAILED, P.CANCELLED})
_ACTIVE = frozenset(P) - TERMINAL
# Allowed moves. Staying in a phase (a native change inside it, such as queued to running) is always allowed.
# Failed and cancelled work can run again (retry); completed work cannot be reopened.
TRANSITIONS: dict[Phase, frozenset[Phase]] = {
    P.CREATED: frozenset({P.PLANNING, P.AWAITING_APPROVAL, P.RUNNING, P.PAUSED, P.FAILED, P.CANCELLED}),
    P.PLANNING: frozenset({P.AWAITING_APPROVAL, P.RUNNING, P.PAUSED, P.FAILED, P.CANCELLED}),
    P.AWAITING_APPROVAL: frozenset({P.PLANNING, P.RUNNING, P.VERIFYING, P.PAUSED, P.COMPLETED, P.FAILED, P.CANCELLED}),
    P.RUNNING: frozenset({P.AWAITING_APPROVAL, P.VERIFYING, P.PAUSED, P.COMPLETED, P.FAILED, P.CANCELLED}),
    P.VERIFYING: frozenset({P.RUNNING, P.AWAITING_APPROVAL, P.COMPLETED, P.FAILED, P.CANCELLED}),
    P.PAUSED: frozenset({P.PLANNING, P.AWAITING_APPROVAL, P.RUNNING, P.FAILED, P.CANCELLED}),
    P.COMPLETED: frozenset(),
    P.FAILED: frozenset({P.RUNNING, P.CREATED}),
    P.CANCELLED: frozenset({P.RUNNING, P.CREATED}),
}


def allowed(old: Phase, new: Phase) -> bool:
    return old == new or new in TRANSITIONS[old]


# What each native status means. outcome refines a phase without adding one (a partly finished mission is completed).
@dataclass(frozen=True)
class Kind:
    table: str
    title_col: str
    statuses: dict[str, tuple[Phase, str]]   # native status → (phase, outcome)
    label: str


KINDS: dict[str, Kind] = {
    "research": Kind("research", "question", {
        "running": (P.RUNNING, ""), "done": (P.COMPLETED, ""), "error": (P.FAILED, ""), "cancelled": (P.CANCELLED, "")},
        "Research"),
    "mission": Kind("missions", "goal", {
        "planning": (P.PLANNING, ""), "review": (P.AWAITING_APPROVAL, "plan"), "review_step": (P.AWAITING_APPROVAL, "step"),
        "queued": (P.RUNNING, "queued"), "running": (P.RUNNING, ""), "waiting_approval": (P.AWAITING_APPROVAL, "action"),
        "paused": (P.PAUSED, ""), "done": (P.COMPLETED, ""), "partial": (P.COMPLETED, "partial"), "failed": (P.FAILED, ""),
        "cancelled": (P.CANCELLED, "")}, "Mission"),
    "worker": Kind("worker_tasks", "goal", {
        # queued is both "not started" and "waiting to continue" (after an approval, a review or a lost lease); the
        # outcome word says Queued either way, and the phase stays with running so a resumed worker is not "new"
        "queued": (P.RUNNING, "queued"), "running": (P.RUNNING, ""), "waiting_approval": (P.AWAITING_APPROVAL, "action"),
        "in_review": (P.VERIFYING, ""), "needs_you": (P.AWAITING_APPROVAL, "result"), "blocked": (P.PAUSED, "blocked"),
        "done": (P.COMPLETED, ""), "failed": (P.FAILED, ""), "cancelled": (P.CANCELLED, "")}, "Worker"),
    "coding": Kind("coding_runs", "task", {
        "running": (P.RUNNING, ""), "done": (P.COMPLETED, ""), "failed": (P.FAILED, ""), "cancelled": (P.CANCELLED, "")},
        "Coding task"),
    "agent_task": Kind("agent_tasks", "title", {
        "todo": (P.CREATED, ""), "queued": (P.CREATED, "queued"), "running": (P.RUNNING, ""), "done": (P.COMPLETED, ""),
        "failed": (P.FAILED, ""), "blocked": (P.PAUSED, "blocked")}, "Agent task"),
}
TABLE_KIND = {k.table: name for name, k in KINDS.items()}


def phase_of(kind: str, native: str) -> tuple[Phase, str]:
    """The typed phase of a native status. An unknown status is reported as such, never guessed into a phase."""
    try:
        return KINDS[kind].statuses[native]
    except KeyError:
        raise ValueError(f"{kind} has no phase for status {native!r}") from None


# Plain words for each phase, shown to people (the same in every client)
WORDS = {P.CREATED: "Waiting to start", P.PLANNING: "Planning", P.AWAITING_APPROVAL: "Ready for your review",
         P.RUNNING: "Working", P.VERIFYING: "Checking the result", P.PAUSED: "Paused", P.COMPLETED: "Completed",
         P.FAILED: "Could not finish", P.CANCELLED: "Stopped"}
OUTCOME_WORDS = {"partial": "Partly completed", "blocked": "Waiting on earlier work", "queued": "Queued",
                 "plan": "Plan ready for your approval", "action": "An action needs your approval",
                 "result": "Result needs your decision", "step": "Step ready for your review"}


@dataclass
class WorkItem:
    id: str                          # "<kind>:<native id>", stable across clients
    kind: str
    title: str
    phase: Phase
    outcome: str
    status_text: str
    native_status: str
    conversation_id: int | None
    project_id: int | None
    created_at: str
    updated_at: str
    progress: str = ""
    error: str = ""
    can_cancel: bool = False
    can_retry: bool = False
    steps: list = field(default_factory=list)

    def dict(self) -> dict:
        d = asdict(self)
        d["phase"] = str(self.phase)
        d["terminal"] = self.phase in TERMINAL
        return d


_SQL = {
    "research": "SELECT id, question AS title, status, progress, error, project_id, NULL AS conversation_id, created_at, updated_at FROM research",
    "mission": "SELECT id, goal AS title, status, '' AS progress, error, project_id, conversation_id, created_at, updated_at FROM missions",
    "worker": "SELECT w.id, w.goal AS title, w.status, w.attention AS progress, w.error, m.project_id, NULL AS conversation_id, "
              "w.mission_id, w.created_at, w.updated_at FROM worker_tasks w LEFT JOIN missions m ON m.id = w.mission_id",
    "coding": "SELECT id, task AS title, status, '' AS progress, error, NULL AS project_id, conversation_id, created_at, updated_at FROM coding_runs",
    "agent_task": "SELECT id, title, status, '' AS progress, '' AS error, NULL AS project_id, conversation_id, created_at, updated_at FROM agent_tasks",
}
_ID_COL = {"research": "id", "mission": "id", "worker": "w.id", "coding": "id", "agent_task": "id"}


def _origin(kind: str, row: dict) -> int | None:
    """The conversation the work was started from (the job record), else the engine's own conversation."""
    if kind == "worker":  # a worker belongs to its mission: the conversation that started the mission
        kind, ref = "mission", row.get("mission_id")
    else:
        ref = row["id"]
    j = db.one("SELECT conversation_id FROM jobs WHERE kind = ? AND ref_id = ? ORDER BY id LIMIT 1", kind, ref) \
        if kind in ("research", "mission", "coding") and ref else None
    if j:
        return j["conversation_id"]
    # missions run in their own internal conversation; only a conversation someone typed in counts as the origin
    return row.get("conversation_id") if kind == "coding" else None


def _item(kind: str, row: dict) -> WorkItem:
    try:
        phase, outcome = phase_of(kind, row["status"])
    except ValueError:
        phase, outcome = P.RUNNING, "unknown"
    words = OUTCOME_WORDS.get(outcome) or WORDS[phase]
    return WorkItem(id=f"{kind}:{row['id']}", kind=kind, title=(row["title"] or "")[:300], phase=phase, outcome=outcome,
                    status_text=words, native_status=row["status"], conversation_id=_origin(kind, row),
                    project_id=row.get("project_id"), created_at=row["created_at"], updated_at=row["updated_at"] or row["created_at"],
                    progress=(row.get("progress") or "")[:300], error=(row.get("error") or "")[:600],
                    can_cancel=_can(kind, "cancel", row["status"]), can_retry=_can(kind, "retry", row["status"]))


def parse_id(item_id: str) -> tuple[str, int]:
    kind, _, num = item_id.partition(":")
    if kind not in KINDS or not num.isdigit():
        raise HTTPException(404, "Unknown work item")
    return kind, int(num)


def get(item_id: str) -> WorkItem:
    kind, num = parse_id(item_id)
    row = db.one(f"{_SQL[kind]} WHERE {_ID_COL[kind]} = ?", num)
    if not row:
        raise HTTPException(404, "Work item not found")
    return _item(kind, row)


def list_items(conversation_id: int | None = None, project_id: int | None = None, active: bool = False, limit: int = 50) -> list[dict]:
    out: list[WorkItem] = []
    for kind, sql in _SQL.items():
        for row in db.all(f"{sql} ORDER BY {_ID_COL[kind]} DESC LIMIT ?", min(limit, 200)):
            it = _item(kind, row)
            if active and it.phase in TERMINAL:
                continue
            if conversation_id is not None and it.conversation_id != conversation_id:
                continue
            if project_id is not None and it.project_id != project_id:
                continue
            out.append(it)
    out.sort(key=lambda w: w.updated_at, reverse=True)
    return [w.dict() for w in out[:limit]]


# Actions, checked against the typed phase first, then done by the engine's own function (its checks still apply)
def _can(kind: str, action: str, native: str) -> bool:
    phase, _ = KINDS[kind].statuses.get(native, (P.RUNNING, ""))
    if action == "cancel":
        return phase not in TERMINAL and kind in ("research", "mission", "worker", "coding")
    if action == "retry":
        return (kind == "research" and native in ("error", "cancelled")) or \
               (kind == "worker" and native in ("failed", "needs_you", "blocked", "cancelled"))
    return False


async def cancel(item_id: str) -> dict:
    it = get(item_id)
    kind, num = parse_id(item_id)
    if not it.can_cancel:
        raise HTTPException(409, f"This {KINDS[kind].label.lower()} is {it.status_text.lower()}; it cannot be stopped now.")
    if kind == "research":
        from . import api

        api.cancel_research(num)
    elif kind == "mission":
        from . import missions

        missions.cancel(num)
    elif kind == "worker":
        from . import workers

        workers.cancel(num)
    elif kind == "coding":
        from . import coding

        await coding.stop(num)
    return get(item_id).dict()


async def retry(item_id: str) -> dict:
    it = get(item_id)
    kind, num = parse_id(item_id)
    if not it.can_retry:
        raise HTTPException(409, f"This {KINDS[kind].label.lower()} is {it.status_text.lower()}; only failed or stopped work can run again.")
    if kind == "research":
        from . import api

        await api.retry_research(num)
    elif kind == "worker":
        from . import workers

        workers.retry(num)
    return get(item_id).dict()


def events(item_id: str, limit: int = 100) -> list[dict]:
    kind, num = parse_id(item_id)
    rows = db.all("SELECT id, old_status, new_status, at FROM work_events WHERE kind = ? AND ref_id = ? ORDER BY id DESC LIMIT ?",
                  kind, num, limit)
    out = []
    for r in reversed(rows):
        new, _ = KINDS[kind].statuses.get(r["new_status"], (P.RUNNING, ""))
        old = KINDS[kind].statuses.get(r["old_status"], (None, ""))[0] if r["old_status"] else None
        out.append({"id": r["id"], "at": r["at"], "from": str(old) if old else None, "to": str(new),
                    "native_from": r["old_status"], "native_to": r["new_status"], "text": WORDS[new]})
    return out


def triggers_sql() -> str:
    """Triggers that record every status change of every engine, inside the changing transaction."""
    parts = []
    for kind, k in KINDS.items():
        t = k.table
        parts.append(f"""
CREATE TRIGGER IF NOT EXISTS work_ins_{t} AFTER INSERT ON {t} BEGIN
  INSERT INTO work_events(kind, ref_id, old_status, new_status, at) VALUES ('{kind}', NEW.id, NULL, NEW.status, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
END;
CREATE TRIGGER IF NOT EXISTS work_upd_{t} AFTER UPDATE OF status ON {t} WHEN NEW.status IS NOT OLD.status BEGIN
  INSERT INTO work_events(kind, ref_id, old_status, new_status, at) VALUES ('{kind}', NEW.id, OLD.status, NEW.status, strftime('%Y-%m-%dT%H:%M:%fZ','now'));
END;""")
    return "\n".join(parts)
