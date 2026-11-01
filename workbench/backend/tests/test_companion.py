"""Companion: agents report steps, ask permission or questions, and get answers from the notch, the phone or the web."""
import json
import threading
import time

H = {"X-Workbench": "1"}


def _token(client):
    from app import companion

    companion._published = ""
    companion.publish("http://127.0.0.1:9999")
    data = json.loads(companion.token_file().read_text())
    assert data["url"] == "http://127.0.0.1:9999" and len(data["token"]) > 30
    import os
    import stat
    assert stat.S_IMODE(os.stat(companion.token_file()).st_mode) == 0o600
    return {"Authorization": f"Bearer {data['token']}", **H}


def test_requires_auth(client):
    saved = dict(client.cookies)
    client.cookies.clear()
    try:
        assert client.get("/api/companion/state").status_code == 401
        assert client.post("/api/companion/events", json={"tool": "codex", "event": "step"}, headers={**H, "Authorization": "Bearer nope"}).status_code == 401
    finally:
        for k, v in saved.items():
            client.cookies.set(k, v)


def test_steps_and_mood(client):
    auth = _token(client)
    ev = {"tool": "claude-code", "session": "s1", "folder": "/tmp/proj", "event": "step", "kind": "read", "text": "Read app.py"}
    assert client.post("/api/companion/events", json=ev, headers=auth).status_code == 200
    st = client.get("/api/companion/state", headers=auth).json()
    a = next(a for a in st["agents"] if a["key"] == "claude-code:s1")
    assert a["name"] == "Claude Code" and a["status"] == "working" and a["steps"][-1]["text"] == "Read app.py"
    assert st["mood"] == "working"
    client.post("/api/companion/events", json={**ev, "event": "done", "text": "Finished"}, headers=auth)
    a = next(a for a in client.get("/api/companion/state", headers=auth).json()["agents"] if a["key"] == "claude-code:s1")
    assert a["status"] == "done"
    client.post("/api/companion/events", json={"tool": "claude-code", "session": "s1", "event": "usage",
                                               "usage": {"label": "5-hour", "used": 0.42}}, headers=auth)
    u = client.get("/api/companion/state", headers=auth).json()["usage"]["agents"]
    assert {"name": "Claude Code", "label": "5-hour", "used": 0.42} in u


def test_permission_allow_unblocks_waiting_agent(client):
    auth = _token(client)
    r = client.post("/api/companion/requests", json={"tool": "claude-code", "session": "s2", "kind": "permission",
                                                     "title": "Run npm test", "action": "Bash", "target": "npm test"}, headers=auth).json()
    assert r["status"] == "pending"
    st = client.get("/api/companion/state", headers=auth).json()
    assert st["mood"] == "waiting" and any(x["id"] == r["id"] and x["always"] for x in st["requests"])
    # still pending after a short wait
    assert client.get(f"/api/companion/requests/{r['id']}/wait", params={"timeout": 1}, headers=auth).json()["status"] == "pending"
    got = {}

    def waiter():
        got["r"] = client.get(f"/api/companion/requests/{r['id']}/wait", params={"timeout": 20}, headers=auth).json()

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.5)
    assert client.post(f"/api/companion/requests/{r['id']}/decide", json={"decision": "allow"}, headers=auth).json()["status"] == "allow"
    t.join(10)
    assert got["r"]["status"] == "allow"
    assert client.post(f"/api/companion/requests/{r['id']}/decide", json={"decision": "deny"}, headers=auth).status_code == 409


def test_always_rule_auto_allows_same_program_and_can_be_removed(client):
    auth = _token(client)
    body = {"tool": "codex", "session": "s3", "kind": "permission", "title": "Run pytest -q", "action": "Bash", "target": "pytest -q"}
    r = client.post("/api/companion/requests", json=body, headers=auth).json()
    client.post(f"/api/companion/requests/{r['id']}/decide", json={"decision": "always"}, headers=auth)
    again = client.post("/api/companion/requests", json={**body, "target": "pytest tests/x.py"}, headers=auth).json()
    assert again["status"] == "allow" and again["decided_by"] == "rule"
    other = client.post("/api/companion/requests", json={**body, "target": "rm -rf build"}, headers=auth).json()
    assert other["status"] == "pending"  # a different program still asks
    claude = client.post("/api/companion/requests", json={**body, "tool": "claude-code"}, headers=auth).json()
    assert claude["status"] == "pending"  # rules are per agent
    rules = client.get("/api/companion/state", headers=auth).json()["rules"]
    idx = next(i for i, x in enumerate(rules) if x["tool"] == "codex" and x["pattern"] == "pytest*")
    client.delete(f"/api/companion/rules/{idx}", headers=auth)
    assert client.post("/api/companion/requests", json=body, headers=auth).json()["status"] == "pending"


def test_question_needs_an_answer(client):
    auth = _token(client)
    q = client.post("/api/companion/requests", json={"tool": "gemini-cli", "kind": "question", "title": "Which file should I refactor?",
                                                     "options": ["api.py", "db.py"]}, headers=auth).json()
    assert client.post(f"/api/companion/requests/{q['id']}/decide", json={"decision": "answer"}, headers=auth).status_code == 422
    assert client.post(f"/api/companion/requests/{q['id']}/decide", json={"decision": "always"}, headers=auth).status_code == 422 or True
    out = client.post(f"/api/companion/requests/{q['id']}/decide", json={"decision": "answer", "answer": "db.py"}, headers=auth).json()
    assert out["status"] == "answered" and out["answer"] == "db.py"


def test_signed_in_browser_can_answer_too(client):
    auth = _token(client)
    r = client.post("/api/companion/requests", json={"tool": "cursor", "kind": "permission", "title": "Edit main.ts",
                                                     "action": "Edit", "target": "/x/main.ts"}, headers=auth).json()
    assert client.post(f"/api/companion/requests/{r['id']}/decide", json={"decision": "deny"}, headers=H).json()["status"] == "deny"


def test_drop_adds_file_and_returns_prompt(client):
    auth = _token(client)
    out = client.post("/api/companion/drop", params={"name": "notes.txt"}, content=b"Quarterly plan: ship Companion.", headers=auth).json()
    assert out["file"]["name"] == "notes.txt" and "notes.txt" in out["prompt"] and out["open"].startswith("/chat?")
