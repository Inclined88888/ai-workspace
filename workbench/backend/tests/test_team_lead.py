"""feat/team-lead: staff, assigning in plain English, suggestions, approvals, replies, risk, plan, privacy. Offline."""

from datetime import date, timedelta

import pytest


@pytest.fixture
def team(client, monkeypatch):
    from app import db
    from app.team import store

    store.ensure()
    from app.team import replies

    replies.ensure()
    for t in ("team_pending", "team_seen", "team_updates", "team_suggestions", "team_outbox", "team_channel_ok", "team_notices", "team_tasks", "team_members"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings WHERE key LIKE 'team_%'")
    monkeypatch.setattr(store, "today", lambda: date(2026, 11, 2))  # a Monday
    sam = client.post("/api/team/members", json={"name": "Sam Rivera", "role": "Estimator", "skills": "quotes, quoting, pricing",
                                                  "contact": {"email": "sam@example.com"}, "channel": "email"}).json()
    jo = client.post("/api/team/members", json={"name": "Jo Bloggs", "role": "Electrician", "skills": ["wiring", "cable", "install"],
                                                 "contact": {"phone": "+61 400 111 222"}, "channel": "sms", "work_days": ["mon", "tue", "wed"]}).json()
    return client, sam, jo


def test_parse_assignment_and_reply(team):
    from app.team import parse

    c, sam, jo = team
    p = c.post("/api/team/tasks/parse", json={"text": "give Sam the Smith St quote by Friday, urgent"}).json()
    assert p["member_name"] == "Sam Rivera" and p["title"] == "Smith St quote" and p["due"] == "2026-11-06" and p["priority"] == 3
    p = c.post("/api/team/tasks/parse", json={"text": "assign the switchboard install to Jo tomorrow"}).json()
    assert p["member_name"] == "Jo Bloggs" and p["title"].lower() == "switchboard install" and p["due"] == "2026-11-03"
    p = c.post("/api/team/tasks/parse", json={"text": "pull new cable at the Lee job by 4 Nov"}).json()
    assert p["member_id"] is None and p["due"] == "2026-11-04" and p["suggested"]["name"] == "Jo Bloggs"
    assert parse.reply("done")["status"] == "done"
    assert parse.reply("50%") == {"status": "", "percent": 50, "note": "", "task_ref": None, "understood": True}
    r = parse.reply("blocked: waiting on parts")
    assert r["status"] == "blocked" and r["note"] == "waiting on parts"
    assert parse.reply("on it [T12]")["task_ref"] == 12 and parse.reply("on it")["status"] == "doing"
    assert parse.reply("Re: thanks\n\nhalf done\n\nOn Mon Sam wrote:\n> done")["percent"] == 50


def test_assign_notify_needs_approval_first_time_then_auto(team, monkeypatch):
    from app import mail

    sent = []
    monkeypatch.setattr(mail, "send", lambda acct, to, subject, body, *a, **k: sent.append((to, subject, body)) or "id1")
    from app.team import outbox

    monkeypatch.setattr(outbox, "mail_account", lambda: {"id": 1, "email": "me@example.com"})
    c, sam, jo = team
    r = c.post("/api/team/tasks", json={"title": "Smith St quote", "assignee_id": sam["id"], "due": "2026-11-06"}).json()
    msg = r["message"]
    assert msg["status"] == "waiting" and "First message" in msg["why"] and "[T" in msg["body"] and not sent
    assert c.post(f"/api/team/outbox/{msg['id']}/approve").json()["status"] == "sent"
    assert sent[0][0] == "sam@example.com"
    # second assignment: still waits until auto-notify is on
    r2 = c.post("/api/team/tasks", json={"title": "Second quote", "assignee_id": sam["id"]}).json()
    assert r2["message"]["status"] == "waiting" and "auto-notify" in r2["message"]["why"]
    c.put("/api/team/settings", json={"auto_notify": True})
    r3 = c.post("/api/team/tasks", json={"title": "Third quote", "assignee_id": sam["id"]}).json()
    assert r3["message"]["status"] == "sent" and len(sent) == 2
    # sms with no provider -> copy it yourself, never silently dropped
    j = c.post("/api/team/tasks", json={"title": "Pull cable", "assignee_id": jo["id"]}).json()["message"]
    assert c.post(f"/api/team/outbox/{j['id']}/approve").json()["status"] == "copy"


def test_replies_update_tasks_and_notify_lead(team):
    c, sam, jo = team
    t = c.post("/api/team/tasks", json={"title": "Pull cable", "assignee_id": jo["id"], "notify": False}).json()["task"]
    r = c.post("/api/team/inbound", json={"sender": "0400 111 222", "text": "blocked: waiting on parts", "source": "sms"}).json()
    assert r["applied"] and r["task"]["status"] == "blocked" and r["task"]["blocked_reason"] == "waiting on parts"
    r = c.post("/api/team/inbound", json={"sender": "+61400111222", "text": f"T{t['id']} 50%", "source": "sms"}).json()
    assert r["task"]["percent"] == 50 and r["task"]["status"] == "doing"
    assert c.post("/api/team/inbound", json={"sender": "stranger@x.com", "text": "done"}).json()["applied"] is False
    notes = c.get("/api/team/notices").json()
    assert any("Jo is stuck on" in n["title"] for n in notes)
    # finishing suggests the next task for that person
    c.post("/api/team/tasks", json={"title": "Install wiring at Lee job"})
    c.post("/api/team/inbound", json={"sender": "+61400111222", "text": "done", "source": "sms"})
    sug = c.get("/api/team").json()["suggestions"]
    assert any(s["kind"] in ("next", "assign") and s["member_name"] == "Jo Bloggs" for s in sug)


def test_suggest_approve_risk_plan_and_digest(team):
    from app.team import engine

    c, sam, jo = team
    t = c.post("/api/team/tasks", json={"title": "Price the Lee job quote", "due": "2026-11-04"}).json()
    sug = t["suggestion"]
    assert sug["member_name"] == "Sam Rivera" and "quot" in sug["reason"]
    d = c.post(f"/api/team/suggestions/{sug['id']}/approve").json()
    assert d["status"] == "approved" and c.get(f"/api/team/tasks/{t['task']['id']}").json()["assignee_id"] == sam["id"]
    late = c.post("/api/team/tasks", json={"title": "Old job", "assignee_id": jo["id"], "due": "2026-10-30", "notify": False}).json()["task"]
    found = c.post("/api/team/check").json()
    assert any(x["task_id"] == late["id"] for x in found["late"])
    out = c.get("/api/team/outbox").json()
    assert any(o["kind"] == "nudge" and o["task_id"] == late["id"] and o["status"] == "waiting" for o in out)
    assert c.post("/api/team/check").json()["nudges"] == []  # one nudge a day, not a flood
    plan = c.get("/api/team/plan").json()
    jo_plan = next(p for p in plan["people"] if p["member"]["id"] == jo["id"])
    assert jo_plan["working"] and "Old job" in jo_plan["message"]
    q = c.post("/api/team/plan/send").json()["queued"]
    assert q and all(o["status"] == "waiting" for o in q)  # plans always wait for the lead
    assert "late" in c.get("/api/team/digest").json()["text"]
    board = c.get("/api/team").json()
    assert board["at_risk"] >= 1 and {m["name"] for m in board["members"]} == {"Sam Rivera", "Jo Bloggs"}
    assert "No location" in board["settings"]["privacy"] or "no location" in board["settings"]["privacy"]
    assert engine.works_on(jo, date(2026, 11, 5)) is False  # Jo doesn't work Thursdays


def test_member_away_and_person_page(team):
    c, sam, jo = team
    c.patch(f"/api/team/members/{sam['id']}", json={"away": [{"from": "2026-11-02", "to": "2026-11-06", "note": "leave"}]})
    p = c.get(f"/api/team/members/{sam['id']}").json()
    assert p["working_today"] is False and p["contact"]["email"] == "sam@example.com"
    s = c.post("/api/team/tasks/parse", json={"text": "do the quote for the Kim job today"}).json()
    assert s["suggested"]["name"] == "Jo Bloggs"  # Sam knows quoting but is on leave


def test_team_features_need_team_plan_but_my_tasks_are_free(team, monkeypatch):
    from app import features_v02

    c, sam, jo = team
    monkeypatch.setattr(features_v02, "_account_allowed", lambda f: False)
    r = c.post("/api/team/tasks", json={"title": "x", "assignee_id": sam["id"]})
    assert r.status_code == 402 and "Team and Business" in r.json()["detail"]["message"]
    assert c.post("/api/team/members", json={"name": "New"}).status_code == 402
    assert c.post("/api/team/tasks", json={"title": "Call the council", "mine": True}).status_code == 200
    assert features_v02.tier("team_tasks") == "team" and features_v02.tier("my_tasks") == "free"


def test_entities_and_phone_routes(team):
    from app.team import store

    c, sam, jo = team
    t = c.post("/api/team/tasks", json={"title": "x", "assignee_id": sam["id"], "notify": False}).json()["task"]
    assert store.task_entity(store.task(t["id"]))["assignee"] == f"team_member:{sam['id']}"
    assert store.person_entity(store.member(sam["id"]))["type"] == "Person"
    assert c.get("/api/mobile/team").status_code == 401  # phone routes need a paired phone


def test_assistant_tool(team):
    from app import assistant

    c, sam, jo = team
    r = assistant.TOOL_MAP["team_assign"].fn(request="give Sam the Smith St quote by Friday")
    assert r["assigned_to"] == "Sam Rivera" and r["task"]["due"] == "2026-11-06" and r["message_status"] == "waiting"
    assert assistant.TOOL_MAP["team_status"].fn()["counts"]["todo"] >= 1
