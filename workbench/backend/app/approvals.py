"""Tool permissions: allow, ask or deny per tool, approvals for paused tool calls, and a log of every tool call.

"ask" pauses the turn: the tool call gets a placeholder result, the user approves or declines in the web chat,
the terminal or Telegram, the placeholder is replaced by the real result and the conversation continues.
Scheduled runs and company agents have nobody to ask, so for them "ask" means "deny".
"""

import json

from fastapi import HTTPException

from . import db

POLICIES = ("allow", "ask", "deny")
# Tools that act on the outside world or plant lasting instructions ask by default; MCP tools always do
DEFAULTS = {"browse_web": "ask", "schedule_task": "ask"}
UNATTENDED = ("schedule", "agent")
WAITING = "Waiting for the user's approval."
NOT_RUN = "Not run: the user did not approve it."


def default_policy(name: str) -> str:
    return "ask" if name.startswith("mcp__") else DEFAULTS.get(name, "allow")


def policy(name: str) -> str:
    row = db.one("SELECT policy FROM tool_policies WHERE name = ?", name)
    return row["policy"] if row else default_policy(name)


def set_policy(name: str, value: str) -> None:
    if value not in POLICIES:
        raise HTTPException(400, f"Policy must be one of {', '.join(POLICIES)}")
    if value == default_policy(name):
        db.run("DELETE FROM tool_policies WHERE name = ?", name)
    else:
        db.run("INSERT INTO tool_policies(name, policy) VALUES (?, ?) ON CONFLICT(name) DO UPDATE SET policy = excluded.policy",
               name, value)


def decide(tool, ctx) -> tuple[str, str]:
    """(allow | ask | deny, reason shown to the user or the model)."""
    p = policy(tool.name)
    if p == "deny":
        return "deny", f"Refused: {tool.name} is turned off in Assistant settings > Tool permissions."
    reason = ""
    if p == "ask":
        reason = "This tool asks before it runs."
    elif tool.persists and ctx.untrusted:
        # Web pages and email can carry instructions aimed at the assistant: lasting writes need a person
        p, reason = "ask", "Web or email content is in this conversation turn and could be steering the assistant."
    if p == "ask" and ctx.channel in UNATTENDED:
        return "deny", (f"Refused: {tool.name} needs your approval, and nobody can approve during a scheduled or agent run. "
                        "Allow it in Assistant settings > Tool permissions to let unattended runs use it.")
    return p, reason


def log(ctx, name: str, arguments: str, decision: str, ok: bool, result: str) -> None:
    db.run("INSERT INTO actions(ts, conversation_id, channel, agent_id, name, arguments, decision, ok, result) "
           "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", db.now(), ctx.conversation_id, ctx.channel, (ctx.agent or {}).get("id"),
           name, (arguments or "")[:2000], decision, int(ok), (result or "")[:1000])


def request(ctx, call: dict, message_id: int, reason: str, model: str, prompt: str) -> int:
    fn = call["function"]
    return db.run("INSERT INTO approvals(conversation_id, message_id, tool_call_id, name, arguments, reason, channel, agent_id, "
                  "model, prompt, untrusted, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  ctx.conversation_id, message_id, call["id"], fn["name"], fn["arguments"] or "{}", reason, ctx.channel,
                  (ctx.agent or {}).get("id"), model or "", prompt, int(ctx.untrusted), db.now())


def pending(conversation_id: int | None = None) -> list[dict]:
    if conversation_id:
        return db.all("SELECT * FROM approvals WHERE status = 'pending' AND conversation_id = ? ORDER BY id", conversation_id)
    return db.all("SELECT * FROM approvals WHERE status = 'pending' ORDER BY id")


def expire(conversation_id: int) -> None:
    """A new message instead of a decision: the paused calls are not run, and the model is told so."""
    for a in pending(conversation_id):
        db.run("UPDATE approvals SET status = 'expired', decided_at = ? WHERE id = ?", db.now(), a["id"])
        db.run("UPDATE messages SET content = ? WHERE id = ?", NOT_RUN, a["message_id"])
        db.run("INSERT INTO actions(ts, conversation_id, channel, agent_id, name, arguments, decision, ok, result) "
               "VALUES (?, ?, ?, ?, ?, ?, 'expired', 0, '')", db.now(), a["conversation_id"], a["channel"], a["agent_id"],
               a["name"], a["arguments"][:2000])


def claim(approval_id: int, approve: bool) -> dict:
    """Mark a pending approval decided; exactly one caller wins, so a double click cannot run the tool twice."""
    status = "approved" if approve else "declined"
    if not db.changes("UPDATE approvals SET status = ?, decided_at = ? WHERE id = ? AND status = 'pending'", status, db.now(), approval_id):
        a = db.one("SELECT status FROM approvals WHERE id = ?", approval_id)
        raise HTTPException(404 if not a else 409, "Approval not found" if not a else f"Already {a['status']}")
    return db.one("SELECT * FROM approvals WHERE id = ?", approval_id)


def args_preview(arguments: str) -> str:
    try:
        return json.dumps(json.loads(arguments or "{}"), ensure_ascii=False)[:500]
    except ValueError:
        return (arguments or "")[:500]
