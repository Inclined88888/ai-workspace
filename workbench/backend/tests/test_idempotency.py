"""Item 28: a change is made once. A model repeating a write in the same turn, a client retrying a request, and Telegram
redelivering a message after a restart must not run the same side effect twice."""

import asyncio

from conftest import SCRIPT, events, tool_call


def _tasks(title: str) -> int:
    from app import db
    return db.one("SELECT COUNT(*) AS n FROM tasks WHERE title = ?", title)["n"]


def _tool_rows(cid: int) -> list[dict]:
    from app import db
    return db.all("SELECT a.tool, a.outcome, a.detail FROM actions a JOIN actions t ON a.parent_action_id = t.id "
                  "WHERE t.conversation_id = ? AND t.kind = 'turn' AND a.kind = 'tool_call' ORDER BY a.id", cid)


def test_a_repeated_write_in_one_turn_runs_once(client):
    seen = []
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem once"}, "a"),
                 tool_call("create_task", {"title": "Idem once"}, "b"),
                 lambda msgs: seen.append(msgs[-1]["content"]) or {"role": "assistant", "content": "Task created."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add idem once"}))
    cid = ev[0]["conversation_id"]
    assert _tasks("Idem once") == 1
    rows = _tool_rows(cid)
    assert [r["outcome"] for r in rows] == ["verified", "duplicate"] and "not run again" in rows[1]["detail"]
    assert "Already done earlier in this turn" in seen[0] and "Do not repeat it" in seen[0]
    from app import db
    turn = db.one("SELECT outcome FROM actions WHERE kind = 'turn' AND conversation_id = ?", cid)
    assert turn["outcome"] == "backed"  # the claim is backed by the first call; the repeat is not reported as a failure


def test_the_same_write_in_a_new_turn_runs_again(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem twice"}), {"role": "assistant", "content": "Task created."}]
    cid = events(client.post("/api/assistant/chat", json={"message": "add idem twice"}))[0]["conversation_id"]
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem twice"}), {"role": "assistant", "content": "Task created."}]
    events(client.post("/api/assistant/chat", json={"message": "add another one", "conversation_id": cid}))
    assert _tasks("Idem twice") == 2


def test_a_toggle_still_runs_and_a_repeat_after_it_does_not(client):
    from app import db
    tid = client.post("/api/tasks", json={"title": "Idem toggle"}).json()["id"]
    doing, todo = {"task_id": tid, "status": "doing"}, {"task_id": tid, "status": "todo"}
    SCRIPT[:] = [tool_call("update_task", doing, "1"), tool_call("update_task", todo, "2"), tool_call("update_task", doing, "3"),
                 tool_call("update_task", doing, "4"), {"role": "assistant", "content": "Moved it."}]
    cid = events(client.post("/api/assistant/chat", json={"message": "toggle it"}))[0]["conversation_id"]
    assert [r["outcome"] for r in _tool_rows(cid)] == ["verified", "verified", "verified", "duplicate"]
    assert db.one("SELECT status FROM tasks WHERE id = ?", tid)["status"] == "doing"


def test_reads_and_failed_calls_are_not_deduplicated(client, monkeypatch):
    from app import assistant

    original = assistant.TOOL_MAP["create_task"].fn
    calls = []

    def flaky(title, notes="", due=None, priority=0, project=""):
        calls.append(title)
        if len(calls) == 1:
            raise RuntimeError("database is locked")
        return original(title)

    monkeypatch.setattr(assistant.TOOL_MAP["create_task"], "fn", flaky)
    SCRIPT[:] = [tool_call("list_tasks", {}, "r1"), tool_call("list_tasks", {}, "r2"),
                 tool_call("create_task", {"title": "Idem retry"}, "w1"), tool_call("create_task", {"title": "Idem retry"}, "w2"),
                 {"role": "assistant", "content": "Task created."}]
    cid = events(client.post("/api/assistant/chat", json={"message": "retry it"}))[0]["conversation_id"]
    assert [r["outcome"] for r in _tool_rows(cid)] == ["ok", "ok", "tool_error", "verified"]
    assert _tasks("Idem retry") == 1 and len(calls) == 2


def test_a_retried_chat_request_with_the_same_key_is_not_run_again(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem retry request"}), {"role": "assistant", "content": "Task created."}]
    h = {"Idempotency-Key": "web-0f3c9a2e-retry"}
    first = events(client.post("/api/assistant/chat", json={"message": "add idem retry request"}, headers=h))
    cid = first[0]["conversation_id"]
    conversations = len(client.get("/api/conversations").json())
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem retry request"}), {"role": "assistant", "content": "Task created."}]
    again = client.post("/api/assistant/chat", json={"message": "add idem retry request"}, headers=h)
    assert again.status_code == 409
    body = again.json()
    assert body["conversation_id"] == cid and body["outcome"] == "backed" and body["turn_id"]
    assert _tasks("Idem retry request") == 1
    assert len(client.get("/api/conversations").json()) == conversations  # no empty conversation left behind
    assert client.post("/api/assistant/chat", json={"message": "x"}, headers={"Idempotency-Key": "bad key!"}).status_code == 400
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    assert client.post("/api/assistant/chat", json={"message": "a new request"}, headers={"Idempotency-Key": "web-another-key"}).status_code == 200


def test_telegram_does_not_answer_a_redelivered_update_twice(client, monkeypatch):
    from app import db, telegram

    client.put("/api/assistant/settings", json={"telegram_token": "4242:abc", "telegram_allowed_users": "555"})
    sent = []

    async def fake_api(client_, method, **params):
        if method == "sendMessage":
            sent.append(params["text"])
        return {}

    monkeypatch.setattr(telegram, "_api", fake_api)
    msg = {"chat": {"id": 555}, "from": {"id": 555}, "text": "add milk"}
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem milk"}), {"role": "assistant", "content": "Task created."}]
    asyncio.run(telegram._handle(None, msg, 9001))
    SCRIPT[:] = [tool_call("create_task", {"title": "Idem milk"}), {"role": "assistant", "content": "Task created."}]
    asyncio.run(telegram._handle(None, msg, 9001))  # the same update after a restart
    assert sent == ["Task created."] and _tasks("Idem milk") == 1
    assert db.one("SELECT 1 FROM actions WHERE request_id = 'turn:telegram:4242:9001'")
    client.put("/api/assistant/settings", json={"telegram_token": ""})
