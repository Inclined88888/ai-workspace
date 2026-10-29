"""The run log: every agent action is one row in `actions` (R2). There is no other ledger.

Kinds and their outcomes:
  turn        running | no_claim | backed | fixed | unbacked | model_error | step_limit | empty_answer | budget_paused | interrupted
  model_call  ok | error
  tool_call   verified | ok | check_failed | bad_args | unknown_tool | rejected | tool_error | held | denied | duplicate
  decision    approved | denied        (the user's answer to a held tool_call, which is its parent)

Rows link through parent_action_id: a turn's model and tool calls point at the turn; an approved tool run points at
its decision, which points at the held call. Cost, budgets, reliability, approvals and the run log all read this table.
Text columns are redacted on the way in (they are a trace, R7); raw arguments a held action needs to run later are
kept encrypted in payload_enc and never logged.
"""

import json
import sqlite3
import uuid

from fastapi import HTTPException

from . import db
from .redact import redact

_TEXT = ("args", "failure_reason", "evidence", "detail")


def add(kind: str, outcome: str, *, trace: dict | None = None, payload: str | None = None, **fields) -> int:
    """Insert one action. trace carries who and where (channel, conversation_id, agent_id, parent); fields the rest."""
    from .security import encrypt

    row = {"channel": "", "conversation_id": None, "agent_id": None, "parent_action_id": None}
    t = trace or {}
    row.update(channel=t.get("channel", ""), conversation_id=t.get("conversation_id"), agent_id=t.get("agent_id"),
               parent_action_id=t.get("parent"))
    row.update(fields)
    for k in _TEXT:
        if row.get(k):
            row[k] = redact(str(row[k]))[:4000]
    if payload is not None:
        row["payload_enc"] = encrypt(payload)
    row.update(ts=db.now(), kind=kind, outcome=outcome, request_id=row.get("request_id") or uuid.uuid4().hex)
    cols = ", ".join(row)
    return db.run(f"INSERT INTO actions({cols}) VALUES ({', '.join('?' * len(row))})", *row.values())


def by_key(request_id: str) -> dict | None:
    """The action recorded under an idempotency key (item 28), if any. Keys are request_id values:
    "turn:<caller key>" for a turn a client may retry. Tool calls use last_change below."""
    return db.one("SELECT id, kind, outcome, conversation_id, evidence FROM actions WHERE request_id = ?", request_id)


def last_change(turn_id: int, key: str) -> dict | None:
    """The newest change in this turn made under an idempotency key ("tool:<turn>:<hash of tool and arguments>")."""
    return db.one("SELECT id, evidence FROM actions WHERE parent_action_id = ? AND kind = 'tool_call' AND request_id LIKE ? "
                  "ORDER BY id DESC LIMIT 1", turn_id, key + ":%")


def changed_since(turn_id: int, action_id: int) -> bool:
    """Whether any change (a write that ran) happened in this turn after the given action."""
    return bool(db.one("SELECT 1 FROM actions WHERE parent_action_id = ? AND kind = 'tool_call' AND id > ? AND effect != 'read' "
                       "AND outcome IN ('ok', 'verified') LIMIT 1", turn_id, action_id))


def finish(action_id: int, outcome: str, **fields) -> None:
    """Close an action that was opened before its result was known (a turn)."""
    for k in _TEXT:
        if fields.get(k):
            fields[k] = redact(str(fields[k]))[:4000]
    sets = ", ".join(f"{k} = ?" for k in ("outcome", *fields))
    db.run(f"UPDATE actions SET {sets} WHERE id = ?", outcome, *fields.values(), action_id)


def recover() -> int:
    """Turns still 'running' at startup were cut off by a restart."""
    return db.run("UPDATE actions SET outcome = 'interrupted', failure_reason = 'Workbench restarted during the turn' "
                  "WHERE kind = 'turn' AND outcome = 'running'")


# Approvals are held tool_call rows; a decision row answers each one exactly once (unique index on its parent).
_PENDING = ("SELECT a.* FROM actions a WHERE a.kind = 'tool_call' AND a.outcome = 'held' AND NOT EXISTS "
            "(SELECT 1 FROM actions d WHERE d.kind = 'decision' AND d.parent_action_id = a.id)")


def approval(action_id: int) -> dict:
    """A held or refused tool call as the approval card shows it, with the raw arguments for its owner only."""
    from .security import decrypt

    a = db.one("SELECT * FROM actions WHERE id = ? AND kind = 'tool_call' AND outcome IN ('held', 'denied')", action_id)
    if not a:
        raise HTTPException(404, "Approval request not found")
    status, result, decided_at = ("denied", "", a["ts"]) if a["outcome"] == "denied" else ("pending", "", None)
    d = db.one("SELECT * FROM actions WHERE kind = 'decision' AND parent_action_id = ?", action_id)
    if d:
        decided_at = d["ts"]
        run = db.one("SELECT * FROM actions WHERE kind = 'tool_call' AND parent_action_id = ?", d["id"])
        status = "denied" if d["outcome"] == "denied" else ("approved" if not run else ("done" if run["outcome"] in ("ok", "verified") else "failed"))
        result = (run["detail"] or run["failure_reason"]) if run else ""
    return {"id": a["id"], "conversation_id": a["conversation_id"], "tool": a["tool"],
            "args": decrypt(a["payload_enc"]) or a["args"], "effect": a["effect"], "reason": a["detail"],
            "status": status, "result": result, "created_at": a["ts"], "decided_at": decided_at}


def pending(conversation_id: int | None = None) -> list[dict]:
    if conversation_id:
        rows = db.all(_PENDING + " AND a.conversation_id = ? ORDER BY a.id", conversation_id)
    else:
        rows = db.all(_PENDING + " ORDER BY a.id DESC LIMIT 200")
    return [approval(r["id"]) for r in rows]


def decide(action_id: int, approve: bool) -> int:
    """Record the user's answer. Raises 409 if it was already answered (by another click or client)."""
    a = approval(action_id)
    if a["status"] != "pending":
        raise HTTPException(409, f"Already {a['status']}")
    try:
        return add("decision", "approved" if approve else "denied", tool=a["tool"],
                   trace={"channel": "web", "conversation_id": a["conversation_id"], "parent": action_id})
    except sqlite3.IntegrityError as e:
        raise HTTPException(409, "Already decided") from e


def raw_args(action_id: int) -> str:
    from .security import decrypt

    a = db.one("SELECT payload_enc, args FROM actions WHERE id = ?", action_id)
    return (decrypt(a["payload_enc"]) or a["args"]) if a else "{}"


def args_preview(raw: str) -> str:
    """Arguments as stored in the trace: compact JSON, redacted by add()."""
    try:
        return json.dumps(json.loads(raw or "{}"), ensure_ascii=False)[:2000]
    except ValueError:
        return (raw or "")[:2000]


# The trace (item 21): top-level runs and the tree of actions under each. payload_enc never leaves this module.
_TREE = ("WITH RECURSIVE tree(id) AS (SELECT ? UNION ALL SELECT a.id FROM actions a JOIN tree t ON a.parent_action_id = t.id) ")
FAILED = ("error", "check_failed", "bad_args", "unknown_tool", "tool_error", "unbacked", "model_error", "step_limit",
          "empty_answer", "interrupted", "denied", "rejected")


def runs(limit: int = 50, before: int | None = None, channel: str = "", failed: bool = False,
         conversation_id: int | None = None) -> list[dict]:
    """Newest first: actions with no parent (turns, and model calls made outside a turn), with totals over their tree."""
    where, params = ["r.parent_action_id IS NULL", "r.kind IN ('turn', 'model_call', 'tool_call')"], []
    if before:
        where.append("r.id < ?"); params.append(before)
    if channel:
        where.append("r.channel = ?"); params.append(channel)
    if conversation_id:
        where.append("r.conversation_id = ?"); params.append(conversation_id)
    if failed:
        where.append(f"r.outcome IN ({','.join('?' * len(FAILED))})"); params.extend(FAILED)
    rows = db.all(f"SELECT r.*, c.title AS conversation_title "
                  f"FROM actions r LEFT JOIN conversations c ON c.id = r.conversation_id "
                  f"WHERE {' AND '.join(where)} ORDER BY r.id DESC LIMIT ?", *params, max(1, min(limit, 200)))
    for r in rows:
        r.pop("payload_enc")
        r.update(totals(r["id"]))
    return rows


def totals(root: int) -> dict:
    t = db.one(_TREE + "SELECT COUNT(*) - 1 AS steps, "
               "SUM(CASE WHEN kind = 'model_call' THEN 1 ELSE 0 END) AS model_calls, "
               "SUM(CASE WHEN kind = 'tool_call' THEN 1 ELSE 0 END) AS tool_calls, "
               "COALESCE(SUM(tokens_in), 0) AS total_tokens_in, COALESCE(SUM(tokens_out), 0) AS total_tokens_out, "
               "SUM(usd_cost) AS total_usd, SUM(CASE WHEN kind = 'model_call' AND usd_cost IS NULL THEN 1 ELSE 0 END) AS unpriced, "
               f"SUM(CASE WHEN outcome IN ({','.join('?' * len(FAILED))}) THEN 1 ELSE 0 END) AS failures "
               "FROM actions WHERE id IN tree", root, *FAILED)
    return {k: (v or 0) if k != "total_usd" else v for k, v in t.items()}


def trace(root: int) -> dict:
    """One run as a tree: the action and everything under it, oldest first at each level."""
    rows = db.all(_TREE + "SELECT * FROM actions WHERE id IN tree ORDER BY id", root)
    if not rows:
        raise HTTPException(404, "Run not found")
    by_id = {r["id"]: r | {"children": []} for r in rows}
    for r in by_id.values():
        r.pop("payload_enc")
    for r in by_id.values():
        if r["id"] != root and r["parent_action_id"] in by_id:
            by_id[r["parent_action_id"]]["children"].append(r)
    out = by_id[root]
    conv = db.one("SELECT title FROM conversations WHERE id = ?", out["conversation_id"]) if out["conversation_id"] else None
    out["conversation_title"] = conv["title"] if conv else None
    out.update(totals(root))
    return out
