"""Computer Use on the ROOKS side: pairing, policy, approvals, grants, stop, take control and recovery.
A thread plays the companion over the real /api/companion routes."""

import json
import threading
import time

import pytest

from conftest import SCRIPT, events, tool_call


class FakeCompanion:
    """Answers actions like a companion would. reply(kind, args) -> (ok, result)."""

    def __init__(self, client, token, reply=None):
        self.client, self.h = client, {"Authorization": f"Bearer {token}", "X-Workbench": "1"}
        self.reply = reply or (lambda k, a: (True, {"active_app": "Notes", "windows": [{"app": "Notes", "title": "Notes", "focused": True}],
                                                    "elements": [{"id": "e1", "role": "Button", "label": "Add one"}]} if k == "observe" else {"after": {}}))
        self.seen, self.quit = [], threading.Event()
        self.t = threading.Thread(target=self.loop, daemon=True)

    def hello(self, extra=None):
        return self.client.post("/api/companion/hello", headers=self.h, json={"os": "Test OS", "capabilities": ["desktop", "browser", "files"],
                                                                                "permissions": {"accessibility": True}, **(extra or {})}).json()

    def loop(self):
        while not self.quit.is_set():
            r = self.client.post("/api/companion/next", headers=self.h).json()
            a = r.get("action")
            if a:
                self.seen.append(a)
                ok, res = self.reply(a["kind"], a["args"])
                self.client.post("/api/companion/result", headers=self.h, json={"id": a["id"], "ok": ok, "result": res, "error": "" if ok else "no"})

    def __enter__(self):
        self.hello()
        self.t.start()
        return self

    def __exit__(self, *a):
        self.quit.set()


@pytest.fixture()
def paired(client):
    from app import db, security

    security._attempts.clear()  # the pairing throttle (10 per 5 minutes) is per test here, not per suite
    client.put("/api/computer/settings", json={"enabled": True, "mode": "allowed", "app_rules": {}})
    code = client.post("/api/computer/pair").json()["code"]
    r = client.post("/api/companion/pair", json={"code": code, "name": "Test Mac", "info": {"os": "Test OS"}})
    assert r.status_code == 200
    yield r.json()["token"]
    for d in db.all("SELECT id FROM computer_devices WHERE revoked = 0"):
        client.delete(f"/api/computer/devices/{d['id']}")
    client.post("/api/computer/allow")
    client.post("/api/computer/hand-back")
    client.put("/api/computer/settings", json={"enabled": False, "mode": "ask", "app_rules": {}})
    db.run("DELETE FROM computer_actions")
    SCRIPT.clear()


def chat(client, msg):
    ev = events(client.post("/api/assistant/chat", json={"message": msg}))
    return ev, next(e for e in ev if e["type"] == "start")["conversation_id"]


def test_off_by_default_and_pairing_rules(client):
    from app import assistant, computer

    assert computer.settings()["enabled"] is False
    __import__("app.security", fromlist=["x"])._attempts.clear()
    names = {t.name for t in assistant.available_tools(assistant.Ctx(channel="web", conversation_id=0))}
    assert not any(n.startswith(("computer_", "browser_", "files_")) for n in names)
    code = client.post("/api/computer/pair").json()["code"]
    assert client.post("/api/companion/pair", json={"code": "WRONG-CODE-0000", "name": "x"}).status_code == 403
    tok = client.post("/api/companion/pair", json={"code": code, "name": "Mac"}).json()["token"]
    assert client.post("/api/companion/pair", json={"code": code, "name": "again"}).status_code == 403  # one use only
    h = {"Authorization": f"Bearer {tok}", "X-Workbench": "1"}
    assert client.get("/api/companion/state", headers=h).status_code == 200
    assert client.get("/api/companion/state", headers={"Authorization": "Bearer nope"}).status_code == 401
    did = next(d["id"] for d in computer.devices() if d["name"] == "Mac")
    client.delete(f"/api/computer/devices/{did}")
    assert client.get("/api/companion/state", headers=h).status_code == 401
    assert "token" not in json.dumps(client.get("/api/computer").json()).lower().replace("token_hash", "")


def test_companions_only_from_this_machine_or_private_network():
    from fastapi import HTTPException

    from app.computer_api import _local_only

    class Req:
        def __init__(self, host):
            self.client = type("C", (), {"host": host})()

    for ok in ("127.0.0.1", "172.17.0.1", "192.168.1.20", "::1"):
        _local_only(Req(ok))
    with pytest.raises(HTTPException):
        _local_only(Req("8.8.8.8"))


def test_policy_rules(client, paired):
    from fastapi import HTTPException

    from app import computer

    assert computer.classify("click", {"label": "Send", "app": "Mail"})[0] == "consequential"
    assert computer.classify("key", {"keys": "enter", "app": "Slack"})[0] == "consequential"
    assert computer.classify("key", {"keys": "enter", "app": "Numbers"})[0] == "write"
    assert computer.classify("observe", {})[0] == "read"
    with pytest.raises(HTTPException, match="never controls"):
        computer.check("key", {"keys": "cmd+s", "app": "Terminal"})
    with pytest.raises(HTTPException, match="approval"):
        computer.check("click", {"label": "Buy now", "app": "Safari"})
    computer.check("click", {"label": "Buy now", "app": "Safari"}, approved=True)
    computer.update_settings(mode="read_only")
    with pytest.raises(HTTPException, match="read only"):
        computer.check("key", {"keys": "cmd+s", "app": "Numbers"})
    computer.check("observe", {})
    computer.update_settings(mode="ask", app_rules={"Numbers": "write", "Slack": "blocked"})
    assert computer.approval_reason("key", {"keys": "cmd+s", "app": "Numbers"}) == ""
    assert "ask before changes" in computer.approval_reason("key", {"keys": "cmd+s", "app": "Pages"})
    with pytest.raises(HTTPException, match="blocked"):
        computer.check("observe", {"app": "Slack"})
    computer.grant("Pages", 30)
    assert computer.approval_reason("key", {"keys": "cmd+s", "app": "Pages"}) == ""
    with pytest.raises(HTTPException, match="unattended"):
        computer.check("observe", {}, unattended=True)


def test_assistant_uses_the_computer_through_the_companion(client, paired):
    with FakeCompanion(client, paired) as comp:
        SCRIPT[:] = [tool_call("computer_observe", {}, "o"), tool_call("computer_click", {"target": "e1", "label": "Add one", "app": "Notes"}, "c"),
                     {"role": "assistant", "content": "Clicked it."}]
        ev, _ = chat(client, "Click Add one in Notes")
        results = [e for e in ev if e["type"] == "tool_result"]
        assert [r["ok"] for r in results] == [True, True], results
        assert '[e1] Button "Add one"' in results[0]["preview"]
        assert [a["kind"] for a in comp.seen] == ["observe", "click"]
    hist = client.get("/api/computer/history").json()
    assert hist[0]["kind"] == "click" and hist[0]["status"] == "done"
    assert any(r["action"] == "computer" and r["target"] == "click" for r in client.get("/api/audit").json())


def test_consequential_action_waits_for_approval_then_runs_once(client, paired):
    with FakeCompanion(client, paired) as comp:
        SCRIPT[:] = [tool_call("computer_click", {"target": "e9", "label": "Send", "app": "Mail"}, "s"), {"role": "assistant", "content": "Waiting."}]
        ev, _ = chat(client, "Send the email")
        ap = next(e for e in ev if e["type"] == "approval")
        assert comp.seen == []  # nothing reached the computer
        assert client.post(f"/api/approvals/{ap['id']}", json={"approve": True}).json()["status"] == "approved"
        assert [a["kind"] for a in comp.seen] == ["click"] and comp.seen[0]["approved"] is True


def test_web_content_makes_computer_changes_ask_first(client, paired):
    with FakeCompanion(client, paired) as comp:
        SCRIPT[:] = [tool_call("browser_snapshot", {}, "b"), tool_call("computer_key", {"keys": "cmd+s", "app": "Numbers"}, "k"),
                     {"role": "assistant", "content": "ok"}]
        ev, _ = chat(client, "Read the page then save")
        assert [e["name"] for e in ev if e["type"] == "approval"] == ["computer_key"]
        assert [a["kind"] for a in comp.seen] == ["browser_snapshot"]


def test_stop_cancels_and_blocks_until_allowed(client, paired):
    from app import computer

    with FakeCompanion(client, paired, reply=lambda k, a: (time.sleep(0.2), (True, {}))[1]):
        computer.stop()
        SCRIPT[:] = [tool_call("computer_observe", {}, "o"), {"role": "assistant", "content": "ok"}]
        ev, _ = chat(client, "look")
        r = next(e for e in ev if e["type"] == "tool_result")
        assert not r["ok"] and "stopped by you" in r["preview"]
        assert client.get("/api/computer").json()["settings"]["stopped"] is True
        client.post("/api/computer/allow")
        SCRIPT[:] = [tool_call("computer_observe", {}, "o2"), {"role": "assistant", "content": "ok"}]
        ev, _ = chat(client, "look again")
        assert next(e for e in ev if e["type"] == "tool_result")["ok"]


def test_stop_flags_a_running_action_for_the_companion(client, paired):
    from app import computer, db

    did = computer.online_device() or {"id": db.one("SELECT id FROM computer_devices WHERE revoked = 0")["id"]}
    h = {"Authorization": f"Bearer {paired}", "X-Workbench": "1"}
    client.post("/api/companion/hello", headers=h, json={"os": "x"})
    aid = db.run("INSERT INTO computer_actions(device_id, kind, args, status, created_at) VALUES (?, 'type_text', '{}', 'dispatched', ?)",
                 did["id"], db.now())
    computer.stop()
    st = client.get("/api/companion/state", headers=h).json()
    assert st["stop"] is True and aid in st["cancel"]


def test_restart_makes_unconfirmed_actions_uncertain_and_requires_a_look(client, paired):
    import asyncio

    from app import computer, db

    h = {"Authorization": f"Bearer {paired}", "X-Workbench": "1"}
    client.post("/api/companion/hello", headers=h, json={"os": "x"})
    dev = computer.online_device()
    args = {"text": "Invoice 42", "app": "Numbers"}
    key = computer._canon("type_text", args)
    aid = db.run("INSERT INTO computer_actions(device_id, kind, args, risk, status, conversation_id, idem_key, created_at) "
                 "VALUES (?, 'type_text', ?, 'write', 'dispatched', 77, ?, ?)", dev["id"], json.dumps(args), key, db.now())
    computer.recover()
    assert db.one("SELECT status FROM computer_actions WHERE id = ?", aid)["status"] == "uncertain"
    with pytest.raises(Exception, match="outcome is unknown"):
        asyncio.run(computer.request("type_text", args, conversation_id=77))
    # the companion's late result arrives: it did happen. Still not repeated before a fresh look.
    client.post("/api/companion/result", headers=h, json={"id": aid, "ok": True, "result": {}})
    row = db.one("SELECT status, recovered FROM computer_actions WHERE id = ?", aid)
    assert row["status"] == "done" and row["recovered"] == 1
    with pytest.raises(Exception, match="did finish"):
        asyncio.run(computer.request("type_text", args, conversation_id=77))
    # after an observation the decision is the model's again
    db.run("INSERT INTO computer_actions(device_id, kind, args, status, created_at) VALUES (?, 'observe', '{}', 'done', ?)", dev["id"], db.now())
    with FakeCompanion(client, paired):
        r = asyncio.run(computer.request("type_text", args, conversation_id=77, timeout=5))
    assert r["ok"]


def test_missions_pause_when_you_take_control(client, paired):
    from app import computer, db

    mid = db.run("INSERT INTO missions(goal, status, created_at, updated_at) VALUES ('desk', 'running', ?, ?)", db.now(), db.now())
    dev = db.one("SELECT id FROM computer_devices WHERE revoked = 0")
    db.run("INSERT INTO computer_actions(device_id, kind, args, status, mission_id, created_at) VALUES (?, 'observe', '{}', 'queued', ?, ?)",
           dev["id"], mid, db.now())
    computer.take_control()
    assert db.one("SELECT control FROM missions WHERE id = ?", mid)["control"] == "pause"
    assert db.one("SELECT status FROM computer_actions WHERE mission_id = ?", mid)["status"] == "cancelled"
    with pytest.raises(Exception, match="taken control"):
        computer.check("observe", {})
    computer.hand_back()
    computer.check("observe", {})


def test_regressions_from_the_end_to_end_run(client, paired):
    """Found by the real-process run: partial settings updates failed, devices looked online before saying hello,
    typed text reached the activity and tool logs, and the restart check only covered one conversation."""
    import asyncio

    from app import computer, db

    assert client.put("/api/computer/settings", json={"app_rules": {"Mail": "blocked"}}).status_code == 200
    assert computer.settings()["app_rules"] == {"Mail": "blocked"}
    assert computer.online_device() is None  # paired, but it has not said hello yet
    with FakeCompanion(client, paired):
        SCRIPT[:] = [tool_call("computer_type", {"text": "my private note 4471", "app": "Notes"}, "t"), {"role": "assistant", "content": "ok"}]
        chat(client, "type it")
        text = json.dumps(client.get("/api/audit").json()) + json.dumps(client.get("/api/tool-runs").json())
        assert "my private note 4471" not in text and "text of 20 characters" in text
        # an uncertain action from one conversation blocks the identical action from another until a fresh look
        args = {"text": "again", "app": "Notes"}
        dev = computer.online_device()
        db.run("INSERT INTO computer_actions(device_id, kind, args, risk, status, conversation_id, idem_key, created_at) "
               "VALUES (?, 'type_text', ?, 'write', 'uncertain', 1, ?, ?)", dev["id"], json.dumps(args), computer._canon("type_text", args), db.now())
        with pytest.raises(Exception, match="outcome is unknown"):
            asyncio.run(computer.request("type_text", args, conversation_id=999))
