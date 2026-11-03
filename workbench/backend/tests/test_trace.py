"""Item 21: the run log as a trace. Every turn lists with its totals and opens as a tree of model calls, tool calls
and approval decisions, read from the one actions table (R2) and never exposing encrypted raw arguments (R7)."""

import memfacts
import json

from conftest import SCRIPT, events, tool_call
from test_hardening import _poisoned_page


def _walk(node):
    yield node
    for c in node["children"]:
        yield from _walk(c)


def test_a_turn_lists_with_totals_and_opens_as_its_tree(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Trace check"}), {"role": "assistant", "content": "Task created successfully!"}]
    cid = events(client.post("/api/assistant/chat", json={"message": "add trace check"}))[0]["conversation_id"]
    listed = client.get("/api/runs", params={"conversation_id": cid}).json()["runs"]
    assert len(listed) == 1
    run = listed[0]
    assert run["kind"] == "turn" and run["outcome"] == "backed" and run["conversation_title"] == "add trace check"
    assert (run["steps"], run["model_calls"], run["tool_calls"], run["failures"]) == (3, 2, 1, 0)
    assert (run["total_tokens_in"], run["total_tokens_out"]) == (2000, 1000)

    tree = client.get(f"/api/runs/{run['id']}").json()
    assert [c["kind"] for c in tree["children"]] == ["model_call", "tool_call", "model_call"]
    tool = tree["children"][1]
    assert tool["tool"] == "create_task" and tool["outcome"] == "verified" and "Trace check" in tool["args"] and tool["evidence"]
    assert tree["children"][0]["provider"] == "Fake" and tree["children"][0]["protocol"] == "chat_completions"


def test_an_approval_nests_under_the_held_call(client, monkeypatch):
    _poisoned_page(monkeypatch)
    SCRIPT[:] = [tool_call("read_page", {"url": "https://recipes.example/pasta"}, "r"),
                 tool_call("remember", {"content": "Trace secret sk-proj-TRACEabcdefghijklmnopqrstuv", "kind": "fact"}, "m"),
                 {"role": "assistant", "content": "Needs your approval."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "read https://recipes.example/pasta"}))
    held = next(e for e in ev if e["type"] == "approval_required")
    assert client.post(f"/api/approvals/{held['id']}", json={"approve": True}).json()["status"] == "done"
    run = client.get("/api/runs", params={"conversation_id": ev[0]["conversation_id"]}).json()["runs"][0]
    tree = client.get(f"/api/runs/{run['id']}").json()
    call = next(n for n in _walk(tree) if n["id"] == held["id"])
    assert call["outcome"] == "held"
    [decision] = call["children"]
    assert decision["kind"] == "decision" and decision["outcome"] == "approved"
    assert [c["kind"] for c in decision["children"]] == ["tool_call"] and decision["children"][0]["tool"] == "remember"
    # the trace shows redacted arguments only; encrypted raw arguments never leave the server
    body = json.dumps(tree) + json.dumps(client.get("/api/runs", params={"limit": 200}).json())
    assert "payload_enc" not in body and "TRACEabcdef" not in body
    from app import db
    memfacts.drop('Trace secret')


def test_filters_and_paging(client):
    from app import db

    pid = client.post("/api/providers", json={"name": "Down", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "models": "m"}).json()["id"]
    events(client.post("/api/assistant/chat", json={"message": "this will fail", "model": f"{pid}/m"}))
    client.delete(f"/api/providers/{pid}")
    failed = client.get("/api/runs", params={"failed": "true"}).json()["runs"]
    assert failed and all(r["failures"] > 0 for r in failed)
    assert any(r["outcome"] == "model_error" for r in failed)

    client.post("/api/ai/assist", json={"action": "fix", "text": "teh cat"})  # a model call outside any turn is a run too
    data = client.get("/api/runs", params={"channel": "assist"}).json()
    assert "assist" in data["channels"] and data["runs"][0]["kind"] == "model_call"
    assert all(r["channel"] == "assist" for r in data["runs"])

    first = client.get("/api/runs", params={"limit": 2}).json()["runs"]
    assert len(first) == 2 and first[0]["id"] > first[1]["id"]
    nxt = client.get("/api/runs", params={"limit": 2, "before": first[1]["id"]}).json()["runs"]
    assert nxt and all(r["id"] < first[1]["id"] for r in nxt)
    roots = db.one("SELECT COUNT(*) AS n FROM actions WHERE parent_action_id IS NULL "
                   "AND kind IN ('turn', 'model_call', 'tool_call')")["n"]  # egress rows are an audit log, not runs
    assert len(client.get("/api/runs", params={"limit": 200}).json()["runs"]) == min(roots, 200)


def test_a_missing_run_is_404_and_the_trace_needs_sign_in(client):
    from fastapi.testclient import TestClient

    from app import main

    assert client.get("/api/runs/999999").status_code == 404
    anon = TestClient(main.app)
    assert anon.get("/api/runs").status_code == 401 and anon.get("/api/runs/1").status_code == 401
