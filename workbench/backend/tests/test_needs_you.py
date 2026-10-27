"""Needs You: every kind of waiting decision in one list, each explaining what happened, why, the options and what
follows; actions are single and specific; deferring hides without deciding."""

import re

from conftest import SCRIPT


def test_needs_you_gathers_every_kind_and_each_item_explains_itself(client):
    from app import assistant, db, missions, projects, workers

    SCRIPT[:] = [{"role": "assistant", "content": "ok"}] * 20
    conv = assistant.new_conversation("web", "NY chat")
    aid = assistant.request_approval("create_task", '{"title": "NY approve me"}', assistant.Ctx(channel="web", conversation_id=conv), "You asked to approve tasks.")
    mid = missions.create("NY mission waiting for a plan check", steps=[{"title": "a", "done_when": "b"}])["id"]
    wid = workers.create(mid, "computer", "NY open the accounts app")["id"]
    db.run("UPDATE worker_tasks SET status = 'needs_you', attention = 'This uses your computer, so it waits for you to start it.' WHERE id = ?", wid)
    rid = db.run("INSERT INTO research(question, model, status, error, created_at, updated_at) VALUES ('NY research question', 'x', 'error', 'Search failed', ?, ?)", db.now(), db.now())
    tid = db.run("INSERT INTO topics(name, cron, sources, last_error, failures, created_at, updated_at) VALUES ('NY check-in', '0 9 * * *', '[\"tasks\"]', 'Telegram unreachable', 2, ?, ?)", db.now(), db.now())
    task = client.post("/api/tasks", json={"title": "NY overdue task", "due": "2020-01-01"}).json()
    p = projects.create("NY project")
    projects.note(p["id"], "question", "NY which supplier?")
    iid = db.run("INSERT INTO call_invites(title, briefing, expires_at, created_at) VALUES ('NY call', 'Invoice due', '2999-01-01T00:00:00+00:00', ?)", db.now())

    r = client.get("/api/needs-you").json()
    by_id = {i["id"]: i for i in r["items"]}
    expected = {f"approval:{aid}": "approval", f"mission:{mid}": "mission", f"worker:{wid}": "worker", f"research:{rid}": "research",
                f"automation:checkin-{tid}": "automation", f"reminder:{task['id']}": "reminder", f"call:{iid}": "call"}
    for k, kind in expected.items():
        assert k in by_id and by_id[k]["type"] == kind, (k, list(by_id))
    assert any(i["type"] == "follow_up" and "NY which supplier?" in i["what"] for i in r["items"])
    for i in r["items"]:
        assert i["title"] and i["what"] and (i["why"] or i["after"]) and i["link"]
        for a in i["actions"]:
            assert a["effect"] and re.search(r"/\d+(/|$)", a["path"]), a  # every action names one specific thing, never "all"
    appr = by_id[f"approval:{aid}"]
    assert [a["label"] for a in appr["actions"]] == ["Approve once", "Deny"] and "exactly this action, once" in appr["actions"][0]["effect"]
    assert "NY approve me" in appr["what"] and appr["risk"]
    assert r["items"][0]["type"] == "approval"  # most urgent first

    # an action from the list does what it says, on that one item
    a = appr["actions"][0]
    client.post(f"/api{a['path']}", json=a["body"])
    assert db.one("SELECT status FROM approvals WHERE id = ?", aid)["status"] == "approved"
    assert f"approval:{aid}" not in {i["id"] for i in client.get("/api/needs-you").json()["items"]}

    # deferring hides it for a while without deciding anything
    client.post("/api/needs-you/defer", json={"id": f"reminder:{task['id']}", "hours": 2})
    after = client.get("/api/needs-you").json()
    assert f"reminder:{task['id']}" not in {i["id"] for i in after["items"]} and after["deferred"] >= 1
    assert db.one("SELECT status FROM tasks WHERE id = ?", task["id"])["status"] != "done"
    assert f"reminder:{task['id']}" in {i["id"] for i in client.get("/api/needs-you?include_deferred=true").json()["items"]}
    assert client.get("/api/needs-you/count").json()["count"] == len(after["items"])

    # clean up so other tests do not see these
    workers.cancel(wid)
    missions.cancel(mid)
    db.run("DELETE FROM topics WHERE id = ?", tid)
    db.run("DELETE FROM research WHERE id = ?", rid)
    db.run("UPDATE call_invites SET status = 'declined' WHERE id = ?", iid)
    client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
    projects.set_status(p["id"], "deleted")
    projects.purge(p["id"], "NY project")
    SCRIPT.clear()
