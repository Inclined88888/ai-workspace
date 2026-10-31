import asyncio
import io
import json
import time
import zipfile

from conftest import CALLS, SCRIPT, events, tool_call


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


def test_chat_runs_tools_and_answers(client):
    SCRIPT[:] = [
        tool_call("create_task", {"title": "Renew registration", "due": "2026-11-30", "priority": 3}),
        lambda msgs: {"role": "assistant", "content": "Added. " + ("ok" if msgs[-1]["role"] == "tool" and "Renew registration" in msgs[-1]["content"] else "MISSING TOOL RESULT")},
    ]
    ev = events(client.post("/api/assistant/chat", json={"message": "I'm a nurse in Sydney. Remind me to renew my registration by end of November"}))
    kinds = [e["type"] for e in ev]
    assert kinds[0] == "start" and "tool_call" in kinds and kinds[-1] == "done"
    final = next(e for e in ev if e["type"] == "message")["content"]
    assert final == "Added. ok"
    tasks = client.get("/api/tasks").json()
    assert any(t["title"] == "Renew registration" and t["due"] == "2026-11-30" for t in tasks)
    # tools were offered with JSON schemas, temperature never sent
    first = next(c for c in CALLS if c.get("tools"))
    assert {"create_task", "remember", "load_skill", "schedule_task"} <= {t["function"]["name"] for t in first["tools"]}
    assert "temperature" not in first


def test_zip_can_be_attached_to_chat(client):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("notes.txt", "The project deadline is Friday.")
    uploaded = client.post("/api/files/upload", params={"name": "project.zip"}, content=archive.getvalue())
    assert uploaded.status_code == 200, uploaded.text
    fid = uploaded.json()["id"]
    SCRIPT[:] = [lambda msgs: {"role": "assistant", "content": "I can see the attachment." if f"#{fid}" in msgs[-1]["content"] else "Missing attachment."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "", "file_ids": [fid]}))
    assert next(e for e in ev if e["type"] == "message")["content"] == "I can see the attachment."
    assert "notes.txt" in str(client.get(f"/api/files/{fid}").json()["extract"])
    assert client.post("/api/assistant/chat", json={"message": "", "file_ids": [999999]}).status_code == 404
    assert client.post("/api/assistant/chat", json={"message": ""}).status_code == 422


def test_memory_is_learned_and_used_next_time(client):
    # learned into the encrypted "What I know about you" store (adapt/knowledge.py), profile facts are pinned
    mems = wait_for(lambda: [m for m in client.get("/api/learn/facts").json()["facts"] if "nurse" in m["content"]])
    assert mems and mems[0]["category"] == "about" and mems[0]["pinned"]
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "hello"}]
    events(client.post("/api/assistant/chat", json={"message": "hi"}))
    system = next(c for c in CALLS if c.get("tools"))["messages"][0]["content"]
    assert "nurse in Sydney" in system  # profile memories are always in context


def test_skill_created_after_complex_task(client):
    SCRIPT[:] = [
        tool_call("get_agenda", {"days": 7}, "a"),
        tool_call("list_tasks", {}, "b"),
        tool_call("search_notes", {"query": "plan"}, "c"),
        {"role": "assistant", "content": "Here is your week."},
    ]
    events(client.post("/api/assistant/chat", json={"message": "Plan my week"}))
    skill = wait_for(lambda: [s for s in client.get("/api/skills").json() if s["name"] == "weekly-plan"])
    assert skill and "get_agenda" in skill[0]["steps"]
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    events(client.post("/api/assistant/chat", json={"message": "again"}))
    assert "weekly-plan" in next(c for c in CALLS if c.get("tools"))["messages"][0]["content"]


def test_tool_errors_go_back_to_the_model(client):
    SCRIPT[:] = [tool_call("read_note", {"doc_id": 99999}), {"role": "assistant", "content": "Could not find it."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "read note 99999"}))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"] is False and "Not found" in res["preview"]
    SCRIPT[:] = [tool_call("no_such_tool", {}), {"role": "assistant", "content": "done"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "x"}))
    assert next(e for e in ev if e["type"] == "tool_result")["preview"].startswith("Unknown tool")


def test_conversation_history_and_search(client):
    SCRIPT[:] = [{"role": "assistant", "content": "Noted the zebra project."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Remember the zebra project deadline"}))
    cid = ev[0]["conversation_id"]
    SCRIPT[:] = [lambda msgs: {"role": "assistant", "content": "history:" + str(sum(1 for m in msgs if m["role"] == "user"))}]
    ev = events(client.post("/api/assistant/chat", json={"message": "and?", "conversation_id": cid}))
    assert next(e for e in ev if e["type"] == "message")["content"] == "history:2"
    hits = client.get("/api/conversations", params={"q": "zebra"}).json()
    assert any(c["id"] == cid for c in hits)


def test_schedule_runs_and_delivers_to_inbox(client):
    r = client.post("/api/schedules", json={"name": "Daily report", "cron": "0 8 * * *", "prompt": "Write my daily report"})
    assert r.status_code == 200 and r.json()["next_run"]
    assert client.post("/api/schedules", json={"name": "bad", "cron": "every day", "prompt": "x x x"}).status_code == 400
    SCRIPT[:] = [lambda msgs: {"role": "assistant", "content": "Report: " + ("no schedule tool" if "schedule_task" not in json.dumps(msgs) else "")}]
    out = client.post(f"/api/schedules/{r.json()['id']}/run").json()["output"]
    assert out.startswith("Report")
    inbox = client.get("/api/inbox").json()
    assert inbox["unread"] >= 1 and inbox["items"][0]["title"] == "Daily report"
    # schedule runs cannot create schedules (no runaway loops)
    last = CALLS[-1] if not CALLS[-1].get("messages", [{}])[0].get("content", "").startswith("You maintain") else CALLS[-2]
    assert "schedule_task" not in {t["function"]["name"] for t in last.get("tools", [])}


def test_timezone_validation(client):
    assert client.put("/api/assistant/settings", json={"timezone": "Mars/Base"}).status_code == 400
    assert client.put("/api/assistant/settings", json={"timezone": "Australia/Sydney"}).json()["timezone"] == "Australia/Sydney"


def test_company_delegation_and_budget_pause(client):
    from app import company, db

    ceo = client.post("/api/company/agents", json={"name": "Ava", "title": "CEO", "monthly_budget_cents": 0, "monthly_token_limit": 100000}).json()
    eng = client.post("/api/company/agents", json={"name": "Kai", "title": "Engineer", "reports_to": ceo["id"], "monthly_token_limit": 1000}).json()
    assert client.put(f"/api/company/agents/{ceo['id']}", json={"name": "Ava", "reports_to": eng["id"]}).status_code == 400  # loop
    client.put("/api/company/mission", json={"mission": "Build the best nurse rostering app"})
    t = client.post("/api/company/tasks", json={"title": "Draft the roadmap", "agent_id": ceo["id"], "start": True}).json()
    assert t["status"] == "queued"
    SCRIPT[:] = [
        lambda msgs: (tool_call("delegate_task", {"agent_name": "Kai", "title": "Estimate effort", "description": "rough"})
                      if "nurse rostering" in msgs[0]["content"] and "Ava" in msgs[0]["content"] else {"role": "assistant", "content": "NO CONTEXT"}),
        {"role": "assistant", "content": "Roadmap drafted; Kai is estimating."},
    ]
    asyncio.run(company.run_task(t["id"]))
    tasks = {x["title"]: x for x in client.get("/api/company").json()["tasks"]}
    assert tasks["Draft the roadmap"]["status"] == "done"
    assert tasks["Estimate effort"]["status"] == "queued" and tasks["Estimate effort"]["agent_name"] == "Kai"
    # Budgets are checked before each model call, so one call (1500 tokens) may overshoot the 1000 limit; the next check pauses him
    SCRIPT[:] = [tool_call("list_tasks", {}), {"role": "assistant", "content": "never reached"}]
    asyncio.run(company.run_task(tasks["Estimate effort"]["id"]))
    kai = db.one("SELECT * FROM agents WHERE id = ?", eng["id"])
    assert kai["status"] == "paused" and "token limit" in kai["paused_reason"]
    assert db.one("SELECT status FROM agent_tasks WHERE id = ?", tasks["Estimate effort"]["id"])["status"] == "blocked"
    r = client.post(f"/api/company/agents/{eng['id']}/status", json={"status": "active"})
    assert r.status_code == 400 and "over budget" in r.json()["detail"].lower()


def test_usage_and_prices(client):
    s = client.get("/api/usage").json()
    assert s["month_tokens"] > 0 and s["month_cents"] == 0
    client.put("/api/usage/prices", json={"prices": {"fake-model": {"in": 3, "out": 15}}})
    SCRIPT[:] = [{"role": "assistant", "content": "priced"}]
    events(client.post("/api/assistant/chat", json={"message": "cost?"}))
    # 1000 in * $3/M + 500 out * $15/M = $0.0105 = 1.05 cents per call
    assert client.get("/api/usage").json()["month_cents"] >= 1.05


def test_cli_token_auth(client):
    from fastapi.testclient import TestClient

    from app import main

    token = client.post("/api/cli-token").json()["token"]
    anon = TestClient(main.app)
    assert anon.get("/api/memories").status_code == 401
    assert anon.get("/api/memories", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    assert anon.get("/api/memories", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_web_search_tool_only_offered_when_search_is_set_up(monkeypatch):
    from app import assistant, search

    monkeypatch.setattr(search, "provider", lambda: "")
    assert not assistant.TOOL_MAP["web_search"].when(None)
    monkeypatch.setattr(search, "provider", lambda: "brave")
    assert assistant.TOOL_MAP["web_search"].when(None)
