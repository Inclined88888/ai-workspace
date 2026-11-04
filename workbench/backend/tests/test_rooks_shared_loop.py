"""The rooks terminal on the app's shared agent loop: delegated file and shell tools, and per-action approvals that the
terminal and the app (browser, app window, phone) share."""

import asyncio
import json

import pytest

from conftest import SCRIPT, events, tool_call


@pytest.fixture
def bridge(client):
    token = client.post("/api/cli-token").json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    cid = client.post("/v1/rooks/conversation", json={"session": "shared-loop-test"}, headers=h).json()["conversation_id"]
    return h, cid


def test_terminal_tools_exist_only_on_the_cli_channel_and_replace_the_workspace_ones(client):
    from app import assistant

    cli = {t.name: t for t in assistant.available_tools(assistant.Ctx(channel="cli", conversation_id=0))}
    web = {t.name: t for t in assistant.available_tools(assistant.Ctx(channel="web", conversation_id=0))}
    for name in ("read_file", "list_files", "search", "edit_file", "write_file", "run_command"):
        assert cli[name] is assistant.TERMINAL_MAP[name]
        assert web.get(name) is not assistant.TERMINAL_MAP[name]
    assert {"path", "offset", "limit"} == set(cli["read_file"].params)
    assert "file" in web["read_file"].params  # the workspace's own read_file is untouched on the web


def test_a_cli_tool_call_is_delegated_with_its_arguments(client, bridge):
    from app import assistant

    _, cid = bridge
    ctx = assistant.Ctx(channel="cli", conversation_id=cid)

    async def go():
        with pytest.raises(assistant.DelegationRequired) as e:
            await assistant.run_tool("edit_file", json.dumps({"path": "a.py", "old_text": "x", "new_text": "y"}), ctx)
        return e.value

    e = asyncio.run(go())
    assert e.action_id > 0 and e.tool == "edit_file" and e.effect == "write"
    assert e.tool_args == {"path": "a.py", "old_text": "x", "new_text": "y"}
    assistant._PENDING_DELEGATIONS.pop(e.action_id, None)


def test_the_shared_loop_waits_for_the_terminal_and_continues_with_its_result(client, bridge):
    """run_turn streams tool_delegation, the terminal posts /v1/rooks/tool_result, the model sees that result."""
    from app import assistant, db, rooks_link

    _, cid = bridge
    SCRIPT[:] = [tool_call("read_file", {"path": "calc.py"}),
                 lambda msgs: {"role": "assistant", "content": "It subtracts." if msgs[-1]["role"] == "tool"
                               and "return a - b" in msgs[-1]["content"] else "MISSING TOOL RESULT"}]

    async def go():
        seen = []
        ctx = assistant.Ctx(channel="cli", conversation_id=cid)
        async for e in assistant.run_turn(ctx, "what does calc.py do", None):
            seen.append(e)
            if e["type"] == "tool_delegation":
                assert e["tool"] == "read_file" and e["args"] == {"path": "calc.py"}
                # the terminal answers over HTTP; the endpoint must find the waiting future (it used to be popped first)
                await rooks_link.tool_result(rooks_link.ToolResultIn(delegation_id=e["id"], ok=True,
                                                                     result="1 def add(a, b):\n2     return a - b"))
        return seen

    seen = asyncio.run(go())
    kinds = [e["type"] for e in seen]
    assert "tool_delegation" in kinds and "tool_result" in kinds
    assert next(e for e in seen if e["type"] == "message")["content"] == "It subtracts."
    row = db.one("SELECT outcome, evidence FROM actions WHERE kind = 'tool_call' AND tool = 'read_file' ORDER BY id DESC")
    assert row["outcome"] == "ok" and "return a - b" in row["evidence"]
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert any(m["role"] == "tool" and m["name"] == "read_file" for m in msgs)  # the app shows the step


def test_a_terminal_approval_shows_in_the_app_with_its_diff_and_the_app_can_answer(client, bridge):
    h, cid = bridge
    diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n+    return a + b\n"
    aid = client.post("/v1/rooks/approvals", headers=h, json={"conversation_id": cid, "tool": "edit_file", "effect": "write",
                                                              "summary": "edit calc.py", "preview": diff}).json()["id"]
    card = next(a for a in client.get(f"/api/approvals?conversation_id={cid}").json() if a["id"] == aid)
    assert card["status"] == "pending" and card["tool"] == "edit_file" and json.loads(card["args"])["diff"] == diff
    assert client.get(f"/v1/rooks/approvals/{aid}", headers=h).json()["status"] == "pending"
    # approved in the app: recorded, never run by the app itself
    assert client.post(f"/api/approvals/{aid}", json={"approve": True}).json()["status"] == "approved"
    assert client.get(f"/v1/rooks/approvals/{aid}", headers=h).json()["status"] == "approved"
    # the terminal's late answer does not override the app's
    r = client.post(f"/v1/rooks/approvals/{aid}", headers=h, json={"approve": False})
    assert r.status_code == 409
    assert client.get(f"/v1/rooks/approvals/{aid}", headers=h).json()["status"] == "approved"


def test_an_earlier_yes_never_approves_a_later_change(client, bridge):
    """The old poll approved any new terminal action once any approval in the conversation had been approved."""
    h, cid = bridge
    body = {"conversation_id": cid, "tool": "run_command", "effect": "command", "summary": "rm -rf build", "preview": "rm -rf build"}
    first = client.post("/v1/rooks/approvals", headers=h, json=body).json()["id"]
    client.post(f"/api/approvals/{first}", json={"approve": True})
    second = client.post("/v1/rooks/approvals", headers=h, json=body).json()["id"]
    assert second != first
    assert client.get(f"/v1/rooks/approvals/{second}", headers=h).json()["status"] == "pending"
    # answered in the terminal: the app's card closes
    assert client.post(f"/v1/rooks/approvals/{second}", headers=h, json={"approve": False}).json()["status"] == "denied"
    assert all(a["id"] != second for a in client.get(f"/api/approvals?conversation_id={cid}").json() if a["status"] == "pending")


def test_terminal_approval_endpoints_need_the_bridge_key_and_a_terminal_row(client, bridge):
    h, cid = bridge
    assert client.post("/v1/rooks/approvals", json={"conversation_id": cid, "tool": "x", "effect": "write"}).status_code == 401
    # a normal held chat action is not reachable through the terminal endpoints
    from app import actions

    held = actions.add("tool_call", "held", trace={"channel": "web", "conversation_id": cid}, tool="send_email", effect="external")
    assert client.get(f"/v1/rooks/approvals/{held}", headers=h).status_code == 404
    assert client.post(f"/v1/rooks/approvals/{held}", headers=h, json={"approve": True}).status_code == 404


def test_terminal_egress_audit_counts_external_hosts(client, bridge):
    from app import egress

    h, _ = bridge
    egress.record("POST", "https://api.example-llm.test/v1/chat/completions", "ok", 120, component="llm")
    egress.record("GET", "http://127.0.0.1:11434/api/tags", "ok", 3, component="ollama")
    d = client.get("/v1/rooks/egress", headers=h, params={"hours": 1}).json()
    assert d["local_only"] is False and d["external_requests"] >= 1
    assert any(x["host"] == "api.example-llm.test" and x["effect"] == "external" for x in d["hosts"])
    assert client.get("/v1/rooks/egress").status_code == 401
