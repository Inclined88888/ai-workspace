"""Adapt and learn (app/adapt): memory, teach once, job patterns, feedback. Offline: fake recorder events, a fake agent
runner and a scripted model."""

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import CALLS, SCRIPT, events


def wait_for(fn, timeout=5):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    return fn()


@pytest.fixture
def model(monkeypatch):
    """Scripted JSON answers for adapt's own model calls, keyed by a phrase in the prompt."""
    from app.adapt import knowledge

    answers: dict[str, object] = {}
    seen: list[str] = []

    async def fake(prompt, payload, model=None, channel="adapt"):
        seen.append(payload)
        for key, ans in answers.items():
            if key in prompt:
                return ans(payload) if callable(ans) else ans
        return []

    monkeypatch.setattr(knowledge, "model_json", fake)
    answers["seen"] = None
    answers.pop("seen")
    fake.answers, fake.seen = answers, seen
    return fake


# What I know about you ---------------------------------------------------------------------------------------------
def test_facts_are_encrypted_searchable_editable_and_used_in_chat(client):
    from app import db

    a = client.post("/api/learn/facts", json={"content": "Runs a plumbing business on the Gold Coast called Drip Fix", "category": "work"}).json()
    b = client.post("/api/learn/facts", json={"content": "Prefers invoices as PDF with ABN in the footer", "category": "format"}).json()
    client.post("/api/learn/facts", json={"content": "Biggest client is Acme Builders, project Harbour Towers", "category": "people"})
    raw = json.dumps(db.all("SELECT * FROM adapt_facts"))
    assert "plumbing" not in raw and "Acme" not in raw  # encrypted at rest
    hits = client.get("/api/learn/facts", params={"q": "send the invoice"}).json()["facts"]
    assert hits[0]["id"] == b["id"]  # BM25: invoice ~ invoices
    e = client.put(f"/api/learn/facts/{a['id']}", json={"content": "Runs a plumbing and gas business on the Gold Coast"}).json()
    assert e["content"].startswith("Runs a plumbing and gas") and e["source"] == "user"

    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "sure"}]
    events(client.post("/api/assistant/chat", json={"message": "Write the invoice for Acme"}))
    system = next(c for c in CALLS if c.get("tools"))["messages"][0]["content"]
    assert "Prefers invoices as PDF" in system and "Acme Builders" in system

    client.put("/api/learn/settings", json={"memory_in_chat": False})
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "sure"}]
    events(client.post("/api/assistant/chat", json={"message": "Write the invoice for Acme"}))
    assert "Prefers invoices as PDF" not in next(c for c in CALLS if c.get("tools"))["messages"][0]["content"]
    client.put("/api/learn/settings", json={"memory_in_chat": True})

    assert client.delete(f"/api/learn/facts/{b['id']}").json() == {"deleted": b["id"]}
    assert all(f["id"] != b["id"] for f in client.get("/api/learn/facts").json()["facts"])


def test_learning_switch_off_learns_nothing(client, model):
    from app.adapt import knowledge
    import asyncio

    model.answers["long term memory"] = [{"content": "Studies law at QUT", "kind": "about"}]
    client.put("/api/learn/settings", json={"memory": False})
    assert asyncio.run(knowledge.learn_from("I study law at QUT and I'm in second year, exams in November.", "chat")) == []
    client.put("/api/learn/settings", json={"memory": True})
    got = asyncio.run(knowledge.learn_from("I study law at QUT and I'm in second year, exams in November.", "chat"))
    assert [f["content"] for f in got] == ["Studies law at QUT"] and got[0]["pinned"]
    # duplicates and secrets are skipped
    model.answers["long term memory"] = [{"content": "Studies law at QUT", "kind": "about"},
                                         {"content": "Their bank password is hunter2", "kind": "fact"}]
    assert asyncio.run(knowledge.learn_from("I study law at QUT. My bank password is hunter2, remember it.", "chat")) == []


def test_files_and_tasks_teach_facts_through_observe(client, model):
    model.answers["long term memory"] = lambda payload: ([{"content": "Works on the Riverside Clinic fit-out for Hames Group", "kind": "people"}]
                                                         if "Riverside" in payload else [])
    r = client.post("/api/learn/observe", json={"source": "file", "name": "Riverside schedule.xlsx",
                                                 "text": "Programme: Riverside Clinic fit-out. Client: Hames Group. Week 12 ceilings, week 13 joinery."}).json()
    assert r["learned"][0]["source"] == "file"
    assert any("Riverside" in f["content"] for f in client.get("/api/learn/facts").json()["facts"])


# Show me how ---------------------------------------------------------------------------------------------------------
DEMO = [
    {"kind": "navigate", "url": "https://portal.example.com/login", "t": 0},
    {"kind": "type", "target": {"role": "textbox", "label": "Password", "type": "password"}, "value": "s3cret!", "t": 2},
    {"kind": "click", "target": {"role": "button", "name": "Sign in"}, "t": 3},
    {"kind": "navigate", "url": "https://portal.example.com/invoices", "title": "Invoices", "t": 4},
    {"kind": "type", "target": {"role": "searchbox", "label": "Client"}, "value": "A", "t": 5},
    {"kind": "type", "target": {"role": "searchbox", "label": "Client"}, "value": "Acme Builders", "t": 6},
    {"kind": "click", "target": {"role": "link", "name": "Overdue"}, "t": 7},
    {"kind": "click", "target": {"role": "button", "name": "Send reminder"}, "t": 8},
    {"kind": "mousemove", "x": 10, "y": 20},
]


class FakeRecorder:
    surface = "browser"
    sessions: dict = {}

    async def start(self, start_url=""):
        FakeRecorder.sessions["r1"] = list(DEMO)
        return "r1"

    async def events(self, rid):
        return FakeRecorder.sessions[rid][:4]

    async def stop(self, rid):
        return FakeRecorder.sessions.pop(rid)


class FakeRunner:
    surface = "browser"
    started: list = []
    state: dict = {"status": "running"}

    async def start(self, goal, pb):
        FakeRunner.started.append(goal)
        return f"job{len(FakeRunner.started)}"

    async def status(self, remote_id):
        return dict(FakeRunner.state)


@pytest.fixture
def fakes(monkeypatch):
    from app.adapt import playbooks, recorder

    monkeypatch.setitem(recorder.FACTORIES, "browser", FakeRecorder)
    monkeypatch.setitem(playbooks.RUNNERS, "browser", FakeRunner)
    FakeRunner.started.clear()
    FakeRunner.state = {"status": "running"}


def test_show_me_once_becomes_a_parameterised_playbook_that_replays_by_meaning(client, fakes, model):
    from app import db

    model.answers["one-time demonstration"] = lambda p: {"name": "Chase overdue invoices", "summary": "Remind a client about overdue invoices.",
                                                         "job_kind": "invoice_chasing", "steps": None}
    rec = client.post("/api/learn/recordings", json={"surface": "browser", "start_url": "portal.example.com/login"}).json()
    assert rec["status"] == "recording" and rec["start_url"] == "https://portal.example.com/login"
    live = client.get(f"/api/learn/recordings/{rec['id']}").json()
    assert [e["kind"] for e in live["events"]] == ["navigate", "type", "click", "navigate"]
    assert live["events"][1]["secret"] and live["events"][1]["value"] == ""  # passwords never kept
    pb = client.post(f"/api/learn/recordings/{rec['id']}/stop", json={"narration": "Every Monday I chase overdue invoices for a client."}).json()
    assert "s3cret" not in json.dumps(db.all("SELECT * FROM adapt_recordings")) and "Acme" not in json.dumps(db.all("SELECT * FROM adapt_playbooks"))
    assert pb["name"] == "Chase overdue invoices" and pb["job_kind"] == "invoice_chasing" and pb["status"] == "draft"
    assert pb["start_url"] == "https://portal.example.com/login"
    assert [p["name"] for p in pb["params"]] == ["client"] and pb["params"][0]["example"] == "Acme Builders"  # keystrokes merged
    texts = [s["text"] for s in pb["steps"]]
    assert texts[0].startswith("Sign in or fill") and pb["steps"][0]["needs_user"]
    assert "In the \u201cClient\u201d search box, type {client}" in texts
    assert any(s.get("sensitive") and "Send reminder" in s["text"] for s in pb["steps"])
    assert len(texts) == 5  # mouse noise dropped; the page a click led to is not a step of its own

    # review: rename and edit a step
    steps = pb["steps"]
    steps[3]["text"] = "Click the Overdue tab"
    ed = client.put(f"/api/learn/playbooks/{pb['id']}", json={"name": "Chase invoices", "steps": steps, "ready": True}).json()
    assert ed["status"] == "ready" and ed["note"].startswith("Learned from your change")

    run = client.post(f"/api/learn/playbooks/{pb['id']}/run", json={"values": {"client": "Bluegum Homes"}}).json()
    goal = FakeRunner.started[-1]
    assert "\u201cBluegum Homes\u201d" in goal and "{client}" not in goal and "never by position" in goal
    assert "label" in goal and "handoff" in goal and "Click the Overdue tab" in goal
    FakeRunner.state = {"status": "running", "pending_approvals": [{"description": "Send reminder to Bluegum Homes?"}]}
    assert client.get(f"/api/learn/runs/{run['id']}").json()["status"] == "needs_you"
    FakeRunner.state = {"status": "done", "result": {"success": True, "final": "Sent 2 reminders"}}
    done = client.get(f"/api/learn/runs/{run['id']}").json()
    assert done["status"] == "done" and done["result"] == "Sent 2 reminders"
    assert client.get(f"/api/learn/playbooks/{pb['id']}").json()["successes"] == 1

    # stuck runs leave a note for next time; missing values are asked for in plain English
    run2 = client.post(f"/api/learn/playbooks/{pb['id']}/run", json={}).json()  # reuses last values
    assert "Bluegum Homes" in FakeRunner.started[-1]
    FakeRunner.state = {"status": "done", "result": {"success": False, "final": "No Overdue tab any more"}}
    client.get(f"/api/learn/runs/{run2['id']}")
    assert any("No Overdue tab" in n for n in client.get(f"/api/learn/playbooks/{pb['id']}").json()["notes"])
    r = client.post(f"/api/learn/playbooks/{pb['id']}/run", json={"values": {"client": " "}})
    assert r.status_code == 400 and "Client" in r.json()["detail"]


def test_model_can_reword_steps_and_fix_constant_values(client, fakes, model):
    model.answers["one-time demonstration"] = lambda p: {"name": "Check site diary", "job_kind": "site_checks",
                                                         "steps": [f"Step {i}" for i in range(1, len(json.loads(p)["captured_steps"]) + 1)],
                                                         "fixed_params": ["client"]}
    rec = client.post("/api/learn/recordings", json={"surface": "browser"}).json()
    pb = client.post(f"/api/learn/recordings/{rec['id']}/stop", json={"narration": ""}).json()
    assert pb["params"] == [] and pb["steps"][0]["text"] == "Step 1" and pb["job_kind"] == "site_checks"


def test_narration_only_and_unavailable_recorders(client, model):
    rec = client.post("/api/learn/recordings", json={"surface": "narration"}).json()
    assert rec["status"] == "recording"
    pb = client.post(f"/api/learn/recordings/{rec['id']}/stop", json={
        "narration": "Open the Dayforce timesheet page. Enter my hours for each day. Submit it before 5pm Friday.", "refine": False}).json()
    assert [s["text"] for s in pb["steps"]][0] == "Open the Dayforce timesheet page."
    assert pb["steps"][2].get("sensitive") and pb["job_kind"] == "timesheets" and pb["surface"] == "narration"
    # Rooks Desk does not stream recordings yet: a clear message, not a crash
    d = client.post("/api/learn/recordings", json={"surface": "desktop"}).json()
    assert d["status"] == "failed" and "Rooks Desk" in d["error"]
    assert client.post(f"/api/learn/recordings/{rec['id']}/events", json={"events": []}).status_code == 409


def test_pushed_events_from_any_recorder(client, model):
    rec = client.post("/api/learn/recordings", json={"surface": "narration"}).json()
    r = client.post(f"/api/learn/recordings/{rec['id']}/events", json={"events": [
        {"kind": "open_app", "app": "Excel"}, {"kind": "click", "target": {"role": "menuitem", "name": "Export as PDF"}}, {"kind": "bogus"}]})
    assert r.json() == {"events": 2}
    pb = client.post(f"/api/learn/recordings/{rec['id']}/stop", json={"refine": False}).json()
    assert [s["text"] for s in pb["steps"]] == ["Open Excel", "Click the \u201cExport as PDF\u201d menu item"]


# Job understanding -----------------------------------------------------------------------------------------------------
def test_recurring_work_becomes_a_one_click_routine_that_cannot_act_outside(client, model):
    from app import db
    from app.adapt import hooks, jobs

    monday = datetime(2026, 10, 4, 22, 0, tzinfo=timezone.utc)  # 08:00 Monday in Brisbane
    db.set_setting("timezone", "Australia/Brisbane")
    for w in range(3):
        jobs.observe(None, "chat", "Write the weekly report for the Harbour Towers job", when=monday + timedelta(weeks=w))
    sug = [s for s in client.get("/api/learn/suggestions").json() if s["kind"] == "weekly_report"]
    assert sug and "every Monday around 8am" in sug[0]["title"] and sug[0]["cron"] == "0 8 * * 1"
    assert "Do not send" in sug[0]["prompt"] and "Harbour Towers" in sug[0]["prompt"]
    acc = client.post(f"/api/learn/suggestions/{sug[0]['id']}/accept", json={}).json()
    assert acc["status"] == "accepted" and acc["schedule"]["cron"] == "0 8 * * 1"
    assert not [s for s in client.get("/api/learn/suggestions").json() if s["id"] == sug[0]["id"]]  # offered once
    # in that routine's own conversation, anything external or persistent is refused
    from app import assistant

    conv = assistant.new_conversation("schedule", "x", str(acc["schedule"]["id"]))
    assert hooks.guard("external", "schedule", conv) and hooks.guard("persistent", "schedule", conv)
    assert not hooks.guard("write", "schedule", conv) and not hooks.guard("external", "web", conv)
    other = assistant.new_conversation("schedule", "y", "99999")
    assert not hooks.guard("external", "schedule", other)
    t = assistant.TOOL_MAP["create_event"]
    assert assistant.needs_approval(t, assistant.Ctx(channel="schedule", conversation_id=conv))
    work = client.get("/api/learn/facts").json()["work"]
    assert any(w["kind"] == "weekly_report" and w["times"] >= 3 and w["label"] == "weekly report" for w in work)


def test_chat_turns_are_noticed_as_work(client, model):
    from app import db

    SCRIPT[:] = [{"role": "assistant", "content": "Done"}]
    events(client.post("/api/assistant/chat", json={"message": "Please chase the overdue invoices for Bluegum Homes"}))
    assert wait_for(lambda: db.one("SELECT 1 FROM adapt_activity WHERE kind = 'invoice_chasing' AND source = 'chat'"))


def test_classifier_spans_fields():
    from app.adapt.jobs import classify

    assert classify("Review this lease and flag the risky clauses") == "legal_review"
    assert classify("Solve this integral and show the derivative steps") == "maths"
    assert classify("Analyse the CSV dataset and chart it") == "data_analysis"
    assert classify("I have an exam next week, make a revision plan") == "study"
    assert classify("Render the AutoCAD drawing") == "creative"
    assert classify("hello there") == "other"


# Keeps getting better ----------------------------------------------------------------------------------------------------
def test_edits_and_thumbs_become_lessons(client, fakes, model):
    original = ("Dear Mr Smith,\n\nI hope this email finds you well. I am writing to kindly remind you that invoice 1042 for "
                "the bathroom renovation remains outstanding and we would greatly appreciate payment at your earliest convenience.\n\n"
                "Kind regards,\nDrip Fix")
    edited = "Hi Dave,\n\nQuick one: invoice 1042 for the bathroom is still unpaid. Can you sort it this week?\n\nCheers,\nMick"
    r = client.post("/api/learn/feedback", json={"target_kind": "draft", "target_id": "7", "rating": 0, "original": original, "edited": edited}).json()
    lessons = [x["lesson"] for x in r["learned"]]
    assert any("shorter" in l for l in lessons) and any("\u201cHi\u201d" in l for l in lessons) and any("Mick" in l for l in lessons)
    assert r["note"].startswith("Learned from your change:")
    facts = client.get("/api/learn/facts").json()["facts"]
    assert any(f["source"] == "feedback" and "shorter" in f["content"] for f in facts)

    # an edit with no obvious pattern asks the model for the lesson
    model.answers["corrected their assistant"] = {"lesson": "Quote prices including GST", "about": "format"}
    r = client.post("/api/learn/feedback", json={"target_kind": "message", "original": "Total $100", "edited": "Total $110 inc GST"}).json()
    assert r["learned"][0]["lesson"] == "Quote prices including GST"

    # thumbs down with a comment on a playbook run goes into the playbook for next time
    rec = client.post("/api/learn/recordings", json={"surface": "narration"}).json()
    pb = client.post(f"/api/learn/recordings/{rec['id']}/stop", json={"narration": "Open the supplier site. Download this month's price list.", "refine": False}).json()
    run = client.post(f"/api/learn/playbooks/{pb['id']}/run", json={}).json()
    r = client.post("/api/learn/feedback", json={"target_kind": "run", "target_id": str(run["id"]), "rating": -1,
                                                  "comment": "Use the trade price list, not retail"}).json()
    assert r["learned"][0]["applied_to"] == f"playbook:{pb['id']}"
    client.post(f"/api/learn/playbooks/{pb['id']}/run", json={})
    assert "Use the trade price list, not retail" in FakeRunner.started[-1]
    assert any("trade price" in l["lesson"] for l in client.get("/api/learn/lessons").json())


def test_run_playbook_tool_is_offered(client):
    CALLS.clear()
    SCRIPT[:] = [{"role": "assistant", "content": "ok"}]
    events(client.post("/api/assistant/chat", json={"message": "what can you replay"}))
    names = {t["function"]["name"] for t in next(c for c in CALLS if c.get("tools"))["tools"]}
    assert {"run_playbook", "list_playbooks"} <= names


def test_forget_everything(client):
    client.post("/api/learn/facts", json={"content": "Temporary fact to forget"})
    n = client.post("/api/learn/facts/forget-all").json()["deleted"]
    assert n >= 1 and client.get("/api/learn/facts").json()["facts"] == []
