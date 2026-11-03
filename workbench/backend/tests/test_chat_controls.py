"""feat/chat-controls: Stop (cancellation keeps partial state), regenerate (re-run the same user turn),
and edit-and-resend (truncate after a user message, replace its text, re-run)."""

import threading
import time

import pytest

from conftest import SCRIPT, events, tool_call

GATE = threading.Event()  # while set, the patched model call hangs, so a stop can land mid-turn


@pytest.fixture()
def slow_model(monkeypatch):
    """Make the assistant's model call hang until released, so a stop can land mid-turn."""
    import asyncio

    import app.llm as llm

    real_chat = llm.chat

    async def hanging_chat(ref, messages, tools=None, trace=None, schema=None):
        while GATE.is_set():
            await asyncio.sleep(0.02)
        return await real_chat(ref, messages, tools, trace, schema)

    monkeypatch.setattr(llm, "chat", hanging_chat)
    yield
    GATE.clear()


def test_stop_cancels_the_turn_and_keeps_partial_state(client, slow_model):
    SCRIPT[:] = [{"role": "assistant", "content": "setup"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    cid = ev[0]["conversation_id"]

    GATE.set()
    result = {}

    def run_turn():
        with client.stream("POST", "/api/assistant/chat", json={"message": "long answer", "conversation_id": cid}) as r:
            result["body"] = r.read().decode()

    t = threading.Thread(target=run_turn, daemon=True)
    t.start()
    time.sleep(0.4)  # let the turn start and register in TURNS

    r = client.post("/api/assistant/stop", json={"conversation_id": cid})
    assert r.status_code == 200 and r.json() == {"stopped": True}
    GATE.clear()  # release the model; the cancelled turn unwinds on its own from here
    t.join(timeout=5)
    assert not t.is_alive()
    assert '"type": "stopped"' in result["body"]

    # The question stays, with a visible note where the answer would have been; the slot is handed back
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["content"] for m in msgs if m["role"] == "user"][-1] == "long answer"
    assert msgs[-1]["role"] == "assistant" and "Stopped" in msgs[-1]["content"]
    from app import assistant as assistant_mod
    from app import db

    assert cid not in assistant_mod.TURNS
    outcome = db.one("SELECT outcome FROM actions WHERE kind = 'turn' AND conversation_id = ? ORDER BY id DESC LIMIT 1", cid)
    assert outcome and outcome["outcome"] == "stopped"


def test_stop_without_a_turn_is_a_noop(client):
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "quiet"}))
    cid = ev[0]["conversation_id"]
    r = client.post("/api/assistant/stop", json={"conversation_id": cid})
    assert r.status_code == 200 and r.json() == {"stopped": False}
    assert client.post("/api/assistant/stop", json={"conversation_id": 999999}).status_code == 404


def test_regenerate_drops_the_last_reply_and_reanswers(client):
    SCRIPT[:] = [{"role": "assistant", "content": "first answer"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "what is 2+2"}))
    cid = ev[0]["conversation_id"]

    r = client.post(f"/api/conversations/{cid}/regenerate")
    assert r.status_code == 200 and r.json() == {"message": "what is 2+2"}
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "what is 2+2")]

    # The client re-runs the same turn; the new answer lands after the kept question
    SCRIPT[:] = [{"role": "assistant", "content": "second answer"}]
    events(client.post("/api/assistant/chat", json={"message": r.json()["message"], "conversation_id": cid, "resend": True}))
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "what is 2+2"), ("assistant", "second answer")]


def test_regenerate_refuses_when_there_is_no_reply(client):
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hi"}))
    cid = ev[0]["conversation_id"]
    client.post(f"/api/conversations/{cid}/regenerate")
    assert client.post(f"/api/conversations/{cid}/regenerate").status_code == 400  # nothing left to regenerate
    assert client.post("/api/conversations/999999/regenerate").status_code == 404


def test_edit_and_resend_truncates_and_replaces(client):
    SCRIPT[:] = [{"role": "assistant", "content": "answer one"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "first question"}))
    cid = ev[0]["conversation_id"]
    SCRIPT[:] = [{"role": "assistant", "content": "answer two"}]
    events(client.post("/api/assistant/chat", json={"message": "second question", "conversation_id": cid}))
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["content"] for m in msgs] == ["first question", "answer one", "second question", "answer two"]
    first_user = msgs[0]["id"]

    # Edit the first message: everything after it goes away
    r = client.patch(f"/api/conversations/{cid}/messages/{first_user}", json={"content": "better question"})
    assert r.status_code == 200
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "better question")]

    SCRIPT[:] = [lambda ms: {"role": "assistant", "content": "saw: " + ms[-1]["content"]}]
    events(client.post("/api/assistant/chat", json={"message": "better question", "conversation_id": cid, "resend": True}))
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [(m["role"], m["content"]) for m in msgs] == [("user", "better question"), ("assistant", "saw: better question")]


def test_edit_rules(client):
    SCRIPT[:] = [{"role": "assistant", "content": "ans"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "q"}))
    cid = ev[0]["conversation_id"]
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assistant_msg = next(m for m in msgs if m["role"] == "assistant")
    # cannot edit an assistant message, an empty edit, or another conversation's message
    assert client.patch(f"/api/conversations/{cid}/messages/{assistant_msg['id']}", json={"content": "x"}).status_code == 400
    assert client.patch(f"/api/conversations/{cid}/messages/{msgs[0]['id']}", json={"content": " "}).status_code == 400
    assert client.patch(f"/api/conversations/999999/messages/{msgs[0]['id']}", json={"content": "x"}).status_code == 404


def test_resend_does_not_duplicate_the_user_message(client):
    SCRIPT[:] = [{"role": "assistant", "content": "a"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "q"}))
    cid = ev[0]["conversation_id"]
    n = len(client.get(f"/api/conversations/{cid}").json()["messages"])
    SCRIPT[:] = [{"role": "assistant", "content": "b"}]
    events(client.post("/api/assistant/chat", json={"message": "q", "conversation_id": cid, "resend": True}))
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert len(msgs) == n + 1  # only the new assistant reply was added
    assert [m["content"] for m in msgs if m["role"] == "user"] == ["q"]


def test_chat_controls_require_auth(client):
    from fastapi.testclient import TestClient

    from app import main

    anon = TestClient(main.app)
    h = {"X-Workbench": "1"}  # the CSRF header browsers always send; without a session the routes must still refuse
    assert anon.post("/api/assistant/stop", json={"conversation_id": 1}, headers=h).status_code == 401
    assert anon.post("/api/conversations/1/regenerate", headers=h).status_code == 401
    assert anon.patch("/api/conversations/1/messages/1", json={"content": "x"}, headers=h).status_code == 401
