"""Projects: memory isolation, the project brief, narrowed permissions, local-only, budgets, bin and permanent delete,
context compression, and mission dependencies, review and recovery inside projects. The model is the fake test server."""

import json

import pytest

from conftest import CALLS, SCRIPT, events, tool_call
from test_missions import Model, use, wait


def new(client, name, **kw):
    r = client.post("/api/projects", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def chat(client, text, **kw):
    return events(client.post("/api/assistant/chat", json={"message": text, **kw}))


def system_of_last_chat() -> str:
    return next(c for c in reversed(CALLS) if c.get("tools"))["messages"][0]["content"]


@pytest.fixture()
def clean(client):
    from app import db

    yield client
    SCRIPT.clear()
    for p in db.all("SELECT id FROM projects"):
        db.run("UPDATE conversations SET project_id = NULL WHERE project_id = ?", p["id"])
        db.run("UPDATE missions SET project_id = NULL WHERE project_id = ?", p["id"])
        db.run("DELETE FROM memories WHERE project_id = ?", p["id"])
        db.run("DELETE FROM project_notes WHERE project_id = ?", p["id"])
    db.run("DELETE FROM projects")
    client.put("/api/settings", json={})


def test_memories_saved_in_a_project_stay_in_it(clean):
    a, b = new(clean, "Kitchen reno"), new(clean, "Tax return")
    SCRIPT[:] = [tool_call("remember", {"content": "The tiler quoted 4200 dollars for the splashback"}), {"role": "assistant", "content": "noted"}]
    ev = chat(clean, "remember the tiler quote", project_id=a["id"])
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"] and "this project" in res["preview"]
    SCRIPT[:] = [tool_call("remember", {"content": "Prefers answers in bullet points", "kind": "preference"}), {"role": "assistant", "content": "ok"}]
    chat(clean, "I like bullets", project_id=a["id"])

    def recalled(**kw):
        ev = chat(clean, "what did the tiler quote for the splashback", **kw)
        return [m["content"] for m in next(e for e in ev if e["type"] == "memories")["items"]]

    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    assert any("tiler" in m for m in recalled(project_id=a["id"]))
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    in_b = recalled(project_id=b["id"])
    assert not any("tiler" in m for m in in_b) and any("bullet" in m for m in in_b)  # preferences are user wide
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    assert not any("tiler" in m for m in recalled())
    # search_memory and forget cannot reach into another project either
    tiler = next(m for m in clean.get("/api/memories", params={"project_id": a["id"]}).json() if "tiler" in m["content"])
    SCRIPT[:] = [tool_call("search_memory", {"query": "tiler splashback"}), tool_call("forget", {"memory_id": tiler["id"]}, "c2"),
                 {"role": "assistant", "content": "x"}]
    ev = chat(clean, "find and forget the tiler", project_id=b["id"])
    results = [e for e in ev if e["type"] == "tool_result"]
    assert "tiler" not in results[0]["preview"] and not results[1]["ok"]
    assert any(m["id"] == tiler["id"] for m in clean.get("/api/memories").json())  # the memory page sees everything
    assert clean.get("/api/memories", params={"q": "tiler"}).json()


def test_isolated_project_does_not_see_user_wide_facts(clean):
    from app import db

    db.run("INSERT INTO memories(content, kind, source, confidence, created_at, updated_at) VALUES "
           "('Client Acme pays invoices late', 'fact', 'user', 1.0, ?, ?)", db.now(), db.now())
    iso = new(clean, "Confidential bid", isolated=True)
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    items = next(e for e in chat(clean, "does Acme pay invoices late", project_id=iso["id"]) if e["type"] == "memories")["items"]
    assert not any("Acme" in m["content"] for m in items)
    SCRIPT[:] = [{"role": "assistant", "content": "x"}]
    items = next(e for e in chat(clean, "does Acme pay invoices late") if e["type"] == "memories")["items"]
    assert any("Acme" in m["content"] for m in items)
    db.run("DELETE FROM memories WHERE content = 'Client Acme pays invoices late'")


def test_brief_notes_and_linked_work(clean):
    p = new(clean, "Launch site", goals="Live by March", instructions="Always write in British English")
    clean.post(f"/api/projects/{p['id']}/notes", json={"kind": "question", "text": "Which hosting provider?"})
    SCRIPT[:] = [tool_call("project_note", {"kind": "decision", "text": "Use Hugo for the static site"}),
                 tool_call("create_task", {"title": "Buy the domain"}, "c2"), {"role": "assistant", "content": "done"}]
    chat(clean, "we decided on Hugo; add a task to buy the domain", project_id=p["id"])
    system = system_of_last_chat()
    assert 'project "Launch site"' in system and "British English" in system and "Which hosting provider?" in system
    o = clean.get(f"/api/projects/{p['id']}").json()
    assert [d["text"] for d in o["decisions"]] == ["Use Hugo for the static site"]
    assert [t["title"] for t in o["tasks"]] == ["Buy the domain"] and len(o["conversations"]) == 1
    assert o["timeline"]
    # the next turn's brief carries the decision and the task
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(clean, "where are we", conversation_id=o["conversations"][0]["id"])
    assert "Use Hugo" in system_of_last_chat() and "Buy the domain" in system_of_last_chat()
    # outside a project there is no brief and no project_note tool
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(clean, "hello")
    last = next(c for c in reversed(CALLS) if c.get("tools"))
    assert "Launch site" not in last["messages"][0]["content"] and "project_note" not in json.dumps(last["tools"])


def test_project_can_narrow_permissions_but_never_widen(clean):
    p = new(clean, "Read only research", permissions={"tasks": "off", "notes": "ask"})
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(clean, "hi", project_id=p["id"])
    names = json.dumps(next(c for c in reversed(CALLS) if c.get("tools"))["tools"])
    assert "create_task" not in names and "create_note" in names
    SCRIPT[:] = [tool_call("create_note", {"title": "Findings"}), {"role": "assistant", "content": "waiting"}]
    ev = chat(clean, "write it up", project_id=p["id"])
    assert [e["name"] for e in ev if e["type"] == "approval"] == ["create_note"]
    assert "this project's settings" in clean.get("/api/approvals").json()[0]["reason"]
    # a project saying "on" does not override Settings saying "off"
    clean.put("/api/permissions", json={"groups": {"tasks": "off"}})
    p2 = new(clean, "Wide open", permissions={"tasks": "on"})
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(clean, "hi", project_id=p2["id"])
    assert "create_task" not in json.dumps(next(c for c in reversed(CALLS) if c.get("tools"))["tools"])
    clean.put("/api/permissions", json={"groups": {"tasks": "on"}})
    assert clean.post("/api/projects", json={"name": "Bad", "permissions": {"tasks": "always"}}).status_code == 400


def test_local_only_project_never_reaches_a_cloud_model(clean, monkeypatch):
    from app import llm

    p = new(clean, "Patient notes", local_only=True)
    monkeypatch.setattr(llm, "is_local", lambda ref: False)  # pretend the test model is a cloud service
    n = len(CALLS)
    ev = chat(clean, "summarise the notes", project_id=p["id"])
    err = next(e for e in ev if e["type"] == "error")["error"]
    assert "local models only" in err and len(CALLS) == n  # refused before anything was sent
    monkeypatch.undo()
    SCRIPT[:] = [{"role": "assistant", "content": "local answer"}]
    ev = chat(clean, "summarise the notes", project_id=p["id"])
    assert next(e for e in ev if e["type"] == "message")["content"] == "local answer"
    # moving the conversation out of the local-only project needs explicit confirmation
    cid = next(e for e in ev if e["type"] == "start")["conversation_id"]
    assert clean.patch(f"/api/conversations/{cid}", json={"project_id": 0}).status_code == 409
    assert clean.patch(f"/api/conversations/{cid}", json={"project_id": 0, "allow_cloud": True}).json()["project_id"] is None


def test_project_budget_stops_work(clean):
    from app import db

    p = new(clean, "Tiny budget", budget_cents=1)
    SCRIPT[:] = [{"role": "assistant", "content": "first"}]
    cid = next(e for e in chat(clean, "go", project_id=p["id"]) if e["type"] == "start")["conversation_id"]
    db.run("INSERT INTO usage(ts, channel, model, prompt_tokens, completion_tokens, cost_cents, conversation_id) VALUES (?, 'web', 'x', 0, 0, 5, ?)",
           db.now(), cid)
    n = len(CALLS)
    ev = chat(clean, "again", conversation_id=cid)
    assert "monthly budget" in next(e for e in ev if e["type"] == "error")["error"] and len(CALLS) == n
    assert clean.get(f"/api/projects/{p['id']}").json()["spend_cents"] >= 5


def test_archive_bin_restore_and_permanent_delete(clean):
    p = new(clean, "Old client")
    SCRIPT[:] = [tool_call("remember", {"content": "Old client prefers Friday calls"}), {"role": "assistant", "content": "ok"}]
    cid = next(e for e in chat(clean, "remember Friday", project_id=p["id"]) if e["type"] == "start")["conversation_id"]
    assert clean.post("/api/projects", json={"name": "old CLIENT"}).status_code == 409
    clean.post(f"/api/projects/{p['id']}/archive")
    assert not clean.get("/api/projects").json() and clean.get("/api/projects", params={"archived": True}).json()
    assert clean.post("/api/assistant/chat", json={"message": "hi", "project_id": p["id"]}).status_code == 409
    assert clean.post("/api/missions", json={"goal": "Call the old client", "project_id": p["id"]}).status_code == 409
    clean.delete(f"/api/projects/{p['id']}")
    assert [b["name"] for b in clean.get("/api/projects", params={"bin": True}).json()] == ["Old client"]
    clean.post(f"/api/projects/{p['id']}/restore")
    assert clean.get(f"/api/projects/{p['id']}").json()["memories"]  # nothing was lost by binning
    assert clean.post(f"/api/projects/{p['id']}/purge", json={"confirm": "Old client"}).status_code == 409  # bin first
    clean.delete(f"/api/projects/{p['id']}")
    assert clean.post(f"/api/projects/{p['id']}/purge", json={"confirm": "old client"}).status_code == 400  # exact name
    r = clean.post(f"/api/projects/{p['id']}/purge", json={"confirm": "Old client"}).json()
    assert r["memories_removed"] == 1
    conv = clean.get(f"/api/conversations/{cid}").json()
    assert conv["project_id"] is None and conv["messages"]  # the conversation is kept, unlinked
    assert clean.get(f"/api/projects/{p['id']}").status_code == 404


def test_old_messages_are_summarised_into_the_prompt(clean):
    from app import assistant, db, learn

    cid = assistant.new_conversation("web")
    for i in range(assistant.HISTORY_MESSAGES + 10):
        db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)", cid,
               "user" if i % 2 == 0 else "assistant", f"message {i}" + (" the budget is 9000 dollars" if i == 1 else ""), db.now())
    seen = {}

    def summariser(msgs):
        seen["payload"] = msgs[1]["content"]
        return {"role": "assistant", "content": "The user set the budget at 9000 dollars."}
    SCRIPT[:] = [summariser]
    import asyncio

    assert asyncio.run(learn.summarize(cid))
    assert "message 1 the budget" in seen["payload"] and f"message {assistant.HISTORY_MESSAGES + 5}" not in seen["payload"]
    conv = db.one("SELECT summary, summary_upto FROM conversations WHERE id = ?", cid)
    assert "9000" in conv["summary"] and conv["summary_upto"] > 0
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(clean, "what was the budget", conversation_id=cid)
    assert "Earlier in this conversation" in system_of_last_chat() and "9000 dollars" in system_of_last_chat()
    assert not asyncio.run(learn.summarize(cid))  # nothing new fell out of the window: no extra model call


def test_mission_in_project_dependencies_review_and_episode(clean):
    p = new(clean, "Office move")
    model = Model(json.dumps({"steps": [
        {"title": "Find the lease", "done_when": "The lease is quoted", "needs_tool": True},
        {"title": "Draft the notice", "done_when": "A note titled Notice exists", "needs_tool": True, "after": [1]},
        {"title": "Book movers", "done_when": "A task exists", "needs_tool": True, "after": [5, 3]}]}),
        {"Draft the notice": [("create_note", {"title": "Notice"})], "Book movers": [("create_task", {"title": "Book movers"})]})
    use(model)
    m = clean.post("/api/missions", json={"goal": "Plan the office move", "project_id": p["id"], "review_unverified": True,
                                          "max_attempts": 1}).json()
    m = wait(clean, m["id"], ("review",))
    assert [s["after"] for s in m["plan"]] == [[], [1], []]  # only earlier steps can be prerequisites
    assert "Office move" in next(c for c in reversed(CALLS) if c["messages"][0]["content"].startswith("You plan missions"))["messages"][1]["content"]
    clean.post(f"/api/missions/{m['id']}/start")
    m = wait(clean, m["id"], ("review_step",))
    assert m["plan"][0]["status"] == "unverified" and "accept" in m["error"]  # no tool used: the user decides
    assert clean.post(f"/api/missions/{m['id']}/start").status_code == 409
    clean.post(f"/api/missions/{m['id']}/steps/0/review", json={"accept": True, "note": "I checked the lease myself"})
    m = wait(clean, m["id"], ("done", "partial", "failed"))
    assert m["status"] == "done", m["events"][-5:]
    assert "Accepted by you" in m["plan"][0]["evidence"]
    o = clean.get(f"/api/projects/{p['id']}").json()
    assert any(x["id"] == m["id"] for x in o["missions"]) and any(t["title"] == "Book movers" for t in o["tasks"])
    episode = [mm for mm in o["memories"] if mm["kind"] == "episode"]
    assert episode and "Plan the office move" in episode[0]["content"]
    SCRIPT.clear()


def test_failed_prerequisite_skips_dependent_step(clean):
    model = Model(json.dumps({"steps": [
        {"title": "Read doc 999999", "done_when": "It is quoted", "needs_tool": True},
        {"title": "Summarise it", "done_when": "A note exists", "needs_tool": True, "after": [1]}]}),
        {"Read doc 999999": [("read_note", {"doc_id": 999999})]}, verdicts={"Read doc 999999": "not_done"})
    use(model)
    m = clean.post("/api/missions", json={"goal": "Summarise doc 999999", "auto_start": True, "max_attempts": 1}).json()
    m = wait(clean, m["id"], ("done", "partial", "failed"))
    assert m["status"] == "failed" and m["plan"][0]["attempts"] == 1
    assert not [c for c in model.calls if c[0] == "Summarise it"]


def test_budget_reservations_across_missions(clean):
    from app import db, missions

    from app.usage import month_start

    spent = db.one("SELECT COALESCE(SUM(cost_cents), 0) AS c FROM usage WHERE ts >= ?", month_start())["c"]
    db.set_setting("monthly_limit_cents", str(int(spent) + 100))  # 100 cents left this month
    try:
        a = db.run("INSERT INTO missions(goal, status, plan, budget_cents, used_cents, created_at, updated_at) "
                   "VALUES ('a', 'running', '[]', 70, 10, ?, ?)", db.now(), db.now())
        b = db.run("INSERT INTO missions(goal, status, plan, budget_cents, created_at, updated_at) VALUES ('b', 'review', ?, 50, ?, ?)",
                   json.dumps([missions._new_step("x", "y")]), db.now(), db.now())
        assert missions.reserved_cents() >= 60
        r = clean.post(f"/api/missions/{b}/start")
        assert r.status_code == 402 and "reserved by other running missions" in r.json()["detail"]
        clean.put(f"/api/missions/{b}/budget", json={"budget_cents": 20})
        db.run("UPDATE missions SET status = 'paused' WHERE id = ?", b)
        assert missions.reserve_check(missions._get(b)) is None
    finally:
        db.set_setting("monthly_limit_cents", "0")
        db.run("DELETE FROM missions WHERE id IN (?, ?)", a, b)


def test_recovery_tells_the_step_to_look_first(clean):
    from app import db, missions

    mid = db.run("INSERT INTO missions(goal, status, plan, created_at, updated_at) VALUES ('type the report', 'running', ?, ?, ?)",
                 json.dumps([{**missions._new_step("Type it", "typed"), "status": "running", "attempts": 1}]), db.now(), db.now())
    aid = db.run("INSERT INTO computer_actions(device_id, kind, status, mission_id, created_at) VALUES (0, 'type_text', 'dispatched', ?, ?)",
                 mid, db.now())
    try:
        missions.recover()
        m = missions._get(mid)
        assert m["status"] == "queued" and any("Observe the screen first" in n for n in m["plan"][0]["notes"])
        assert any("look at the screen" in e["text"] for e in missions.view(mid)["events"])
    finally:
        db.run("UPDATE missions SET status = 'cancelled' WHERE id = ?", mid)
        db.run("DELETE FROM computer_actions WHERE id = ?", aid)


def test_model_roles_need_evidence_and_are_used_exactly(clean):
    from app import db

    ref = f"{clean.pid}/fake-model"
    r = clean.put("/api/models/roles", json={"roles": {"planning": ref}})
    assert r.status_code == 400 and "JSON test" in r.json()["detail"]
    SCRIPT[:] = [{"role": "assistant", "content": '{"sum": 42, "word": "Blue"}'}]
    assert clean.post("/api/models/json-check", json={"ref": ref}).json()["ok"]
    assert CALLS[-1].get("max_tokens", CALLS[-1].get("max_completion_tokens")) is not None  # the test is capped
    roles = {x["role"]: x for x in clean.put("/api/models/roles", json={"roles": {"planning": ref}}).json()["roles"]}
    assert roles["planning"]["ref"] == ref and not roles["planning"]["problems"]
    # context length is not reported by the test provider, so long-context roles cannot be confirmed
    r = clean.put("/api/models/roles", json={"roles": {"long_context": ref}})
    assert r.status_code == 400 and "not reported" in r.json()["detail"]
    caps = clean.get("/api/models/capabilities", params={"ref": ref}).json()[0]["capabilities"]
    assert caps["json"]["state"] == "tested" and caps["context"]["state"] == "unknown" and caps["local"]["state"] == "tested"
    # a mission without its own model plans with the Planning model
    model = Model(json.dumps({"steps": [{"title": "Say hi", "done_when": "hi", "needs_tool": False}]}), {})
    use(model)
    m = clean.post("/api/missions", json={"goal": "Say hello to me"}).json()
    wait(clean, m["id"], ("review",))
    plan_call = next(c for c in reversed(CALLS) if c["messages"][0]["content"].startswith("You plan missions"))
    assert plan_call["model"] == "fake-model"
    clean.put("/api/models/roles", json={"roles": {"planning": ""}})
    db.set_setting(f"json_check:{ref}", "")
    SCRIPT.clear()


def test_template_creates_a_reviewed_plan_without_running(clean):
    ts = clean.get("/api/templates").json()
    assert {"spreadsheet-cleanup", "invoice-check", "email-drafts", "inventory"} <= {t["id"] for t in ts}
    assert clean.post("/api/templates/spreadsheet-cleanup/start", json={"values": {}}).status_code == 400  # path required
    p = new(clean, "Customers")
    m = clean.post("/api/templates/spreadsheet-cleanup/start", json={"values": {"path": "~/Documents/customers.csv", "columns": "Email"},
                                                                       "project_id": p["id"]}).json()
    assert m["status"] == "review" and m["template"] == "spreadsheet-cleanup" and m["project_id"] == p["id"]
    assert "customers.csv" in m["plan"][0]["title"] and "Email" in m["plan"][1]["title"] and m["plan"][2]["after"] == [2]
    assert m["review_unverified"] == 1
    assert not clean.get(f"/api/missions/{m['id']}").json()["plan"][0]["tools"]  # nothing ran
    clean.post(f"/api/missions/{m['id']}/cancel")
