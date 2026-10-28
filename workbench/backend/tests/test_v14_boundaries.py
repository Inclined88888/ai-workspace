"""v14: an approval authorises exactly what was reviewed, in the context it was asked in; the assistant sees only what
its conversation's project boundary allows (scope.py). Each rule is tested both ways: allowed and refused."""

import asyncio
import json

import pytest

from conftest import SCRIPT, events, tool_call


def _approval(tool, args, conv, scope):
    from app import db

    return db.run("INSERT INTO approvals(tool, args, reason, channel, conversation_id, scope, created_at) VALUES (?, ?, 'test', 'web', ?, ?, ?)",
                  tool, json.dumps(args), conv, json.dumps(scope) if scope is not None else "", db.now())


def test_an_approval_cannot_run_a_tool_outside_the_requesters_tools(client):
    from app import assistant, db

    cid = assistant.new_conversation("web")
    aid = _approval("create_note", {"title": "Smuggled note"}, cid, {"allowed_tools": ["search_notes"], "project_id": None})
    r = client.post(f"/api/approvals/{aid}", json={"approve": True}).json()
    assert r["status"] == "failed" and "cannot add it" in r["result"]
    assert not db.one("SELECT id FROM docs WHERE title = 'Smuggled note'")


def test_an_approval_inside_the_requesters_tools_runs(client):
    from app import assistant, db

    cid = assistant.new_conversation("web")
    aid = _approval("create_note", {"title": "Approved note"}, cid, {"allowed_tools": ["create_note"], "project_id": None})
    assert client.post(f"/api/approvals/{aid}", json={"approve": True}).json()["status"] == "approved"
    assert db.one("SELECT id FROM docs WHERE title = 'Approved note'")


def test_older_approvals_without_a_recorded_scope_still_work(client):
    from app import assistant, db

    cid = assistant.new_conversation("web")
    aid = _approval("create_task", {"title": "From an old approval"}, cid, None)
    assert client.post(f"/api/approvals/{aid}", json={"approve": True}).json()["status"] == "approved"
    assert db.one("SELECT id FROM tasks WHERE title = 'From an old approval'")


def test_approved_does_not_bypass_a_workers_tools(client):
    from app import assistant

    ctx = assistant.Ctx(channel="mission", conversation_id=assistant.new_conversation("web"), approved=True,
                        allowed_tools=frozenset({"search_notes"}))
    ok, out = asyncio.run(assistant._run_tool("create_note", json.dumps({"title": "x"}), ctx))
    assert not ok and "Not allowed" in out


def test_an_approval_is_not_run_once_the_project_budget_is_used(client):
    from app import assistant, db

    pid = client.post("/api/projects", json={"name": "Spent project"}).json()["id"]
    db.run("UPDATE projects SET budget_cents = 1 WHERE id = ?", pid)
    db.run("INSERT INTO usage(ts, model, channel, conversation_id, cost_cents, priced) VALUES (?, 'm', 'web', ?, 5, 1)",
           db.now(), assistant.new_conversation("web", project_id=pid))
    cid = assistant.new_conversation("web", project_id=pid)
    aid = _approval("create_task", {"title": "Over budget task"}, cid, {"allowed_tools": None, "project_id": pid})
    r = client.post(f"/api/approvals/{aid}", json={"approve": True}).json()
    assert r["status"] == "failed" and "monthly budget" in r["result"]
    assert not db.one("SELECT id FROM tasks WHERE title = 'Over budget task'")


def test_a_new_approval_records_its_scope(client):
    from app import db

    SCRIPT[:] = [tool_call("save_skill", {"name": "scoped-skill", "description": "d", "steps": "1. x"}), {"role": "assistant", "content": "Asked."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "save it"}))
    aid = next(e for e in ev if e["type"] == "approval")["id"]
    scope = json.loads(db.one("SELECT scope FROM approvals WHERE id = ?", aid)["scope"])
    assert set(scope) >= {"allowed_tools", "project_id", "mission_id", "channel"} and scope["channel"] == "web"


_N = __import__("itertools").count(1)


@pytest.fixture
def projects_with_docs(client):
    from app import db

    n = next(_N)
    iso = client.post("/api/projects", json={"name": f"Private case {n}", "isolated": True}).json()["id"]
    open_ = client.post("/api/projects", json={"name": f"Open plan {n}"}).json()["id"]
    db.run("UPDATE docs SET body = 'old' WHERE body = 'zebrafinch details'")  # one set of matching notes per test

    def doc(title, pid):
        return db.run("INSERT INTO docs(kind, title, body, project_id, created_at, updated_at) VALUES ('note', ?, 'zebrafinch details', ?, ?, ?)",
                      title, pid, db.now(), db.now())
    ids = {"iso": doc("Isolated zebrafinch", iso), "open": doc("Open zebrafinch", open_), "none": doc("Loose zebrafinch", None)}
    ids["n"] = n
    return iso, open_, ids


def _search(cid):
    from app import assistant

    ctx = assistant.Ctx(channel="web", conversation_id=cid)
    ok, out = asyncio.run(assistant._run_tool("search_notes", json.dumps({"query": "zebrafinch"}), ctx))
    assert ok, out
    return {d["title"] for d in json.loads(out)}, ctx


def test_notes_respect_project_boundaries_both_ways(client, projects_with_docs):
    from app import assistant

    iso, open_, ids = projects_with_docs
    found, ctx = _search(assistant.new_conversation("web"))
    assert found == {"Open zebrafinch", "Loose zebrafinch"}  # outside projects: not the isolated one
    ok, out = asyncio.run(assistant._run_tool("read_note", json.dumps({"doc_id": ids["iso"]}), ctx))
    assert not ok and "another project" in out
    found, ctx = _search(assistant.new_conversation("web", project_id=iso))
    assert found == {"Isolated zebrafinch"}  # an isolated project: its own only
    ok, out = asyncio.run(assistant._run_tool("read_note", json.dumps({"doc_id": ids["iso"]}), ctx))
    assert ok and "zebrafinch" in out
    ok, out = asyncio.run(assistant._run_tool("append_to_note", json.dumps({"doc_id": ids["open"], "text": "x"}), ctx))
    assert not ok and "another project" in out
    found, _ = _search(assistant.new_conversation("web", project_id=open_))
    assert found == {"Open zebrafinch", "Loose zebrafinch"}  # a normal project: its own plus loose items


def test_files_respect_boundaries_but_an_attachment_here_is_readable(client, projects_with_docs):
    from app import assistant, db

    iso, _, _ = projects_with_docs
    fid = client.post("/api/files?name=secret.txt", content=b"budget figures for the case", headers={"Content-Type": "text/plain"}).json()["id"]
    db.run("UPDATE files SET project_id = ? WHERE id = ?", iso, fid)
    cid = assistant.new_conversation("web")
    ctx = assistant.Ctx(channel="web", conversation_id=cid)
    ok, out = asyncio.run(assistant._run_tool("read_file", json.dumps({"file_id": fid}), ctx))
    assert not ok and "another project" in out
    assistant._save(cid, "user", f"look at this\n\n[Attached file: secret.txt (file #{fid})]\nbudget figures")
    ok, out = asyncio.run(assistant._run_tool("read_file", json.dumps({"file_id": fid}), ctx))
    assert ok and "budget figures" in out


def test_past_conversations_and_project_status_respect_boundaries(client, projects_with_docs):
    from app import assistant

    iso, open_, ids = projects_with_docs
    n = ids["n"]
    secret = assistant.new_conversation("web", project_id=iso)
    assistant._save(secret, "user", "the quokkaplan is confidential")
    ctx = assistant.Ctx(channel="web", conversation_id=assistant.new_conversation("web"))
    ok, out = asyncio.run(assistant._run_tool("search_history", json.dumps({"query": "quokkaplan"}), ctx))
    assert ok and json.loads(out) == []
    inside = assistant.Ctx(channel="web", conversation_id=assistant.new_conversation("web", project_id=iso))
    ok, out = asyncio.run(assistant._run_tool("search_history", json.dumps({"query": "quokkaplan"}), inside))
    assert ok and json.loads(out)
    ok, out = asyncio.run(assistant._run_tool("project_status", json.dumps({"project": f"Private case {n}"}), ctx))
    assert ok and json.loads(out)["found"] is False
    ok, out = asyncio.run(assistant._run_tool("project_status", json.dumps({"project": f"Open plan {n}"}), inside))
    assert ok and json.loads(out)["found"] is False  # from inside one project, another project's status is not shown
    ok, out = asyncio.run(assistant._run_tool("project_status", json.dumps({"project": f"Private case {n}"}), inside))
    assert ok and json.loads(out)["found"] is True
