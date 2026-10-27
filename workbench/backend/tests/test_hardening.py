"""Loop and time limits, tool permissions, secrets kept out of memory and the audit log, memory updates, and
fetches that cannot be pointed at internal addresses."""

import asyncio
import json
import threading

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from conftest import SCRIPT, _port, events, tool_call


def chat(client, message, cid=None):
    ev = events(client.post("/api/assistant/chat", json={"message": message, "conversation_id": cid}))
    return ev, next(e for e in ev if e["type"] == "start")["conversation_id"]


# Loop and time limits
def test_identical_tool_calls_are_refused_then_the_turn_stops(client):
    SCRIPT[:] = [tool_call("list_tasks", {"include_done": False}, f"c{i}") for i in range(6)]
    ev, _ = chat(client, "What are my tasks?")
    results = [e for e in ev if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [True, True, False, False]
    assert "already called list_tasks" in results[2]["preview"]
    final = next(e for e in ev if e["type"] == "message")["content"]
    assert "repeating the same step" in final
    SCRIPT.clear()


def test_argument_order_does_not_hide_a_loop(client):
    from app.assistant import _canonical_args

    assert _canonical_args('{"a": 1, "b": 2}') == _canonical_args('{"b":2,"a":1}')
    assert _canonical_args("not json") == "not json"


def test_turn_time_budget(client, monkeypatch):
    from app import assistant

    monkeypatch.setattr(assistant, "TURN_SECONDS", 0)
    SCRIPT[:] = [{"role": "assistant", "content": "should not be reached"}]
    ev, _ = chat(client, "Plan my week")
    assert "ran out of time" in next(e for e in ev if e["type"] == "message")["content"]
    SCRIPT.clear()


# Permissions
def test_permission_off_hides_and_refuses(client):
    from app import assistant

    try:
        r = client.put("/api/permissions", json={"groups": {"tasks": "off"}})
        assert r.status_code == 200
        assert next(g for g in r.json()["groups"] if g["id"] == "tasks")["mode"] == "off"
        names = {t.name for t in assistant.available_tools(assistant.Ctx(channel="web", conversation_id=0))}
        assert "list_tasks" not in names and "create_task" not in names and "search_notes" in names
        # a model naming a hidden tool anyway is refused by the backend
        ok, out = asyncio.run(assistant.run_tool("create_task", json.dumps({"title": "x"}), assistant.Ctx(channel="web", conversation_id=0)))
        assert not ok and "turned off" in out
    finally:
        client.put("/api/permissions", json={"groups": {"tasks": "on"}})


def test_permission_ask_gates_writes_but_not_reads(client):
    try:
        client.put("/api/permissions", json={"groups": {"tasks": "ask"}})
        SCRIPT[:] = [tool_call("list_tasks", {}, "l"), tool_call("create_task", {"title": "Call the accountant"}, "t"),
                     {"role": "assistant", "content": "waiting"}]
        ev, _ = chat(client, "Add a task to call the accountant")
        approvals = [e for e in ev if e["type"] == "approval"]
        assert [a["name"] for a in approvals] == ["create_task"]
        assert next(e for e in ev if e["type"] == "tool_result" and e["name"] == "list_tasks")["ok"]
        assert not [t for t in client.get("/api/tasks").json() if t["title"] == "Call the accountant"]
        assert client.post(f"/api/approvals/{approvals[0]['id']}", json={"approve": True}).json()["status"] == "approved"
        assert [t for t in client.get("/api/tasks").json() if t["title"] == "Call the accountant"]
    finally:
        client.put("/api/permissions", json={"groups": {"tasks": "on"}})
        SCRIPT.clear()


def test_permission_input_is_validated(client):
    assert client.put("/api/permissions", json={"groups": {"tasks": "always"}}).status_code == 400
    assert client.put("/api/permissions", json={"groups": {"shell": "on"}}).status_code == 400


def test_every_tool_has_a_permission_group():
    from app import assistant, permissions

    assert {t.name for t in assistant.TOOLS} <= set(permissions.TOOL_GROUP)


# Secrets never become memories
@pytest.mark.parametrize("text", [
    "My OpenAI key is sk-proj-abcdefghijklmnopqrstuvwxyz123456",
    "anthropic sk-ant-api03-abcdefghijklmnopqrstuvwxyz",
    "card 4111 1111 1111 1111 exp 12/29",
    "my password is hunter2!",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "github ghp_abcdefghijklmnopqrstuvwxyz0123456789",
    "AKIAABCDEFGHIJKLMNOP",
])
def test_sensitive_values_are_found(text):
    from app import sensitive

    assert sensitive.find(text)
    assert sensitive.find(sensitive.redact(text)) == "" or "removed" in sensitive.redact(text)


@pytest.mark.parametrize("text", [
    "Lives in Brisbane and works as a nurse",
    "Phone 0412 345 678",
    "Order number 1234567890123",          # 13 digits that fail the Luhn check
    "Prefers meetings after 10am",
    "Password manager of choice is Bitwarden",
])
def test_ordinary_facts_are_kept(text):
    from app import sensitive

    assert sensitive.find(text) == ""


def test_memory_routes_refuse_secrets(client):
    assert client.post("/api/memories", json={"content": "Bank card 4111111111111111", "kind": "fact"}).status_code == 400
    r = client.post("/api/memories/import", json={"memories": [
        {"content": "token is abcdefghijklmnop1234", "kind": "fact"}, {"content": "Has two dogs named Rex and Moss", "kind": "fact"}]})
    assert r.json()["added"] == 1
    contents = [m["content"] for m in client.get("/api/memories").json()]
    assert "Has two dogs named Rex and Moss" in contents and not any("abcdefghijklmnop1234" in c for c in contents)

    SCRIPT[:] = [tool_call("remember", {"content": "Their Tavily key is tvly-abcdefghijklmnopqrstu"}, "r"),
                 {"role": "assistant", "content": "ok"}]
    ev, _ = chat(client, "remember my tavily key")
    res = next(e for e in ev if e["type"] == "tool_result" and e["name"] == "remember")
    assert not res["ok"] and "never keeps secrets" in res["preview"]
    assert not any("tvly-" in m["content"] for m in client.get("/api/memories").json())


def test_mining_updates_instead_of_contradicting(client, monkeypatch):
    from app import db, learn

    mid = client.post("/api/memories", json={"content": "Lives in Sydney with a partner", "kind": "profile"}).json()["id"]
    cid = db.run("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES ('Move', 'web', ?, ?)", db.now(), db.now())
    db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)", cid,
           "We finally moved to Brisbane last month, the whole house is in boxes still.", db.now())

    async def fake_json(conv, prompt, payload, channel):
        assert f"[id {mid}]" in payload
        return [{"content": "Lives in Brisbane with a partner", "kind": "profile", "replaces": mid},
                {"content": "Password is Winter2026", "kind": "fact"},
                {"content": "Overwrites something unseen", "kind": "fact", "replaces": 999999}]

    monkeypatch.setattr(learn, "_model_json", fake_json)
    # an id the model was not shown is never overwritten: that item is added as a new memory instead
    assert asyncio.run(learn.mine_memories(cid)) == 2
    row = db.one("SELECT * FROM memories WHERE id = ?", mid)
    assert row["content"] == "Lives in Brisbane with a partner" and row["previous"] == "Lives in Sydney with a partner"
    assert row["conversation_id"] == cid
    assert not db.one("SELECT 1 FROM memories WHERE content LIKE '%Winter2026%'")
    assert db.one("SELECT previous FROM memories WHERE content = 'Overwrites something unseen'")["previous"] == ""


# Audit trail
def test_audit_records_actions_without_secret_values(client):
    from app import audit

    client.put("/api/assistant/settings", json={"tavily_api_key": "tvly-secretvalue1234567890"})
    client.put("/api/assistant/settings", json={"tavily_api_key": ""})
    client.put("/api/permissions", json={"groups": {"browser": "ask"}})
    client.put("/api/permissions", json={"groups": {"browser": "on"}})
    assert client.post("/api/login", json={"password": "wrong password here"}).status_code == 401
    audit.record("assistant", "tool", "create_note", 'args {"body": "key sk-proj-abcdefghijklmnopqrstuvwxyz99"}')

    rows = client.get("/api/audit").json()
    text = json.dumps(rows)
    assert "tvly-secretvalue" not in text and "sk-proj-abcdefghijklmnopqrstuvwxyz99" not in text
    actions = {(r["action"], r["target"]) for r in rows}
    assert ("key saved", "TAVILY_API_KEY") in actions and ("key removed", "TAVILY_API_KEY") in actions
    assert ("permission", "browser") in actions
    assert any(r["action"] == "sign in" and not r["ok"] for r in rows)
    assert client.get("/api/audit", params={"action": "permission"}).json()[0]["action"] == "permission"


def test_tool_writes_and_approvals_are_audited(client):
    SCRIPT[:] = [tool_call("create_note", {"title": "Audit me", "body": "hello"}, "n"), {"role": "assistant", "content": "ok"}]
    chat(client, "Make a note called Audit me")
    rows = client.get("/api/audit", params={"action": "tool"}).json()
    assert any(r["target"] == "create_note" and r["actor"] == "assistant" for r in rows)
    # reads are not logged as actions
    assert not any(r["target"] in ("list_tasks", "search_notes") for r in rows)
    SCRIPT.clear()


def test_connections_overview(client):
    items = client.get("/api/connections").json()
    ids = {i["id"] for i in items}
    assert f"provider:{client.pid}" in ids and "search" in ids and "browser" in ids
    assert all(i["status"] in ("ok", "warn", "error", "off") for i in items)
    assert "x" not in [i.get("detail") for i in items]  # the provider's key never appears


# Fetching pages
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/latest/meta-data/", "http://[::ffff:127.0.0.1]/", "http://10.1.2.3/",
    "http://100.64.0.1/", "http://0.0.0.0/", "http://[::1]/", "http://user:pw@93.184.215.14/", "file:///etc/passwd",
    "ftp://93.184.215.14/",
])
def test_internal_addresses_are_refused(url):
    from app import research

    assert research._public_host(url) is False


def test_dns_rebinding_is_caught_after_connect(client, monkeypatch):
    """The name check passes (as a rebinding DNS answer would), but the connection lands on 127.0.0.1."""
    from app import research

    app = FastAPI()
    app.get("/")(lambda: HTMLResponse("<html><body><article><p>" + "internal secret " * 50 + "</p></article></body></html>"))
    port = _port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    try:
        monkeypatch.setattr(research, "_public_host", lambda url: True)
        monkeypatch.setattr(research, "_proxied", lambda scheme: False)

        async def go():
            for _ in range(50):
                try:
                    async with httpx.AsyncClient(trust_env=False) as c:
                        await c.get(f"http://127.0.0.1:{port}/")
                    break
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
            async with httpx.AsyncClient(trust_env=False) as c:
                return await research.fetch_text(c, f"http://127.0.0.1:{port}/")

        assert asyncio.run(go()) == ""
        # the same page is read when the peer is allowed, so the empty result above is the peer check
        monkeypatch.setattr(research, "_blocked_ip", lambda ip: False)
        assert "internal secret" in asyncio.run(go())
    finally:
        server.should_exit = True


def test_interrupted_research_can_be_retried(client, monkeypatch):
    from app import api, db, research

    ran = []

    async def fake_run(rid, question, model, depth):
        ran.append((rid, question, depth))
        db.run("UPDATE research SET status = 'done', report = 'ok' WHERE id = ?", rid)

    monkeypatch.setattr(research, "run", fake_run)
    rid = db.run("INSERT INTO research(question, model, depth, status, created_at, updated_at) VALUES ('Why is the sky blue', 'x/y', 5, 'running', ?, ?)",
                 db.now(), db.now())
    api.recover_interrupted()
    row = client.get(f"/api/research/{rid}").json()
    assert row["status"] == "error" and "Retry" in row["error"]
    assert client.post(f"/api/research/{rid}/retry").json()["status"] == "running"
    for _ in range(50):
        if ran:
            break
        client.get("/healthz")
    assert ran == [(rid, "Why is the sky blue", 5)]
    assert client.post(f"/api/research/{rid}/retry").status_code == 409  # only failed jobs


def test_migrations_are_additive_and_repeatable(client):
    from app import db

    db.init()
    db.init()
    cols = {r["name"] for r in db.all("PRAGMA table_info(memories)")}
    assert "previous" in cols


def test_starting_research_works(client, monkeypatch):
    """Regression: the route used to be synchronous and crashed with 'no running event loop'."""
    from app import research

    async def fake_run(rid, question, model, depth):
        return None

    monkeypatch.setattr(research, "run", fake_run)
    r = client.post("/api/research", json={"question": "Why is the sky blue?", "depth": 2})
    assert r.status_code == 200 and r.json()["depth"] == 2


def test_outside_tool_clients_follow_permissions(client):
    """Open WebUI's tool server and MCP clients cannot ask for approval: 'ask' lets them read but not write."""
    from app import mcp_server

    h = {"Authorization": "Bearer " + "k" * 30}
    try:
        assert client.post("/tools/tasks", json={"title": "From Open WebUI"}, headers=h).status_code == 200
        client.put("/api/permissions", json={"groups": {"tasks": "ask"}})
        assert client.get("/tools/tasks", headers=h).status_code == 200
        r = client.post("/tools/tasks", json={"title": "Blocked write"}, headers=h)
        assert r.status_code == 403 and "Permissions" in r.json()["detail"]
        client.put("/api/permissions", json={"groups": {"tasks": "off"}})
        assert client.get("/tools/tasks", headers=h).status_code == 403
        assert client.get("/tools/openapi.json", headers=h).status_code == 200  # the spec itself stays readable
        with pytest.raises(Exception, match="Not allowed"):
            mcp_server.create_task("From MCP")
        with pytest.raises(Exception, match="Not allowed"):
            mcp_server.list_tasks()
    finally:
        client.put("/api/permissions", json={"groups": {"tasks": "on"}})
    rows = client.get("/api/audit").json()
    assert any(r["actor"] == "openwebui" and r["action"] == "tool" and r["target"] == "create_task" for r in rows)
    assert any(r["actor"] == "mcp" and r["action"] == "tool refused" for r in rows)


def test_every_outside_tool_has_a_permission_group():
    from app import permissions, tools

    ops = {getattr(r, "operation_id", None) for r in tools.tools.routes} - {None}
    assert ops and ops <= set(permissions.TOOL_GROUP)


def test_background_learning_follows_memory_and_skill_permissions(client, monkeypatch):
    """Regression: automatic learning used to write memories and skills even when those permissions were off."""
    from app import assistant, db, learn

    cid = db.run("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES ('Perm', 'web', ?, ?)", db.now(), db.now())
    db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, 'user', ?, ?)", cid,
           "I have started learning the cello on Saturday mornings with a teacher called Ana.", db.now())

    async def fake_json(conv, prompt, payload, channel):
        return [{"content": "Learns the cello on Saturday mornings", "kind": "fact"}]

    monkeypatch.setattr(learn, "_model_json", fake_json)
    try:
        client.put("/api/permissions", json={"groups": {"memory": "off", "skills": "off"}})
        assert asyncio.run(learn.mine_memories(cid)) == 0
        assert asyncio.run(learn.mine_skill(cid)) == ""
        assert assistant.recall("cello") == []
        assert "Your saved skills" not in assistant.system_prompt(assistant.Ctx(channel="web", conversation_id=cid), "hi")

        client.put("/api/permissions", json={"groups": {"memory": "ask"}})
        before = {a["id"] for a in client.get("/api/approvals").json()}
        assert asyncio.run(learn.mine_memories(cid)) == 0
        assert not db.one("SELECT 1 FROM memories WHERE content LIKE '%cello%'")
        new = [a for a in client.get("/api/approvals").json() if a["id"] not in before]
        assert [a["tool"] for a in new] == ["remember"] and "cello" in new[0]["args"]
        assert client.post(f"/api/approvals/{new[0]['id']}", json={"approve": True}).json()["status"] == "approved"
        assert db.one("SELECT 1 FROM memories WHERE content LIKE '%cello%'")
    finally:
        client.put("/api/permissions", json={"groups": {"memory": "on", "skills": "on"}})
