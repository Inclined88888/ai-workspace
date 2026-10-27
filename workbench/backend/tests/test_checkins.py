"""Proactive check-ins and ROOKS reaching out: briefings only when something changed, delivered once, quiet hours and
limits, retries, restart recovery, budgets and local-only, call invitations, and phone calls through the test
provider (which never dials anything)."""

import asyncio
import json
from datetime import timedelta

import pytest

from conftest import CALLS, SCRIPT


def briefing(text, notify=True):
    return {"role": "assistant", "content": json.dumps({"notify": notify, "urgency": "normal", "briefing": text})}


def briefing_calls():
    return [c for c in CALLS if c["messages"][0]["content"].startswith("You write proactive check-in")]


def inbox_titles(client):
    return [i["title"] for i in client.get("/api/inbox").json()["items"]]


@pytest.fixture()
def pro(client):
    from app import calls, db

    client.put("/api/checkins/limits", json={"quiet_start": "00:00", "quiet_end": "00:00", "per_topic_day": 3, "per_day": 10})
    client.put("/api/calls/policy", json={"window_start": "00:00", "window_end": "23:59", "weekend_start": "00:00", "weekend_end": "23:59",
                                          "invites_per_day": 5, "invite_minutes": 30, "phone_per_day": 1, "phone_per_week": 3, "phone_month_cents": 100})
    yield client
    SCRIPT.clear()
    calls.MOCK_CALLS.clear()
    for t in ("topics", "topic_runs", "deliveries", "call_invites", "phone_calls", "inbox"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM tasks WHERE title LIKE 'CHK %'")
    for k in ("phone_provider", "phone_enabled", "phone_number_enc", "phone_verified", "phone_code", "contact_policy", "checkin_limits"):
        db.set_setting(k, "")


def task(client, title, due):
    return client.post("/api/tasks", json={"title": title, "due": due}).json()


def today(offset=0):
    from datetime import date

    return (date.today() + timedelta(days=offset)).isoformat()


def new_topic(client, **kw):
    body = {"name": "Deadlines", "cron": "0 8 * * *", "sources": ["tasks"], **kw}
    r = client.post("/api/checkins", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def run(tid, slot):
    from app import checkins

    return asyncio.run(checkins.check(tid, slot))


def test_a_briefing_only_when_something_changed_and_never_twice(pro):
    t = new_topic(pro)
    task(pro, "CHK file the BAS", today(1))
    SCRIPT[:] = [briefing("The BAS is due tomorrow.")]
    r1 = run(t["id"], "2026-10-27T08:00:00+00:00")
    assert r1["status"] == "delivered" and r1["written_by"] == "model" and inbox_titles(pro).count("Deadlines") == 1
    n = len(briefing_calls())
    r2 = run(t["id"], "2026-10-28T08:00:00+00:00")
    assert r2["status"] == "nothing_new" and len(briefing_calls()) == n  # no model call when nothing changed
    assert inbox_titles(pro).count("Deadlines") == 1
    task(pro, "CHK renew insurance", today(2))
    SCRIPT[:] = [briefing("Insurance renewal is due in two days.")]
    r3 = run(t["id"], "2026-10-29T08:00:00+00:00")
    prompt = briefing_calls()[-1]["messages"][1]["content"]
    new_part = prompt.split("NEW:")[1].split("KNOWN:")[0]
    assert "renew insurance" in new_part and "file the BAS" not in new_part  # only what is new counts as new
    assert "The BAS is due tomorrow." in prompt  # told the previous briefing, to avoid repeating it
    assert r3["status"] == "delivered" and inbox_titles(pro).count("Deadlines") == 2
    # the same slot again (a retry) never delivers again
    task(pro, "CHK something else", today(1))
    SCRIPT[:] = [briefing("Another thing.")]
    run(t["id"], "2026-10-29T08:00:00+00:00")
    assert inbox_titles(pro).count("Deadlines") == 2


def test_the_model_may_decide_it_is_not_worth_interrupting(pro):
    t = new_topic(pro)
    task(pro, "CHK trivial", today(1))
    SCRIPT[:] = [briefing("", notify=False)]
    assert run(t["id"], "s1")["status"] == "not_significant" and inbox_titles(pro) == []


def test_without_a_usable_model_a_written_list_is_sent(pro, monkeypatch):
    from app import llm, projects

    p = projects.create("CHK private", local_only=True)
    t = new_topic(pro, sources=["tasks"], project_id=p["id"])
    pro.post("/api/tasks", json={"title": "CHK private deadline", "due": today(1)})
    from app import db
    db.run("UPDATE tasks SET project_id = ? WHERE title = 'CHK private deadline'", p["id"])
    monkeypatch.setattr(llm, "is_local", lambda ref: False)  # the only model is a cloud one: not allowed here
    n = len(briefing_calls())
    r = run(t["id"], "s1")
    assert r["status"] == "delivered" and r["written_by"].startswith("rules") and len(briefing_calls()) == n
    assert "private deadline" in pro.get("/api/inbox").json()["items"][0]["body"]
    projects.set_status(p["id"], "deleted")
    projects.purge(p["id"], "CHK private")


def test_topic_budget_switches_to_the_written_list(pro):
    from app import db

    t = new_topic(pro, budget_cents=1)
    db.run("INSERT INTO topic_runs(topic_id, slot, status, cost_cents, started_at) VALUES (?, 'old', 'delivered', 5, ?)", t["id"], db.now())
    task(pro, "CHK budget", today(1))
    n = len(briefing_calls())
    r = run(t["id"], "s1")
    assert "budget" in r["written_by"] and len(briefing_calls()) == n


def test_quiet_hours_hold_the_call_and_urgent_goes_through(pro):
    from app import checkins, db

    now = checkins._local_now()
    pro.put("/api/checkins/limits", json={"quiet_start": (now - timedelta(hours=1)).strftime("%H:%M"),
                                          "quiet_end": (now + timedelta(hours=1)).strftime("%H:%M")})
    t = new_topic(pro, notify="call")
    task(pro, "CHK quiet", today(1))
    SCRIPT[:] = [briefing("Something for later.")]
    assert run(t["id"], "s1")["status"] == "deferred"
    assert pro.get("/api/calls").json()["pending"] == []  # nothing rang during quiet hours
    db.run("UPDATE deliveries SET deliver_after = ? WHERE topic_id = ?", db.now(), t["id"])  # quiet hours end
    asyncio.run(checkins.send_due())
    assert len(pro.get("/api/calls").json()["pending"]) == 1
    u = new_topic(pro, name="Urgent", notify="call", importance="urgent")
    task(pro, "CHK urgent", today(1))
    SCRIPT[:] = [briefing("Now.")]
    assert run(u["id"], "s1")["status"] == "delivered"


def test_daily_limit_sends_it_quietly_instead(pro):
    pro.put("/api/checkins/limits", json={"per_topic_day": 1})
    t = new_topic(pro, notify="call")
    task(pro, "CHK one", today(1))
    SCRIPT[:] = [briefing("First.")]
    assert run(t["id"], "s1")["status"] == "delivered"
    task(pro, "CHK two", today(1))
    SCRIPT[:] = [briefing("Second.")]
    assert run(t["id"], "s2")["status"] == "limited"
    assert len(pro.get("/api/calls").json()["pending"]) == 1 and "limit for interruptions" in pro.get("/api/inbox").json()["items"][0]["body"]


def test_failures_retry_the_same_slot_then_tell_you_once(pro):
    from app import checkins, db

    t = new_topic(pro, sources=["page:http://127.0.0.1:9/never"])
    for i in range(4):
        r = run(t["id"], "2026-11-01T08:00:00+00:00")
        assert r["status"] == "failed"
    row = db.one("SELECT * FROM topics WHERE id = ?", t["id"])
    assert row["failures"] == 0 and row["retry_slot"] is None and "Check-in failing: Deadlines" in inbox_titles(pro)
    assert inbox_titles(pro).count("Check-in failing: Deadlines") == 1
    db.run("UPDATE topics SET failures = 0 WHERE id = ?", t["id"])
    run(t["id"], "2026-11-02T08:00:00+00:00")
    assert db.one("SELECT retry_slot FROM topics WHERE id = ?", t["id"])["retry_slot"] == "2026-11-02T08:00:00+00:00"
    assert checkins._row(t["id"])["next_check_at"] > db.now()  # backoff, not a tight loop


def test_restart_retries_the_same_slot_and_never_sends_twice(pro):
    from app import checkins, db

    t = new_topic(pro)
    task(pro, "CHK restart", today(1))
    SCRIPT[:] = [briefing("Before the restart.")]
    slot = "2026-11-03T08:00:00+00:00"
    run(t["id"], slot)
    assert inbox_titles(pro).count("Deadlines") == 1
    # a crash mid check-in for the same slot, and a Telegram send caught mid-way
    db.run("INSERT INTO topic_runs(topic_id, slot, attempt, status, started_at) VALUES (?, ?, 9, 'running', ?)", t["id"], slot, db.now())
    db.run("INSERT INTO deliveries(key, topic_id, channel, title, body, status, created_at) VALUES ('x-tg', ?, 'telegram', 't', 'b', 'sending', ?)",
           t["id"], db.now())
    checkins.recover()
    assert db.one("SELECT status FROM topic_runs WHERE attempt = 9")["status"] == "interrupted"
    assert db.one("SELECT status FROM deliveries WHERE key = 'x-tg'")["status"] == "uncertain"  # not sent again blindly
    row = db.one("SELECT retry_slot, next_check_at FROM topics WHERE id = ?", t["id"])
    assert row["retry_slot"] == slot
    db.run("UPDATE topics SET facts = '{}' WHERE id = ?", t["id"])  # even if the rerun sees everything as new
    SCRIPT[:] = [briefing("After the restart.")]
    asyncio.run(checkins.tick())
    assert inbox_titles(pro).count("Deadlines") == 1  # the slot was already delivered: no duplicate


def test_scheduler_runs_due_check_ins_and_expired_topics_stop(pro):
    from app import checkins, db

    t = new_topic(pro)
    task(pro, "CHK scheduled", today(1))
    db.run("UPDATE topics SET next_check_at = ? WHERE id = ?", "2000-01-01T00:00:00+00:00", t["id"])
    SCRIPT[:] = [briefing("From the schedule.")]
    asyncio.run(checkins.tick())
    assert db.one("SELECT next_check_at FROM topics WHERE id = ?", t["id"])["next_check_at"] > db.now()
    assert [r["slot"] for r in checkins.runs(t["id"])] == ["2000-01-01T00:00:00+00:00"]
    e = new_topic(pro, name="Old", expires_at="2000-01-01T00:00:00+00:00")
    asyncio.run(checkins.tick())
    assert checkins._row(e["id"])["enabled"] == 0 and checkins._row(e["id"])["last_status"] == "expired"


def test_call_invitation_accept_decline_snooze_and_missed(pro):
    from app import calls, db

    t = new_topic(pro, notify="call")
    task(pro, "CHK call me", today(1))
    SCRIPT[:] = [briefing("The client signed.")]
    run(t["id"], "s1")
    inv = pro.get("/api/calls").json()["pending"][0]
    a = pro.post(f"/api/calls/invites/{inv['id']}/accept").json()
    conv = pro.get(f"/api/conversations/{a['conversation_id']}").json()
    assert a["status"] == "accepted" and "The client signed." in conv["messages"][0]["content"] and "client signed" in a["speak"]
    assert pro.post(f"/api/calls/invites/{inv['id']}/decline").status_code == 409
    # declined: written once
    task(pro, "CHK decline", today(1))
    SCRIPT[:] = [briefing("Decline me.")]
    run(t["id"], "s2")
    inv = pro.get("/api/calls").json()["pending"][0]
    pro.post(f"/api/calls/invites/{inv['id']}/decline")
    assert sum("Decline me." in i["body"] for i in pro.get("/api/inbox").json()["items"]) == 1
    # snoozed at most twice, then it is answer or written
    task(pro, "CHK snooze", today(1))
    SCRIPT[:] = [briefing("Snooze me.")]
    run(t["id"], "s3")
    inv = pro.get("/api/calls").json()["pending"][0]
    for _ in range(2):
        assert pro.post(f"/api/calls/invites/{inv['id']}/snooze", json={"minutes": 5}).json()["status"] == "snoozed"
        db.run("UPDATE call_invites SET snooze_until = ? WHERE id = ?", "2000-01-01T00:00:00+00:00", inv["id"])
        asyncio.run(calls.tick())
        assert pro.get("/api/calls").json()["pending"][0]["id"] == inv["id"]  # back, the same invitation
    assert pro.post(f"/api/calls/invites/{inv['id']}/snooze", json={"minutes": 5}).status_code == 409
    # missed: one written briefing, no repeat however long it stays unanswered
    db.run("UPDATE call_invites SET expires_at = ? WHERE id = ?", "2000-01-01T00:00:00+00:00", inv["id"])
    for _ in range(3):
        asyncio.run(calls.tick())
    assert pro.get("/api/calls").json()["pending"] == []
    assert sum("Snooze me." in i["body"] and "missed the call" in i["body"] for i in pro.get("/api/inbox").json()["items"]) == 1


def test_outside_the_contact_window_it_writes_instead(pro):
    pro.put("/api/calls/policy", json={"window_start": "00:00", "window_end": "00:01", "weekend_start": "00:00", "weekend_end": "00:01"})
    t = new_topic(pro, notify="call")
    task(pro, "CHK window", today(1))
    SCRIPT[:] = [briefing("Out of hours.")]
    run(t["id"], "s1")
    assert pro.get("/api/calls").json()["pending"] == [] and "Written instead of a call" in pro.get("/api/inbox").json()["items"][0]["body"]


def test_phone_needs_password_verified_number_and_identifies_as_ai(pro):
    from app import calls, db

    assert pro.put("/api/calls/phone", json={"password": "wrong", "provider": "test"}).status_code == 403
    pw = "testpassword1"
    pro.put("/api/calls/phone", json={"password": pw, "provider": "test", "number": "0412 345 678"})
    assert pro.put("/api/calls/phone", json={"password": pw, "enabled": True}).status_code == 409  # not verified yet
    pro.post("/api/calls/phone/verify", json={"password": pw})
    spoken = calls.MOCK_CALLS[-1]["twiml"]
    code = "".join(c for c in spoken.split("verification code is")[1].split(".")[0] if c.isdigit())
    assert calls.MOCK_CALLS[-1]["to"] == "+61412345678" and calls.AI_INTRO in spoken
    assert pro.post("/api/calls/phone/confirm", json={"code": "000000" if code != "000000" else "111111"}).status_code == 400
    pro.post("/api/calls/phone/confirm", json={"code": code})
    s = pro.put("/api/calls/phone", json={"password": pw, "enabled": True}).json()["phone"]
    assert s["enabled"] and s["verified"] and s["number"] == "+61••••••678"
    t = new_topic(pro, notify="phone")
    task(pro, "CHK phone", today(1))
    SCRIPT[:] = [briefing("Payroll runs today.")]
    run(t["id"], "s1")
    call = calls.MOCK_CALLS[-1]
    assert "Payroll runs today." in call["twiml"] and call["twiml"].index(calls.AI_INTRO) < call["twiml"].index("Payroll")
    task(pro, "CHK phone two", today(1))
    SCRIPT[:] = [briefing("Second call today.")]
    n = len(calls.MOCK_CALLS)
    run(t["id"], "s2")
    assert len(calls.MOCK_CALLS) == n  # the daily limit refused it: written instead
    assert "Written instead of a phone call" in pro.get("/api/inbox").json()["items"][0]["body"]
    hist = json.dumps(pro.get("/api/calls").json()["phone_calls"], ensure_ascii=False)
    assert "412345678" not in hist and "+61••••••678" in hist  # never the full number in history
    assert "412345678" not in json.dumps(db.all("SELECT * FROM settings WHERE key LIKE 'phone%'"))  # stored encrypted
    # a new number needs verifying again and turns calls off
    s = pro.put("/api/calls/phone", json={"password": pw, "number": "+61400000000"}).json()["phone"]
    assert not s["enabled"] and not s["verified"]


def test_twilio_without_credentials_never_calls_out(pro):
    from app import calls

    pw = "testpassword1"
    pro.put("/api/calls/phone", json={"password": pw, "provider": "twilio", "number": "+61412345678"})
    r = pro.post("/api/calls/phone/verify", json={"password": pw})
    assert r.status_code == 409 and "real call" in r.json()["detail"]  # cost confirmation first
    r = pro.post("/api/calls/phone/verify", json={"password": pw, "confirm_cost": True})
    assert r.status_code == 502 and "not fully set up" in r.json()["detail"] and calls.MOCK_CALLS == []
    assert pro.put("/api/calls/phone", json={"password": pw, "twilio_sid": "not-a-sid"}).status_code == 400
