"""The assistant's core rules, where code can hold them: skills only offered after a task that succeeded with 3 or
more tool calls; at most 5 learned facts per conversation; the real budget in every prompt; mail stays draft only."""

import asyncio
import json

from conftest import CALLS, SCRIPT, events, tool_call


def _turn(cid, results, final="All done."):
    from app import assistant

    assistant._save(cid, "user", "Plan my week with everything you can find")
    for i, (tool, out) in enumerate(results):
        assistant._save(cid, "assistant", "", [{"id": f"t{i}", "type": "function", "function": {"name": tool, "arguments": "{}"}}])
        assistant._save(cid, "tool", out, tool_call_id=f"t{i}", name=tool)
    if final is not None:
        assistant._save(cid, "assistant", final)


def test_no_skill_is_offered_for_a_failed_or_short_task(client):
    from app import assistant, db, learn

    for results, final in (([("get_agenda", "{}"), ("list_tasks", "Error: database locked"), ("search_notes", "[]")], "Done."),  # a tool failed
                           ([("get_agenda", "{}"), ("list_tasks", "[]")], "Done."),                                            # only 2 calls
                           ([("get_agenda", "{}"), ("list_tasks", "[]"), ("search_notes", "[]")], None),                      # no answer
                           ([("get_agenda", "{}"), ("list_tasks", "[]"), ("search_notes", "[]")], "Stopped. Tell me how you want to continue.")):
        cid = assistant.new_conversation("web")
        _turn(cid, results, final)
        n = len(CALLS)
        assert asyncio.run(learn.mine_skill(cid)) == ""
        assert len(CALLS) == n  # not even asked about
        assert not db.one("SELECT id FROM approvals WHERE conversation_id = ? AND tool = 'save_skill'", cid)


def test_waiting_for_approval_is_not_a_failure(client):
    from app import assistant, db, learn

    cid = assistant.new_conversation("web")
    _turn(cid, [("get_agenda", "{}"), ("list_tasks", "[]"), ("create_checkin", "Not done yet: this needs the user's approval (request #1).")])
    assert asyncio.run(learn.mine_skill(cid)) == "weekly-plan"
    assert db.one("SELECT id FROM approvals WHERE conversation_id = ? AND tool = 'save_skill' AND status = 'pending'", cid)


def test_the_assistant_cannot_save_a_skill_without_approval(client):
    from app import db

    db.run("DELETE FROM skills WHERE name = 'my-proc'")
    SCRIPT[:] = [tool_call("save_skill", {"name": "my-proc", "description": "d", "steps": "1. x"}), {"role": "assistant", "content": "Offered."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "save that as a skill"}))
    assert any(e["type"] == "approval" and e["name"] == "save_skill" for e in ev)
    assert not db.one("SELECT id FROM skills WHERE name = 'my-proc'")


def test_at_most_five_facts_are_learned_from_one_conversation(client, monkeypatch):
    from app import assistant, db, learn

    cid = assistant.new_conversation("web")
    batches = iter([[{"content": f"The user fact number {i} is quite specific", "kind": "fact"} for i in range(4)],
                    [{"content": f"Another durable user fact {i} worth keeping", "kind": "fact"} for i in range(4)]])

    async def fake(conv, prompt, payload, channel):
        return next(batches)
    monkeypatch.setattr(learn, "_model_json", fake)
    for _ in range(2):
        assistant._save(cid, "user", "Here is a long message about my life, my job, my family and my preferences.")
        asyncio.run(learn.mine_memories(cid))
    assert db.one("SELECT COUNT(*) AS n FROM memories WHERE conversation_id = ? AND source = 'auto'", cid)["n"] == 5


def test_every_prompt_carries_the_real_budget(client):
    from app import db

    db.set_setting("monthly_limit_cents", "1000")
    try:
        SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
        CALLS.clear()
        events(client.post("/api/assistant/chat", json={"message": "hello"}))
        sys_prompt = next(c for c in CALLS if c.get("tools"))["messages"][0]["content"]
        assert "Budget: $" in sys_prompt and "of the $10.00 monthly limit spent" in sys_prompt
        assert "Never invent facts" in sys_prompt and "Mail is draft only" in sys_prompt
    finally:
        db.set_setting("monthly_limit_cents", "0")


def test_mail_has_no_send_tool(client):
    from app import assistant

    names = {t.name for t in assistant.TOOLS}
    assert "save_email_draft" in names and not any("send" in n and "mail" in n for n in names)
