"""Goal mode: a durable objective attached to a conversation that auto-continues
with new turns when a turn ends without the goal met, up to a round allowance.

Stops when: the model declares the goal met via an explicit tool/marker that is
CHECKED (reuse receipts.py claim checks so 'done' must be backed by verified
effects where applicable); allowance used up; user cancels; an approval is
pending (never continue past a held action); repeated failure/no progress.
Persist in DB (resume after restart only if opted in).
"""

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from fastapi import HTTPException
from pydantic import BaseModel

from . import db, receipts

log = logging.getLogger("goals")

SCHEMA = """
CREATE TABLE IF NOT EXISTS goals (
  id INTEGER PRIMARY KEY,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  objective TEXT NOT NULL,
  phase TEXT NOT NULL DEFAULT 'active',           -- active | paused | blocked | complete | cancelled
  max_rounds INTEGER NOT NULL DEFAULT 5,
  rounds_used INTEGER NOT NULL DEFAULT 0,
  resume_on_restart INTEGER NOT NULL DEFAULT 0,
  blocked_reason TEXT,                            -- JSON: {"code": "...", "message": "..."}
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS goals_conv ON goals(conversation_id);
"""

DEFAULT_MAX_ROUNDS = 5
HARD_MAX_ROUNDS = 50  # hard ceiling

_ready: set[str] = set()


def ensure() -> None:
    from . import config

    if str(config.DB_PATH) not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


def _out(r: dict | None) -> dict:
    if not r:
        raise HTTPException(404, "Goal not found")
    r = dict(r)
    if r.get("blocked_reason"):
        try:
            r["blocked_reason"] = json.loads(r["blocked_reason"])
        except Exception:
            r["blocked_reason"] = None
    return r


def get(goal_id: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM goals WHERE id = ?", goal_id))


def get_by_conversation(conversation_id: int) -> dict | None:
    ensure()
    r = db.one("SELECT * FROM goals WHERE conversation_id = ? AND phase NOT IN ('complete', 'cancelled')", conversation_id)
    return _out(r) if r else None


def listing(conversation_id: int | None = None) -> list[dict]:
    ensure()
    if conversation_id:
        rows = db.all("SELECT * FROM goals WHERE conversation_id = ? ORDER BY id DESC", conversation_id)
    else:
        rows = db.all("SELECT * FROM goals ORDER BY id DESC")
    return [_out(r) for r in rows]


class GoalIn(BaseModel):
    conversation_id: int
    objective: str
    max_rounds: int = DEFAULT_MAX_ROUNDS
    resume_on_restart: bool = False


class GoalPatch(BaseModel):
    objective: str | None = None
    max_rounds: int | None = None
    resume_on_restart: bool | None = None


def _validate_max_rounds(value: int) -> int:
    if not isinstance(value, int) or value < 1:
        raise HTTPException(400, "max_rounds must be a positive integer")
    return min(value, HARD_MAX_ROUNDS)


def create(data: GoalIn) -> dict:
    ensure()
    # Check if there's already an active goal for this conversation
    existing = db.one(
        "SELECT * FROM goals WHERE conversation_id = ? AND phase NOT IN ('complete', 'cancelled')",
        data.conversation_id,
    )
    if existing:
        raise HTTPException(409, f"Conversation already has a {existing['phase']} goal (id={existing['id']})")

    max_rounds = _validate_max_rounds(data.max_rounds)
    now = db.now()
    gid = db.run(
        "INSERT INTO goals(conversation_id, objective, phase, max_rounds, rounds_used, resume_on_restart, created_at, updated_at) "
        "VALUES (?, ?, 'active', ?, 0, ?, ?, ?)",
        data.conversation_id,
        data.objective.strip(),
        max_rounds,
        1 if data.resume_on_restart else 0,
        now,
        now,
    )
    return get(gid)


def update(goal_id: int, data: GoalPatch) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] in ("complete", "cancelled"):
        raise HTTPException(409, f"Cannot update a {goal['phase']} goal")

    fields = {}
    if data.objective is not None:
        if not data.objective.strip():
            raise HTTPException(400, "objective must be non-empty")
        fields["objective"] = data.objective.strip()
    if data.max_rounds is not None:
        fields["max_rounds"] = _validate_max_rounds(data.max_rounds)
    if data.resume_on_restart is not None:
        fields["resume_on_restart"] = 1 if data.resume_on_restart else 0

    if fields:
        fields["updated_at"] = db.now()
        db.run(
            f"UPDATE goals SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
            *fields.values(),
            goal_id,
        )
    return get(goal_id)


def cancel(goal_id: int) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] in ("complete", "cancelled"):
        raise HTTPException(409, f"Goal is already {goal['phase']}")
    db.run("UPDATE goals SET phase = 'cancelled', updated_at = ? WHERE id = ?", db.now(), goal_id)
    return get(goal_id)


def complete(goal_id: int) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] == "complete":
        return goal
    if goal["phase"] == "cancelled":
        raise HTTPException(409, "Cannot complete a cancelled goal")
    db.run("UPDATE goals SET phase = 'complete', updated_at = ? WHERE id = ?", db.now(), goal_id)
    return get(goal_id)


def pause(goal_id: int) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] != "active":
        raise HTTPException(409, f"Only an active goal can be paused (current: {goal['phase']})")
    db.run("UPDATE goals SET phase = 'paused', updated_at = ? WHERE id = ?", db.now(), goal_id)
    return get(goal_id)


def resume(goal_id: int) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] not in ("paused", "blocked"):
        raise HTTPException(409, f"Only a paused or blocked goal can be resumed (current: {goal['phase']})")
    if goal["rounds_used"] >= goal["max_rounds"]:
        raise HTTPException(409, f"Goal exhausted its {goal['max_rounds']} rounds; increase max_rounds before resuming")
    db.run("UPDATE goals SET phase = 'active', updated_at = ? WHERE id = ?", db.now(), goal_id)
    return get(goal_id)


def block(goal_id: int, reason: dict) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] != "active":
        raise HTTPException(409, f"Only an active goal can be blocked (current: {goal['phase']})")
    if not reason.get("code") or not reason.get("message"):
        raise HTTPException(400, "block reason requires code and message")
    db.run(
        "UPDATE goals SET phase = 'blocked', blocked_reason = ?, updated_at = ? WHERE id = ?",
        json.dumps(reason),
        db.now(),
        goal_id,
    )
    return get(goal_id)


def increment_round(goal_id: int) -> dict:
    ensure()
    goal = get(goal_id)
    if goal["phase"] != "active":
        raise HTTPException(409, f"Goal is not active (current: {goal['phase']})")
    new_used = goal["rounds_used"] + 1
    db.run("UPDATE goals SET rounds_used = ?, updated_at = ? WHERE id = ?", new_used, db.now(), goal_id)
    return get(goal_id)


def can_continue(goal_id: int) -> tuple[bool, str]:
    """Check if the goal can continue to another round."""
    goal = get(goal_id)
    if goal["phase"] != "active":
        return False, f"goal phase is {goal['phase']}"
    if goal["rounds_used"] >= goal["max_rounds"]:
        return False, f"goal reached max rounds ({goal['max_rounds']})"
    return True, ""


# --- Integration with assistant turn loop -----------------------------------

GOAL_CONTINUE_PROMPT = (
    "[Automatic note, not from the user: the goal \"{objective}\" is still active. "
    "This is round {round} of {max_rounds}. Continue working on the goal and finish it. "
    "When the objective is fully achieved, call the complete_goal tool.]"
)

GOAL_COMPLETE_TOOL = {
    "name": "complete_goal",
    "description": "Mark the current conversation goal as complete. Only call this when the objective is fully achieved.",
    "parameters": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Brief summary of what was accomplished"}
        },
        "required": ["summary"],
    },
}


def goal_tool_spec() -> dict:
    return GOAL_COMPLETE_TOOL


async def maybe_continue_goal(ctx, final_answer: str, receipts_list: list[dict]) -> tuple[bool, str | None]:
    """
    Check if the current conversation has an active goal that should continue.
    Returns (should_continue, next_prompt).
    The model must call complete_goal (which is verified via receipts) to end the goal.
    """
    goal = get_by_conversation(ctx.conversation_id)
    if not goal:
        return False, None

    # Check if the model declared completion via complete_goal tool
    for r in receipts_list:
        if r.get("tool") == "complete_goal" and r.get("verified") is True:
            complete(goal["id"])
            return False, None

    # Check for unbacked "done" claim in the final answer (reuse receipts.unbacked_claim)
    claim, why = receipts.unbacked_claim(final_answer, receipts_list)
    if claim and ("goal" in claim.lower() or "complete" in claim.lower() or "finished" in claim.lower()):
        # Unverified completion claim - don't end the goal
        log.info("Goal %s: unbacked completion claim ignored: %s", goal["id"], claim)
        return True, None

    # Check if we can continue
    can, reason = can_continue(goal["id"])
    if not can:
        # Goal exhausted - mark as blocked
        block(goal["id"], {"code": "round-limit", "message": reason})
        return False, None

    # Check for pending approvals - never continue past a held action
    from . import actions
    pending = actions.pending(ctx.conversation_id)
    if pending:
        return False, None

    # Increment round and continue
    goal = increment_round(goal["id"])
    next_prompt = GOAL_CONTINUE_PROMPT.format(
        objective=goal["objective"],
        round=goal["rounds_used"],
        max_rounds=goal["max_rounds"],
    )
    return True, next_prompt