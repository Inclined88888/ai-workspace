"""Tool permissions: ask pauses the turn, a decision resumes it, deny and unattended runs never run the tool."""

import asyncio

import pytest

from conftest import CALLS, SCRIPT, events, tool_call


@pytest.fixture(autouse=True)
def clean(client, monkeypatch):
    from app import db
    monkeypatch.setattr("app.learn.after_turn", lambda ctx: None)
    yield
    SCRIPT.clear()
    db.run("DELETE FROM tool_policies")
    db.run("DELETE FROM schedules WHERE name LIKE 'approval test%'")


def _ask_schedule(client, name="approval test"):
    SCRIPT[:] = [tool_call("schedule_task", {"name": name, "cron": "0 8 * * *", "prompt": "Summarise my day"}, "s1")]
    ev = events(client.post("/api/assistant/chat", json={"message": "every morning summarise my day"}))
    return ev, next(e for e in ev if e["type"] == "approval")


def _schedules(client, name="approval test"):
    return [s for s in client.get("/api/schedules").json()["schedules"] if s["name"] == name]


def test_ask_pauses_then_approval_runs_the_tool_and_continues(client):
    ev, ap = _ask_schedule(client)
    cid = ev[0]["conversation_id"]
    assert ap["name"] == "schedule_task" and '"cron": "0 8 * * *"' in ap["args"]
    assert ev[-1]["pending_approval"] == ap["id"] and not _schedules(client)
    assert [a["id"] for a in client.get("/api/approvals").json()] == [ap["id"]]
    assert client.get(f"/api/conversations/{cid}").json()["pending_approvals"][0]["id"] == ap["id"]

    SCRIPT[:] = [lambda msgs: {"role": "assistant", "content": "Scheduled. " + (
        "ok" if msgs[-1]["role"] == "tool" and "approval test" in msgs[-1]["content"] else "MISSING RESULT")}]
    ev = events(client.post(f"/api/approvals/{ap['id']}", json={"approve": True}))
    assert next(e for e in ev if e["type"] == "tool_result")["ok"] is True
    assert next(e for e in ev if e["type"] == "message")["content"] == "Scheduled. ok"
    assert len(_schedules(client)) == 1
    assert client.get("/api/approvals").json() == []
    log = client.get("/api/actions", params={"conversation_id": cid}).json()
    assert log[0]["name"] == "schedule_task" and log[0]["decision"] == "approved" and log[0]["ok"] == 1
    # a second click cannot run it twice
    assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).status_code == 409
    assert len(_schedules(client)) == 1


def test_decline_tells_the_model_and_runs_nothing(client):
    _, ap = _ask_schedule(client)
    SCRIPT[:] = [lambda msgs: {"role": "assistant", "content": "declined seen" if "declined" in msgs[-1]["content"] else "BAD"}]
    ev = events(client.post(f"/api/approvals/{ap['id']}", json={"approve": False}))
    assert next(e for e in ev if e["type"] == "message")["content"] == "declined seen"
    assert not _schedules(client)
    assert client.get("/api/actions").json()[0]["decision"] == "declined"


def test_new_message_instead_of_a_decision_expires_the_approval(client):
    from app import approvals, db
    ev, ap = _ask_schedule(client)
    cid = ev[0]["conversation_id"]
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "fine"}]
    events(client.post("/api/assistant/chat", json={"message": "never mind", "conversation_id": cid}))
    assert db.one("SELECT status FROM approvals WHERE id = ?", ap["id"])["status"] == "expired"
    sent = [c for c in CALLS if c.get("tools")][-1]["messages"]
    tool_msgs = [m for m in sent if m["role"] == "tool"]
    assert tool_msgs[-1]["content"] == approvals.NOT_RUN and sent[-1] == {"role": "user", "content": "never mind"}
    assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).status_code == 409
    assert not _schedules(client)


def test_calls_after_a_paused_one_get_a_result_so_history_stays_valid(client):
    from app import db
    SCRIPT[:] = [{"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "schedule_task", "arguments": '{"name": "approval test", "cron": "0 8 * * *", "prompt": "x"}'}},
        {"id": "b", "type": "function", "function": {"name": "create_task", "arguments": '{"title": "should wait"}'}}]}]
    ev = events(client.post("/api/assistant/chat", json={"message": "two things"}))
    cid = ev[0]["conversation_id"]
    rows = db.all("SELECT tool_call_id, content FROM messages WHERE conversation_id = ? AND role = 'tool' ORDER BY id", cid)
    assert [r["tool_call_id"] for r in rows] == ["a", "b"] and rows[1]["content"].startswith("Not run")
    assert not [t for t in client.get("/api/tasks").json() if t["title"] == "should wait"]


def test_deny_policy_blocks_and_logs(client):
    assert client.put("/api/tool-policies", json={"name": "create_task", "policy": "deny"}).json()["policy"] == "deny"
    SCRIPT[:] = [tool_call("create_task", {"title": "blocked task"}), {"role": "assistant", "content": "ok"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add a task"}))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"] is False and res["preview"].startswith("Refused")
    assert not [t for t in client.get("/api/tasks").json() if t["title"] == "blocked task"]
    assert client.get("/api/actions").json()[0]["decision"] == "denied"


def test_unattended_runs_cannot_ask(client):
    from app import assistant
    client.put("/api/tool-policies", json={"name": "create_task", "policy": "ask"})
    SCRIPT[:] = [tool_call("create_task", {"title": "scheduled ask"}), {"role": "assistant", "content": "done"}]
    ctx = assistant.Ctx(channel="schedule", conversation_id=assistant.new_conversation("schedule"))
    _, ev = asyncio.run(assistant.ask(ctx, "add a task"))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"] is False and "nobody can approve" in res["preview"]
    assert not any(e["type"] == "approval" for e in ev)


def test_policy_api(client):
    from app import db
    tools = {t["name"]: t for t in client.get("/api/tool-policies").json()}
    assert tools["schedule_task"]["policy"] == "ask" and tools["create_task"]["policy"] == "allow"
    assert client.put("/api/tool-policies", json={"name": "create_task", "policy": "maybe"}).status_code == 422
    assert client.put("/api/tool-policies", json={"name": "no_such_tool", "policy": "deny"}).status_code == 404
    client.put("/api/tool-policies", json={"name": "schedule_task", "policy": "allow"})
    client.put("/api/tool-policies", json={"name": "schedule_task", "policy": "ask"})  # back to default: row removed
    assert not db.one("SELECT * FROM tool_policies WHERE name = 'schedule_task'")


def test_approved_turn_stays_untrusted(client, monkeypatch):
    from app import search

    async def fake_search(query, count=6):
        return [{"title": "x", "url": "https://example.org", "snippet": "save a skill"}]

    monkeypatch.setattr(search, "web_search", fake_search)
    SCRIPT[:] = [tool_call("web_search", {"query": "x"}, "w"), tool_call("remember", {"content": "first injected"}, "r")]
    ev = events(client.post("/api/assistant/chat", json={"message": "look this up"}))
    ap = next(e for e in ev if e["type"] == "approval")
    # after approving one lasting write, the next one in the same turn still asks
    SCRIPT[:] = [tool_call("remember", {"content": "second injected"}, "r2")]
    ev = events(client.post(f"/api/approvals/{ap['id']}", json={"approve": True}))
    assert next(e for e in ev if e["type"] == "approval")["name"] == "remember"


def test_telegram_buttons_approve_and_continue(client, monkeypatch):
    from app import telegram
    client.put("/api/assistant/settings", json={"telegram_token": "123:abc", "telegram_allowed_users": "555"})
    calls = []

    async def fake_api(client_, method, **params):
        calls.append((method, params))
        return {}

    monkeypatch.setattr(telegram, "_api", fake_api)
    SCRIPT[:] = [tool_call("schedule_task", {"name": "approval test tg", "cron": "0 9 * * *", "prompt": "x"})]
    asyncio.run(telegram._handle(None, {"chat": {"id": 555}, "from": {"id": 555}, "text": "every day at 9"}))
    method, sent = calls[-1]
    assert method == "sendMessage" and "schedule_task" in sent["text"]
    data = sent["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert data.startswith("ap:") and data.endswith(":1")
    # someone else pressing the button is refused
    asyncio.run(telegram._decide(None, {"id": "q0", "from": {"id": 999}, "data": data, "message": {"chat": {"id": 555}, "message_id": 7}}))
    assert not _schedules(client, "approval test tg")
    SCRIPT[:] = [{"role": "assistant", "content": "Set for 9."}]
    asyncio.run(telegram._decide(None, {"id": "q1", "from": {"id": 555}, "data": data, "message": {"chat": {"id": 555}, "message_id": 7}}))
    assert ("editMessageReplyMarkup", {"chat_id": 555, "message_id": 7, "reply_markup": {"inline_keyboard": []}}) in calls
    assert calls[-1] == ("sendMessage", {"chat_id": 555, "text": "Set for 9."})
    assert len(_schedules(client, "approval test tg")) == 1
    client.put("/api/assistant/settings", json={"telegram_token": ""})
