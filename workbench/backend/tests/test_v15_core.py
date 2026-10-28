"""v15: one typed work lifecycle over every engine, the versioned client API (v1), safe retries, live state and the
approval audit pair. The browser, the terminal CLI and the desktop app all go through these routes."""

import json

import pytest

from conftest import CALLS, SCRIPT, events, tool_call


@pytest.fixture
def anon(client):
    from fastapi.testclient import TestClient

    from app import main

    return TestClient(main.app)


@pytest.fixture
def token(client):
    return client.post("/api/cli-token").json()["token"]


def bearer(t):
    return {"Authorization": f"Bearer {t}", "X-Workbench": "1"}


# The lifecycle
def test_every_native_status_of_every_engine_has_one_phase(client):
    from app import work

    for kind, k in work.KINDS.items():
        for native, (phase, _) in k.statuses.items():
            assert isinstance(phase, work.Phase), (kind, native)
    with pytest.raises(ValueError):
        work.phase_of("mission", "made-up")


def test_the_lifecycle_allows_the_normal_path_and_refuses_reopening_completed_work(client):
    from app.work import Phase as P, allowed

    path = [P.CREATED, P.PLANNING, P.AWAITING_APPROVAL, P.RUNNING, P.VERIFYING, P.COMPLETED]
    assert all(allowed(a, b) for a, b in zip(path, path[1:]))
    assert not allowed(P.COMPLETED, P.RUNNING) and not allowed(P.COMPLETED, P.CANCELLED)
    assert allowed(P.FAILED, P.RUNNING) and allowed(P.CANCELLED, P.RUNNING)  # retry
    assert not allowed(P.CANCELLED, P.COMPLETED)  # stopped work cannot become finished without running again


def test_every_status_change_is_recorded_in_the_same_transaction(client):
    from app import db, work

    rid = db.run("INSERT INTO research(question, model, status, created_at, updated_at) VALUES ('Lifecycle check', 'm', 'running', ?, ?)",
                 db.now(), db.now())
    db.run("UPDATE research SET status = 'error', error = 'x' WHERE id = ?", rid)
    db.run("UPDATE research SET progress = 'no status change' WHERE id = ?", rid)
    ev = work.events(f"research:{rid}")
    assert [(e["native_from"], e["native_to"]) for e in ev] == [(None, "running"), ("running", "error")]
    assert [e["to"] for e in ev] == ["running", "failed"]


# The versioned API
def test_meta_and_version_negotiation(client):
    m = client.get("/api/v1/meta").json()
    assert m["api_version"] == 1 and "awaiting_approval" in m["phases"]
    assert client.get("/api/v1/meta", headers={"X-Rooks-API": "1"}).status_code == 200
    r = client.get("/api/v1/meta", headers={"X-Rooks-API": "2"})
    assert r.status_code == 426 and "Update the ROOKS server" in r.json()["detail"]
    assert client.get("/api/v1/meta", headers={"X-Rooks-API": "x"}).status_code == 400


def test_the_terminal_token_reaches_v1_and_nothing_without_it(client, anon, token):
    assert anon.get("/api/v1/status").status_code == 401
    assert anon.get("/api/v1/status", headers=bearer("wrong")).status_code == 401
    s = anon.get("/api/v1/status", headers=bearer(token)).json()
    assert s["api_version"] == 1 and s["providers"] and s["model"]
    assert anon.get("/api/settings", headers=bearer(token)).status_code == 401  # Settings stay browser-only


def test_a_cli_conversation_is_the_browsers_conversation(client, anon, token):
    SCRIPT[:] = [{"role": "assistant", "content": "Three things matter today."}]
    ev = events(anon.post("/api/v1/chat", json={"message": "What should I work on today?", "channel": "cli"}, headers=bearer(token)))
    cid = next(e for e in ev if e["type"] == "start")["conversation_id"]
    assert any(e["type"] == "message" and "Three things" in e["content"] for e in ev)
    # the browser lists it and opens it, with the same messages
    assert any(c["id"] == cid for c in client.get("/api/conversations").json())
    msgs = client.get(f"/api/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs if m["content"]] == ["user", "assistant"]
    # and the browser's reply continues it for the CLI
    SCRIPT[:] = [{"role": "assistant", "content": "Start with the invoices."}]
    events(client.post("/api/assistant/chat", json={"message": "Which first?", "conversation_id": cid}))
    later = anon.get(f"/api/v1/conversations/{cid}", headers=bearer(token)).json()["messages"]
    assert later[-1]["content"] == "Start with the invoices." and len(later) == 4


def test_a_retried_request_is_answered_not_run_again(client):
    SCRIPT[:] = [tool_call("create_task", {"title": "Idempotent task"}), {"role": "assistant", "content": "Added it."}]
    before = len(CALLS)
    h = {"Idempotency-Key": "retry-test-1"}
    ev1 = events(client.post("/api/v1/chat", json={"message": "Add a task called Idempotent task"}, headers=h))
    n = len(CALLS) - before
    ev2 = events(client.post("/api/v1/chat", json={"message": "Add a task called Idempotent task"}, headers=h))
    assert len(CALLS) - before == n  # the model was not asked again
    assert ev2[0]["replayed"] and ev2[0]["conversation_id"] == ev1[0]["conversation_id"]
    assert any(e["type"] == "message" and e["content"] == "Added it." for e in ev2)
    from app import db

    assert db.one("SELECT COUNT(*) AS n FROM tasks WHERE title = 'Idempotent task'")["n"] == 1


# Work through v1
def test_work_is_listed_with_its_phase_and_can_be_cancelled_and_retried(client, monkeypatch):
    from app import api, db, research

    monkeypatch.setattr(research, "stop_now", lambda rid: None)
    monkeypatch.setattr(api, "_start_research", lambda *a: None)
    cid = client.post("/api/v1/chat", json={"message": "hello"}).text and \
        client.get("/api/v1/conversations").json()[0]["id"]
    rid = db.run("INSERT INTO research(question, model, status, progress, created_at, updated_at) VALUES ('Work API check', 'm', 'running', 'Searching', ?, ?)",
                 db.now(), db.now())
    db.run("INSERT INTO jobs(conversation_id, kind, ref_id, title, created_at, updated_at) VALUES (?, 'research', ?, 'Work API check', ?, ?)",
           cid, rid, db.now(), db.now())
    item = next(w for w in client.get(f"/api/v1/work?conversation_id={cid}").json() if w["id"] == f"research:{rid}")
    assert item["phase"] == "running" and item["status_text"] == "Working" and item["can_cancel"] and not item["can_retry"]
    assert client.post(f"/api/v1/work/research:{rid}/retry").status_code == 409  # running work cannot be retried
    client.post(f"/api/v1/work/research:{rid}/cancel")
    db.run("UPDATE research SET status = 'cancelled' WHERE id = ?", rid)  # what the engine does at its next checkpoint
    w = client.get(f"/api/v1/work/research:{rid}").json()
    assert w["phase"] == "cancelled" and w["can_retry"] and not w["can_cancel"]
    assert client.post(f"/api/v1/work/research:{rid}/cancel").status_code == 409
    assert client.post(f"/api/v1/work/research:{rid}/retry").json()["phase"] == "running"
    assert [e["to"] for e in w["events"]] == ["running", "cancelled"]
    assert client.get("/api/v1/work/nonsense:1").status_code == 404


def test_deciding_an_approval_twice_runs_it_once(client):
    from app import assistant, db

    cid = assistant.new_conversation("web")
    aid = db.run("INSERT INTO approvals(tool, args, reason, channel, conversation_id, scope, created_at) VALUES "
                 "('create_task', ?, 'test', 'web', ?, ?, ?)", json.dumps({"title": "Approved once"}), cid,
                 json.dumps({"allowed_tools": ["create_task"], "project_id": None}), db.now())
    assert any(a["id"] == aid for a in client.get("/api/v1/approvals").json())
    first = client.post(f"/api/v1/approvals/{aid}", json={"approve": True}).json()
    again = client.post(f"/api/v1/approvals/{aid}", json={"approve": True}).json()
    assert first["status"] == "approved" and again["already_decided"] and again["status"] == "approved"
    assert db.one("SELECT COUNT(*) AS n FROM tasks WHERE title = 'Approved once'")["n"] == 1


def test_an_approval_request_and_its_decision_are_both_audited(client):
    from app import db

    SCRIPT[:] = [tool_call("save_skill", {"name": "audited-skill", "description": "d", "steps": "1. x"}), {"role": "assistant", "content": "Asked."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "save it"}))
    aid = next(e for e in ev if e["type"] == "approval")["id"]
    client.post(f"/api/v1/approvals/{aid}", json={"approve": False})
    rows = db.all("SELECT action FROM audit_log WHERE detail LIKE ? OR target LIKE ?", f"request #{aid}:%", f"%request #{aid}%")
    assert {"approval asked", "deny"} <= {r["action"] for r in rows}


def test_live_state_opens_with_a_complete_baseline(client):
    ev = events(client.get("/api/v1/events?once=true"))
    b = ev[0]
    assert b["type"] == "baseline" and {"work", "conversations", "needs_review", "work_event", "conversations_at"} <= set(b)


def test_capabilities_say_what_is_set_up_and_what_asks_first(client):
    caps = {c["name"]: c for c in client.get("/api/v1/capabilities").json()}
    assert caps["create_note"]["state"] == "available" and caps["create_note"]["risk"] == "write"
    assert caps["github_create_issue"]["state"] == "needs_setup" and caps["github_create_issue"]["asks_first"]
    assert caps["read_email"]["outside_content"] and not caps["create_note"]["outside_content"]


# Files: what is shown and whether what is on disk is what was saved
def test_a_saved_file_is_checked_on_disk_and_a_changed_one_is_reported(client):
    from app import db, files

    fid = client.post("/api/files?name=ledger.csv", content=b"Item,Cost\nPaper,12\nInk,30\n", headers={"Content-Type": "text/csv"}).json()["id"]
    v = client.get(f"/api/v1/files/{fid}/verify").json()
    assert v["matches"] and v["exists"] and v["text"].startswith("Saved and checked")
    t = client.get(f"/api/v1/files/{fid}/table").json()
    assert t["header"] == ["Item", "Cost"] and t["total_rows"] == 2 and t["rows"][1] == ["Ink", "30"]
    path = files.root() / db.one("SELECT path FROM files WHERE id = ?", fid)["path"]
    path.write_bytes(b"Item,Cost\nPaper,999\n")  # changed behind ROOKS's back
    v = client.get(f"/api/v1/files/{fid}/verify").json()
    assert not v["matches"] and "differs" in v["text"]
    path.unlink()
    assert client.get(f"/api/v1/files/{fid}/verify").json()["exists"] is False


def test_only_pdfs_and_images_are_shown_inline(client):
    fid = client.post("/api/files?name=x.html", content=b"<script>1</script>", headers={"Content-Type": "text/html"}).json()["id"]
    assert client.get(f"/api/v1/files/{fid}/view").status_code == 415
    pid = client.post("/api/files?name=p.png", content=b"\x89PNG\r\n\x1a\nnot really", headers={"Content-Type": "image/png"}).json()["id"]
    r = client.get(f"/api/v1/files/{pid}/view")
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
