"""The only calls the rest of Workbench makes into the adapt package. Each is one line at its call site."""

from __future__ import annotations

import logging

from .. import db
from . import jobs, knowledge, store

log = logging.getLogger("adapt")


def context(user_text: str) -> list[str]:
    """Extra system prompt parts for this turn (assistant.system_prompt)."""
    try:
        block = knowledge.context_block(user_text or "")
        return [block] if block else []
    except Exception as e:  # noqa: BLE001 - never break a chat turn
        log.warning("adapt context failed: %s", e)
        return []


def memory_replaces_legacy() -> bool:
    """learn.py asks this: conversations are mined into the encrypted store instead of the old plaintext memories table.
    Always true; the "Learn about me" switch (adapt_memory) then decides whether anything is learned at all."""
    return True


async def after_turn(conv_id: int, channel: str) -> None:
    """Run by learn._after (inside its one-at-a-time lock): learn facts, log the work, look for routines to suggest."""
    try:
        await knowledge.mine_conversation(conv_id)
    except Exception as e:  # noqa: BLE001
        log.warning("adapt memory failed for conversation %s: %s", conv_id, e)
    try:
        if channel in ("schedule", "agent"):
            return  # a routine running is not the person doing the work again
        last = db.one("SELECT id, content FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1", conv_id)
        if last and len(last["content"].strip()) >= 12 and not last["content"].startswith("[I "):
            kind = jobs.classify(last["content"])
            if kind != "other":
                jobs.observe(kind, "chat", last["content"][:300], f"conversation:{conv_id}")
                jobs.refresh_suggestions()
    except Exception as e:  # noqa: BLE001
        log.warning("adapt activity failed for conversation %s: %s", conv_id, e)


def observe(source: str, summary: str, kind: str | None = None, ref: str = "") -> None:
    """For other features (files read, sites checked, tasks done) to report work they did for the person."""
    try:
        jobs.observe(kind, source, summary, ref)
        jobs.refresh_suggestions()
    except Exception as e:  # noqa: BLE001
        log.warning("adapt observe failed: %s", e)


def guard(tool_effect: str, channel: str, conversation_id: int | None) -> bool:
    """True when a tool must not run: a routine created from a suggestion never sends or changes anything outside the
    workspace by itself (assistant.needs_approval). In unattended runs that means it is refused and reported."""
    if channel != "schedule" or tool_effect not in ("external", "persistent") or not conversation_id:
        return False
    try:
        conv = db.one("SELECT external_id FROM conversations WHERE id = ?", conversation_id)
        return bool(conv and conv["external_id"].isdigit() and jobs.is_learned_routine(int(conv["external_id"])))
    except Exception:  # noqa: BLE001 - when in doubt, hold
        return True


def register_tools() -> None:
    """Adds run_playbook to the assistant's tools (so chat and routines can replay what the person taught)."""
    from .. import assistant
    from . import playbooks

    if "run_playbook" in assistant.TOOL_MAP:
        return

    async def _run_playbook(playbook_id: int, values: dict | None = None, ctx=None) -> dict:
        r = await playbooks.run(int(playbook_id), values, "routine" if ctx and ctx.channel == "schedule" else "assistant")
        return {"run_id": r["id"], "status": r["status"], "note": "Steps that send, submit or pay wait for the user's OK."}

    def _list_playbooks() -> list[dict]:
        return [{"id": p["id"], "name": p["name"], "summary": p.get("summary"), "params": [x["name"] for x in p.get("params", [])]}
                for p in playbooks.all_playbooks()]

    tools = [
        assistant.Tool("list_playbooks", "List the tasks the user taught you with Show me how (id, name, parameters).", {},
                       _list_playbooks, ()),
        assistant.Tool("run_playbook", "Replay a task the user taught you. values: the playbook's parameters by name; "
                       "leave out to reuse the last run's values.",
                       {"playbook_id": assistant.I(), "values": {"type": "object", "description": "parameter name -> value"}},
                       _run_playbook, ("playbook_id",), effect="write"),
    ]
    for t in tools:
        assistant.TOOLS.append(t)
        assistant.TOOL_MAP[t.name] = t


def ensure() -> None:
    store.ensure()
