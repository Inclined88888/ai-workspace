"""feat/v02-core: Projects with memory, Action history with undo, Checked answers. Offline: scripted fake model."""

import json

import pytest

from conftest import SCRIPT, events, tool_call


@pytest.fixture(autouse=True)
def quiet_learning(monkeypatch):
    """Background memory mining must not eat the scripted chat replies."""
    from app.adapt import knowledge

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(knowledge, "model_json", nothing)


@pytest.fixture
def clean(client):
    from app import db, projects

    projects.ensure()
    for t in ("project_items", "project_dismissed", "projects"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings WHERE key = 'active_project_id'")
    projects._invalidate()
    yield client


@pytest.fixture
def free_plan(monkeypatch):
    from app import features_v02

    monkeypatch.setattr(features_v02, "_account_allowed", lambda f: False)


def chat(client, message, cid=None):
    ev = events(client.post("/api/assistant/chat", json={"message": message, "conversation_id": cid}))
    return ev[0]["conversation_id"], ev


# Projects --------------------------------------------------------------------------------------------------------
def test_projects_crud_items_and_free_limit(clean, free_plan):
    c = clean
    p = c.post("/api/projects", json={"name": "Smith Street renovation", "kind": "job", "keywords": "SS-42, Jane Smith"}).json()
    assert p["name"] == "Smith Street renovation" and p["kind"] == "job"
    r = c.post("/api/projects", json={"name": "Second client"})
    assert r.status_code == 402 and r.json()["detail"]["error"] == "upgrade_required"
    assert "free plan" in r.json()["detail"]["message"].lower()
    lst = c.get("/api/projects").json()
    assert lst["limit"]["can_add"] is False and len(lst["projects"]) == 1
    pid = p["id"]
    c.post(f"/api/projects/{pid}/items", json={"kind": "person", "title": "Jane Smith", "body": "owner, 0400 000 000"})
    c.post(f"/api/projects/{pid}/items", json={"kind": "deadline", "title": "Council inspection", "due": "2026-11-20"})
    c.post(f"/api/projects/{pid}/items", json={"kind": "note", "body": "Gate code 4821"})
    d = c.get(f"/api/projects/{pid}").json()
    assert [x["title"] for x in d["items"]["person"]] == ["Jane Smith"]
    assert d["items"]["deadline"][0]["due"] == "2026-11-20"
    assert d["items"]["note"][0]["body"] == "Gate code 4821"
    from app import db

    raw = db.one("SELECT body_enc FROM project_items WHERE kind = 'note'")["body_enc"]
    assert "4821" not in raw  # notes are encrypted at rest
    # archiving frees the slot
    c.put(f"/api/projects/{pid}", json={"status": "archived"})
    assert c.post("/api/projects", json={"name": "Second client"}).status_code == 200


def test_pro_has_unlimited_projects(clean):
    for n in ("Alpha", "Bravo", "Charlie"):
        assert clean.post("/api/projects", json={"name": n}).status_code == 200
    assert clean.get("/api/projects").json()["limit"]["unlimited"] is True


def test_chat_is_auto_filed_and_gets_project_context(clean):
    from conftest import CALLS

    c = clean
    p = c.post("/api/projects", json={"name": "Harbour Bridge audit", "kind": "client", "keywords": "HB-7, Maria Lopez"}).json()
    c.post(f"/api/projects/{p['id']}/items", json={"kind": "deadline", "title": "Draft report", "due": "2026-12-01"})
    det = c.post("/api/projects/detect", json={"text": "Can you email Maria Lopez about HB-7?"}).json()
    assert det["confident"] and det["project_id"] == p["id"]
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "Sure."}]
    cid, _ = chat(c, "What's left on the HB-7 job for Maria Lopez?")
    sys = next(m["content"] for m in CALLS[0]["messages"] if m["role"] == "system")
    assert "Current project: Harbour Bridge audit" in sys and "Draft report 2026-12-01" in sys
    assert c.get(f"/api/projects/for-conversation/{cid}").json()["project"]["id"] == p["id"]
    # moving the chat out sticks: the user's choice beats detection
    c.put(f"/api/projects/for-conversation/{cid}", json={"project_id": None})
    c.post(f"/api/projects/{p['id']}/dismiss", json={"kind": "chat", "ref": str(cid)})
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(c, "and HB-7 again", cid)
    assert c.get(f"/api/projects/for-conversation/{cid}").json()["project"] is None


def test_active_project_is_used_for_new_chats(clean):
    from conftest import CALLS

    c = clean
    p = c.post("/api/projects", json={"name": "Biology 101", "kind": "course"}).json()
    c.put("/api/projects/active", json={"project_id": p["id"]})
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    cid, _ = chat(c, "summarise my notes")
    sys = next(m["content"] for m in CALLS[0]["messages"] if m["role"] == "system")
    assert "Current project: Biology 101" in sys
    assert c.get(f"/api/projects/{p['id']}").json()["items"]["chat"][0]["ref"] == str(cid)
    c.put("/api/projects/active", json={"project_id": None})


# Action history ---------------------------------------------------------------------------------------------------
def _entry(c, title_part):
    for e in c.get("/api/history").json()["entries"]:
        if title_part in e["title"]:
            return e
    raise AssertionError(f"no history entry with {title_part!r}")


def test_task_note_and_memory_changes_are_listed_and_undone(clean):
    c = clean
    SCRIPT[:] = [tool_call("create_task", {"title": "Order timber"}),
                 tool_call("create_note", {"title": "Site visit", "body": "Roof ok"}, "c2"),
                 {"role": "assistant", "content": "Done."}]
    chat(c, "add a task and a note")
    e = _entry(c, "Added task “Order timber”")
    assert e["undo"]["available"] and e["who"] == "Assistant" and e["category"] == "task"
    tid = next(t["id"] for t in c.get("/api/tasks").json() if t["title"] == "Order timber")
    r = c.post(f"/api/history/{e['id']}/undo", json={}).json()
    assert r["status"] == "undone" and "removed" in r["undo"]["note"].lower()
    assert all(t["id"] != tid for t in c.get("/api/tasks").json())
    assert c.post(f"/api/history/{e['id']}/undo", json={}).status_code == 409
    n = _entry(c, "Created note “Site visit”")
    doc_id = next(d["id"] for d in c.get("/api/docs").json() if d["title"] == "Site visit")
    # append then undo the append: the body goes back
    SCRIPT[:] = [tool_call("append_to_note", {"doc_id": doc_id, "text": "Gutter loose"}), {"role": "assistant", "content": "ok"}]
    chat(c, "add gutter")
    a = _entry(c, f"Added to note #{doc_id}")
    c.post(f"/api/history/{a['id']}/undo", json={})
    assert "Gutter" not in c.get(f"/api/docs/{doc_id}").json()["body"]
    c.post(f"/api/history/{n['id']}/undo", json={})
    assert c.get(f"/api/docs/{doc_id}").status_code == 404
    # chat turns appear too
    assert any(x["category"] == "chat" for x in c.get("/api/history").json()["entries"])


def test_task_edit_restores_previous_state(clean):
    c = clean
    t = c.post("/api/tasks", json={"title": "Call plumber"}).json()
    SCRIPT[:] = [tool_call("update_task", {"task_id": t["id"], "status": "done"}), {"role": "assistant", "content": "ok"}]
    chat(c, "done with plumber")
    e = _entry(c, "Task “Call plumber” marked done")
    c.post(f"/api/history/{e['id']}/undo", json={})
    assert next(x for x in c.get("/api/tasks").json() if x["id"] == t["id"])["status"] == "todo"


def test_local_event_and_draft_undo(clean):
    c = clean
    SCRIPT[:] = [tool_call("create_event", {"title": "Site meeting", "start": "2026-11-03T09:00:00+10:00", "end": "2026-11-03T10:00:00+10:00"}),
                 {"role": "assistant", "content": "Booked."}]
    chat(c, "book site meeting")
    e = _entry(c, "Added “Site meeting”")
    assert e["category"] == "calendar" and e["undo"]["available"] and not e["undo"]["confirm"]
    c.post(f"/api/history/{e['id']}/undo", json={})
    from app import db, history

    assert db.one("SELECT 1 FROM events WHERE title = 'Site meeting'") is None

    did = db.run("INSERT INTO mail_drafts(to_addr, subject, body, created_at, updated_at) VALUES ('a@b.co', 'Quote', 'x', ?, ?)", db.now(), db.now())
    hid = history.record("email", "Drafted email", tool="save_email_draft", undo_kind="discard_draft", undo={"draft_id": did})
    c.post(f"/api/history/h{hid}/undo", json={})
    assert db.one("SELECT 1 FROM mail_drafts WHERE id = ?", did) is None


def test_remote_event_undo_needs_confirmation(clean):
    from app import history

    hid = history.record("calendar", "Added “Board call” to Work", tool="create_event", undo_kind="delete_events",
                         undo={"events": []}, confirm="This also removes it from Work. The people you invited will get a cancellation email.")
    r = clean.post(f"/api/history/h{hid}/undo", json={})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "confirm"
    assert clean.post(f"/api/history/h{hid}/undo", json={"confirm": True}).json()["status"] == "undone"


def test_sent_email_cannot_be_undone_but_says_how_to_fix(clean):
    from app import history

    d = history.describe("send_email", {"to": "jo@example.com", "subject": "Invoice"}, {"sent": True}, {})
    assert d["undo_kind"] == "cannot" and "correction" in d["fix"] and "jo@example.com" in d["fix_prompt"]
    hid = history.record(d.pop("category"), d.pop("title"), tool="send_email", **d)
    e = next(x for x in clean.get("/api/history").json()["entries"] if x["id"] == f"h{hid}")
    assert not e["undo"]["available"] and e["fix"] and e["fix_prompt"]
    assert clean.post(f"/api/history/h{hid}/undo", json={}).status_code == 400
    comp = history.describe("use_computer", {"goal": "rename files"}, {"files": ["/x/a.txt"], "commands": ["mv a b"]}, {})
    assert comp["undo_kind"] == "cannot" and "Computer page" in comp["fix"] and "/x/a.txt" in comp["detail"]


def test_settings_changes_are_undoable_and_coalesced(clean):
    c = clean
    before = c.get("/api/settings").json().get("workspace_name")
    c.put("/api/settings", json={"workspace_name": "Studio A"})
    c.put("/api/settings", json={"workspace_name": "Studio AB"})
    rows = [e for e in c.get("/api/history", params={"category": "setting"}).json()["entries"] if "Workspace name" in e["title"]]
    assert len(rows) == 1 and "Studio AB" in rows[0]["title"] and rows[0]["who"] == "Settings"
    c.post(f"/api/history/{rows[0]['id']}/undo", json={})
    assert c.get("/api/settings").json().get("workspace_name") == before
    # the undo itself is not a new change
    assert len([e for e in c.get("/api/history", params={"category": "setting"}).json()["entries"] if "Workspace name" in e["title"]]) == 1


def test_memory_and_schedule_undo(clean):
    c = clean
    SCRIPT[:] = [tool_call("remember", {"content": "Prefers invoices on Fridays"}),
                 tool_call("schedule_task", {"name": "Friday invoices", "cron": "0 9 * * 5", "prompt": "Remind me to send invoices"}, "c2"),
                 {"role": "assistant", "content": "ok"}]
    chat(c, "remember and schedule")
    m = _entry(c, "Remembered “Prefers invoices on Fridays”")
    s = _entry(c, "Set up automation “Friday invoices”")
    c.post(f"/api/history/{m['id']}/undo", json={})
    c.post(f"/api/history/{s['id']}/undo", json={})
    assert not any("Fridays" in f["content"] for f in c.get("/api/learn/facts").json()["facts"])
    from app import db

    assert db.one("SELECT 1 FROM schedules WHERE name = 'Friday invoices'") is None


def test_history_search_held_calls_and_export_gate(clean, monkeypatch):
    c = clean
    SCRIPT[:] = [tool_call("send_email", {"to": "x@y.co", "subject": "Hi", "body": "b"}), {"role": "assistant", "content": "Waiting."}]
    chat(c, "email x")
    assert any(e["category"] == "approval" and "send email" in e["title"] for e in c.get("/api/history").json()["entries"])
    assert c.get("/api/history", params={"q": "zzzz-nothing"}).json()["entries"] == []
    csv = c.get("/api/history/export.csv")
    assert csv.status_code == 200 and csv.text.lstrip("\ufeff").startswith("When,Who,Kind")
    pdf = c.get("/api/history/export.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF") and b"%%EOF" in pdf.content
    from app import features_v02

    monkeypatch.setattr(features_v02, "_account_allowed", lambda f: False)
    assert c.get("/api/history/export.csv").status_code == 402
    assert c.get("/api/history").json()["export_allowed"] is False
    assert c.get("/api/history").status_code == 200  # the timeline and undo stay free


def test_project_tools_are_recorded_and_undoable(clean):
    c = clean
    SCRIPT[:] = [tool_call("create_project", {"name": "Thesis", "kind": "research", "keywords": "chapter 3"}),
                 {"role": "assistant", "content": "Started."}]
    chat(c, "start a project for my thesis")
    e = _entry(c, "Started project Thesis")
    c.post(f"/api/history/{e['id']}/undo", json={})
    assert all(p["name"] != "Thesis" for p in c.get("/api/projects").json()["projects"])


# Checked answers --------------------------------------------------------------------------------------------------
@pytest.fixture
def checker(monkeypatch):
    from app import checked

    replies: list = []

    async def fake(prompt, payload, model=None):
        fake.seen.append((prompt, payload))
        return replies.pop(0) if replies else {}

    fake.seen = []
    monkeypatch.setattr(checked, "model_json", fake)
    return replies


def test_check_cites_flags_and_downgrades(clean, checker):
    c = clean
    c.post("/api/docs", json={"title": "Budget memo", "body": "The 2026 budget is $48,000. Approved by Dana on 3 March.", "kind": "note"})
    answer = "The budget is $48,000. Dana approved it on 3 March. The project ends in June."
    SCRIPT[:] = [tool_call("search_notes", {"query": "budget"}), {"role": "assistant", "content": answer}]
    cid, _ = chat(c, "what's the budget?")
    checker[:] = [
        {"claims": [{"text": "The budget is $48,000.", "sources": ["S1"]},
                    {"text": "Dana approved it on 3 March.", "sources": ["S1"]},
                    {"text": "The project ends in June.", "sources": []}]},
        {"checks": [{"i": 0, "verdict": "supported", "source": "S1", "quote": "The 2026 budget is $48,000."},
                    {"i": 1, "verdict": "supported", "source": "S1", "quote": "Approved by Dana in April."},  # not in the source
                    {"i": 2, "verdict": "unsupported"}]},
    ]
    r = c.post("/api/checked/check", json={"conversation_id": cid}).json()
    v = [x["verdict"] for x in r["claims"]]
    assert v == ["supported", "unsupported", "unsupported"]
    assert r["claims"][1]["downgraded"] and "quoted passage" in r["claims"][1]["note"]
    assert r["sources"][0]["kind"] == "note" and r["claims"][0]["source"] == "S1"
    assert "1 of 3 claims backed" in r["summary"]
    marked = [s for s in r["segments"] if "claim" in s]
    assert len(marked) == 3 and "".join(s["text"] for s in r["segments"]) == answer
    # cached: no new model calls
    n = len(checker)
    c.post("/api/checked/check", json={"conversation_id": cid})
    assert len(checker) == n


def test_numbers_missing_from_source_downgrade_to_partly(client):
    from app import checked

    v, why = checked._plain_check("Revenue grew 12% to $3.4m", {"text": "Revenue grew 12% this year."}, "Revenue grew 12%", "supported")
    assert v == "partly" and "3.4" in why


def test_check_mode_adds_prompt_and_is_pro(clean, checker, monkeypatch):
    from conftest import CALLS

    c = clean
    SCRIPT[:] = [{"role": "assistant", "content": "hi"}]
    cid, _ = chat(c, "hello")
    assert c.put("/api/checked/mode", json={"conversation_id": cid, "on": True}).json()["on"] is True
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    chat(c, "how many staff?", cid)
    sys = next(m["content"] for m in CALLS[0]["messages"] if m["role"] == "system")
    assert "Check mode is on" in sys
    from app import features_v02

    monkeypatch.setattr(features_v02, "_account_allowed", lambda f: False)
    assert c.put("/api/checked/mode", json={"conversation_id": cid, "on": True}).status_code == 402
    assert c.post("/api/checked/check", json={"conversation_id": cid}).status_code == 402
    assert c.get("/api/checked/mode", params={"conversation_id": cid}).json()["allowed"] is False
    c.put("/api/checked/mode", json={"conversation_id": cid, "on": False})


def test_check_falls_back_when_model_returns_nothing(clean, checker):
    c = clean
    SCRIPT[:] = [{"role": "assistant", "content": "Sydney has 5.3 million people. Hope that helps!"}]
    cid, _ = chat(c, "population?")
    r = c.post("/api/checked/check", json={"conversation_id": cid}).json()
    assert r["claims"] and r["claims"][0]["verdict"] == "unsupported" and r["counts"]["unsupported"] >= 1


def test_history_event_times_read_like_people_say_them(client):
    from app import db, history

    db.set_setting("timezone", "Australia/Brisbane")
    assert history._when("2026-11-02T23:00:00+00:00") == "Tue 3 Nov, 9:00 am"
    assert history._when("2026-11-03") == "Tue 3 Nov"
    assert history._when("not a date") == "not a date"


def test_phone_gets_projects_history_and_check_with_the_same_rules(client, clean, monkeypatch):
    from fastapi.testclient import TestClient

    from app import db, features_v02, history, main

    phone = TestClient(main.app, headers={"X-Workbench": "1"})
    start = client.post("/api/mobile/pair/start").json()
    token = phone.post("/api/mobile/pair/claim", json={"code": start["code"], "name": "v02 phone", "platform": "ios"}).json()["device_token"]
    h = {"Authorization": f"Bearer {token}"}
    assert phone.get("/api/mobile/projects").status_code == 401  # needs the device token
    p = client.post("/api/projects", json={"name": "Phone job", "kind": "job", "keywords": "phone job"}).json()
    lst = phone.get("/api/mobile/projects", headers=h).json()
    assert [x["name"] for x in lst["projects"]] == ["Phone job"]
    assert phone.put("/api/mobile/projects/active", json={"project_id": p["id"]}, headers=h).json()["active_id"] == p["id"]
    assert phone.get(f"/api/mobile/projects/{p['id']}", headers=h).json()["name"] == "Phone job"
    # undo from the phone; remote changes still need the extra yes
    tid = db.run("INSERT INTO tasks(title, status, priority, created_at, updated_at) VALUES ('phone undo', 'todo', 0, ?, ?)", db.now(), db.now())
    hid = history.record("tasks", "Added task “phone undo”", tool="create_task", undo_kind="delete_task", undo={"task_id": tid})
    assert any(e["id"] == f"h{hid}" for e in phone.get("/api/mobile/history", headers=h).json()["entries"])
    assert phone.post(f"/api/mobile/history/h{hid}/undo", json={}, headers=h).status_code == 200
    assert not db.one("SELECT 1 FROM tasks WHERE id = ?", tid)
    hid2 = history.record("calendar", "Added event", undo_kind="delete_events", undo={"event_ids": []}, confirm="This also cancels it in Google Calendar.")
    assert phone.post(f"/api/mobile/history/h{hid2}/undo", json={}, headers=h).status_code == 409
    # Pro gate for checked answers holds on the phone too
    monkeypatch.setattr(features_v02, "_account_allowed", lambda f: False)
    assert phone.get("/api/mobile/checked/mode", headers=h).json()["allowed"] is False
    assert phone.put("/api/mobile/checked/mode", json={"conversation_id": None, "on": True, "default": True}, headers=h).status_code == 402
