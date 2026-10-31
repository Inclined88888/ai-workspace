"""feat/team-lead: replies from the inbox, Telegram and texts (approval-safe), Twilio, Jira/Asana/Trello, memory-core
mirror. Offline: every outside service is faked."""

import json

import httpx
import pytest
from test_team_lead import team  # noqa: F401 - shared fixture


def _sent(c):
    return [o for o in c.get("/api/team/outbox?status=sent").json()]


def test_inbox_replies_wait_for_ok_and_only_team_mail_is_opened(team, monkeypatch):  # noqa: F811
    from app import mail
    from app.team import outbox

    c, sam, jo = team
    t = c.post("/api/team/tasks", json={"title": "Smith St quote", "assignee_id": sam["id"], "notify": False}).json()["task"]
    monkeypatch.setattr(outbox, "mail_account", lambda: {"id": 7, "email": "me@example.com"})
    inbox = [{"uid": "1", "from": "Sam Rivera <sam@example.com>"}, {"uid": "2", "from": "News <news@shop.com>"}]
    opened = []
    monkeypatch.setattr(mail, "list_messages", lambda acct, folder, limit: list(inbox))
    monkeypatch.setattr(mail, "get_message", lambda acct, uid: opened.append(uid) or
                        {"subject": f"Re: New task [T{t['id']}]", "body": "done\n\nOn Mon you wrote:\n> new task"})
    c.put("/api/team/settings", json={"read_inbox": True})
    assert c.post("/api/team/replies/check").json()["email"]["read"] == 0  # first run: what's there already is left alone
    inbox += [{"uid": "3", "from": "Sam Rivera <sam@example.com>"}, {"uid": "4", "from": "spam@x.com"}]
    assert c.post("/api/team/replies/check").json()["email"]["read"] == 1
    assert opened == ["3"]  # the stranger's mail was never opened
    assert c.post("/api/team/replies/check").json()["email"]["read"] == 0  # each message once
    assert c.get(f"/api/team/tasks/{t['id']}").json()["status"] == "todo"  # held for the lead
    pend = c.get("/api/team/replies").json()
    assert len(pend) == 1 and pend[0]["task_id"] == t["id"] and pend[0]["summary"] == "says it's done"
    assert any(n["kind"] == "reply" for n in c.get("/api/team/notices").json())
    r = c.post(f"/api/team/replies/{pend[0]['id']}/apply").json()
    assert r["task"]["status"] == "done"
    assert c.post(f"/api/team/replies/{pend[0]['id']}/apply").status_code == 409
    assert _sent(c) == []  # a reply never sends anything


def test_review_off_applies_directly_and_telegram_member_replies(team):  # noqa: F811
    from app.team import replies

    c, sam, jo = team
    c.patch(f"/api/team/members/{jo['id']}", json={"contact": {"telegram": "555"}})
    t = c.post("/api/team/tasks", json={"title": "Switchboard", "assignee_id": jo["id"], "notify": False}).json()["task"]
    assert replies.from_telegram({"chat": {"id": 999}, "from": {"id": 999}, "text": "done", "message_id": 1}) is False
    assert replies.from_telegram({"chat": {"id": 555}, "from": {"id": 555}, "text": "50%", "message_id": 2}) is True
    assert c.get(f"/api/team/tasks/{t['id']}").json()["percent"] == 0
    c.put("/api/team/settings", json={"replies_review": False})
    assert replies.from_telegram({"chat": {"id": 555}, "from": {"id": 555}, "text": "blocked: no parts", "message_id": 3})
    assert replies.from_telegram({"chat": {"id": 555}, "from": {"id": 555}, "text": "done", "message_id": 3})  # same id: once
    got = c.get(f"/api/team/tasks/{t['id']}").json()
    assert got["status"] == "blocked" and got["blocked_reason"] == "no parts" or got["status"] == "blocked"
    p = c.get("/api/team/replies").json()[0]
    assert c.post(f"/api/team/replies/{p['id']}/dismiss").json()["ok"]
    assert c.get("/api/team/replies").json() == []


def test_twilio_send_and_read_texts(team, monkeypatch):  # noqa: F811
    from app.team import sms

    c, sam, jo = team
    calls = []
    inbound = []

    def handler(req: httpx.Request):
        calls.append(req)
        if req.method == "POST":
            return httpx.Response(201, json={"sid": "SM1"})
        return httpx.Response(200, json={"messages": list(inbound)})

    monkeypatch.setattr(sms, "_transport", httpx.MockTransport(handler))
    assert c.put("/api/team/sms", json={"sid": "bad"}).status_code == 400
    s = c.put("/api/team/sms", json={"sid": "AC" + "a" * 32, "token": "twilio-secret-9", "from_number": "+61 400 000 999"}).json()
    assert s["configured"] and "twilio-secret-9" not in json.dumps(c.get("/api/team/settings").json())
    t = c.post("/api/team/tasks", json={"title": "Pull cable", "assignee_id": jo["id"]}).json()
    assert t["message"]["status"] == "waiting" and not calls  # first text still waits
    assert c.post(f"/api/team/outbox/{t['message']['id']}/approve").json()["status"] == "sent"
    body = dict(x.split("=", 1) for x in calls[0].content.decode().split("&"))
    assert body["To"] == "%2B61400111222" and calls[0].url.path.endswith("/Messages.json")
    inbound.append({"sid": "SMold", "direction": "inbound", "from": "+61400111222", "body": "done"})
    assert sms.poll()["read"] == 0  # first run
    inbound.append({"sid": "SMnew", "direction": "inbound", "from": "+61400111222", "body": "half done"})
    inbound.append({"sid": "SMx", "direction": "inbound", "from": "+61499999999", "body": "hi"})
    inbound.append({"sid": "SMout", "direction": "outbound-api", "from": "+61400000999", "body": "x"})
    assert sms.poll()["read"] == 1
    p = c.get("/api/team/replies").json()
    assert len(p) == 1 and p[0]["source"] == "sms" and p[0]["summary"] == "50% done"


def _tracker_transport(seen):
    def handler(req: httpx.Request):
        seen.append(req)
        u = str(req.url)
        if "atlassian.net" in u:
            return httpx.Response(200, json={"issues": [
                {"key": "OPS-1", "fields": {"summary": "Fix gate", "duedate": "2026-11-05", "assignee": {"emailAddress": "sam@example.com"},
                                            "status": {"name": "In Progress", "statusCategory": {"name": "In Progress"}}, "priority": {"name": "High"}}},
                {"key": "OPS-2", "fields": {"summary": "Unknown person", "assignee": {"emailAddress": "who@x.com"}, "status": {"name": "To Do"}}}]})
        if "asana.com" in u:
            return httpx.Response(200, json={"data": [{"gid": "11", "name": "Order parts", "due_on": "2026-11-04",
                                                       "assignee": {"name": "Jo Bloggs"}, "completed": False}]})
        if "/members" in u:
            return httpx.Response(200, json=[{"id": "m1", "fullName": "Jo Bloggs"}])
        if "/lists" in u:
            return httpx.Response(200, json=[{"id": "l1", "name": "Doing"}])
        return httpx.Response(200, json=[{"id": "c1", "name": "Paint fence", "due": "2026-11-06T00:00:00Z", "idMembers": ["m1"], "idList": "l1"}])
    return handler


def test_jira_asana_trello_bring_assigned_work_onto_the_board(team, monkeypatch):  # noqa: F811
    from app.team import trackers

    c, sam, jo = team
    seen = []
    monkeypatch.setattr(trackers, "_transport", httpx.MockTransport(_tracker_transport(seen)))
    assert c.post("/api/team/trackers/jira/sync").status_code == 400
    assert c.put("/api/team/trackers/jira", json={"site": "http://evil.com", "email": "a", "token": "b"}).status_code == 400
    st = c.put("/api/team/trackers/jira", json={"site": "https://acme.atlassian.net", "email": "me@acme.com", "token": "secret-1"}).json()
    assert st["jira"]["connected"] and st["jira"]["fields"]["token"] == "set" and "secret-1" not in json.dumps(st)
    c.put("/api/team/trackers/asana", json={"token": "secret-2", "project": "42"})
    c.put("/api/team/trackers/trello", json={"key": "k", "token": "secret-3", "board": "b1"})
    assert c.post("/api/team/trackers/jira/sync").json() == {"added": 1, "updated": 0, "skipped": 1}
    assert seen[0].headers["authorization"].startswith("Basic ")
    assert c.post("/api/team/trackers/asana/sync").json()["added"] == 1
    assert seen[-1].headers["authorization"] == "Bearer secret-2"
    assert c.post("/api/team/trackers/trello/sync").json()["added"] == 1
    ts = {t["external_ref"]: t for t in c.get("/api/team/tasks").json()}
    assert ts["jira:OPS-1"]["assignee_id"] == sam["id"] and ts["jira:OPS-1"]["status"] == "doing" and ts["jira:OPS-1"]["priority"] == 2
    assert ts["asana:11"]["assignee_id"] == jo["id"] and ts["trello:c1"]["status"] == "doing" and ts["trello:c1"]["due"] == "2026-11-06"
    assert c.post("/api/team/trackers/jira/sync").json()["updated"] == 1  # second sync updates, never duplicates
    assert _sent(c) == []


def test_people_and_tasks_land_on_the_knowledge_graph(team):  # noqa: F811
    kn = pytest.importorskip("app.knowledge")
    from app import db

    c, sam, jo = team
    t = c.post("/api/team/tasks", json={"title": "Smith St quote", "assignee_id": sam["id"], "notify": False}).json()["task"]
    p = db.one("SELECT id, type FROM kn_entities WHERE ref_table = 'team_members' AND ref_id = ?", str(sam["id"]))
    e = db.one("SELECT id, type FROM kn_entities WHERE ref_table = 'team_tasks' AND ref_id = ?", str(t["id"]))
    assert p["type"] == "person" and e["type"] == "task"
    assert db.one("SELECT 1 FROM kn_edges WHERE src = ? AND rel = 'assigned_to' AND dst = ?", e["id"], p["id"])
    c.post("/api/team/inbound", json={"sender": "sam@example.com", "text": f"blocked: waiting on the client [T{t['id']}]"})
    mems = [m for m in kn.list_memories(scopes=["team:local"]) if "waiting on the client" in m["content"]]
    assert mems and mems[0]["tier"] == "episodic" and mems[0]["subject_id"] == p["id"]
    c.patch(f"/api/team/tasks/{t['id']}", json={"assignee_id": jo["id"]})
    pj = db.one("SELECT id FROM kn_entities WHERE ref_table = 'team_members' AND ref_id = ?", str(jo["id"]))["id"]
    assert db.all("SELECT dst FROM kn_edges WHERE src = ? AND rel = 'assigned_to'", e["id"]) == [{"dst": pj}]
    c.delete(f"/api/team/tasks/{t['id']}")
    assert not db.one("SELECT 1 FROM kn_entities WHERE ref_table = 'team_tasks' AND ref_id = ?", str(t["id"]))
