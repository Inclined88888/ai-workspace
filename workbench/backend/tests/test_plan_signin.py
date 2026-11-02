"""Plan sign-in from the app (plan_signin.py) against fake claude and codex programs that act like the real sign-ins."""

import os
import stat
import sys
import time

import pytest

H = {"X-Workbench": "1"}

FAKE_CLAUDE = r'''#!{py}
import json, os, sys
mark = os.path.join(os.path.dirname(__file__), "claude-signed-in")
a = sys.argv[1:]
if a[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": os.path.exists(mark), "authMethod": "claude.ai", "email": "me@example.com"}}))
elif a[:2] == ["auth", "login"]:
    print("Opening browser to sign in...")
    print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true&state=abc")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good-code":
        open(mark, "w").close(); print("Login successful"); sys.exit(0)
    print("Invalid code"); sys.exit(1)
'''

FAKE_CODEX = r'''#!{py}
import os, sys, time
mark = os.path.join(os.path.dirname(__file__), "codex-signed-in")
a = sys.argv[1:]
if a == ["login", "status"]:
    if os.path.exists(mark):
        print("Logged in using ChatGPT"); sys.exit(0)
    print("Not logged in"); sys.exit(1)
elif a == ["login", "--device-auth"]:
    print("1. Open this link in your browser and sign in to your account")
    print("   https://auth.openai.com/codex/device")
    print("2. Enter this one-time code (expires in 15 minutes)")
    print("   AB12-CD345", flush=True)
    time.sleep(1.5)
    open(mark, "w").close(); print("Successfully logged in"); sys.exit(0)
'''


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        p = tmp_path / name
        p.write_text(body.format(py=sys.executable))
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(tmp_path / "claude"))
    monkeypatch.setenv("WORKBENCH_CODEX_BIN", str(tmp_path / "codex"))
    yield tmp_path
    from app import db
    db.run("DELETE FROM connections WHERE base_url LIKE 'cli://%'")


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminal sign-in is POSIX; Windows uses pipes")
def test_claude_sign_in_from_the_app_then_chat_on_the_plan(client, fakes):
    st = client.get("/api/plans", headers=H).json()["claude"]
    assert st["installed"] and not st["signed_in"] and not st["connected"]
    assert client.post("/api/plans/claude/connect", headers=H).status_code == 400  # not signed in yet
    s = client.post("/api/plans/claude/signin", headers=H).json()
    assert s["status"] == "waiting" and s["needs_code"] and s["url"].startswith("https://claude.com/cai/oauth/authorize")
    bad = client.post(f"/api/plans/signin/{s['id']}/code", headers=H, json={"code": "wrong"}).json()
    assert bad["status"] == "failed" and not client.get("/api/plans", headers=H).json()["claude"]["signed_in"]
    s = client.post("/api/plans/claude/signin", headers=H).json()
    done = client.post(f"/api/plans/signin/{s['id']}/code", headers=H, json={"code": "good-code"}).json()
    assert done["status"] == "done"
    st = client.get("/api/plans", headers=H).json()["claude"]
    assert st["signed_in"] and st["account"] == "me@example.com"
    c = client.post("/api/plans/claude/connect", headers=H).json()
    assert c["connected"]
    from app import db
    row = db.one("SELECT * FROM connections WHERE id = ?", c["connection_id"])
    assert row["base_url"] == "cli://claude" and row["enabled"] == 1
    assert client.post("/api/plans/claude/connect", headers=H).json()["connection_id"] == c["connection_id"]  # no duplicate


@pytest.mark.skipif(sys.platform == "win32", reason="pseudo-terminal sign-in is POSIX; Windows uses pipes")
def test_codex_device_sign_in_shows_the_code_and_finishes_by_itself(client, fakes):
    s = client.post("/api/plans/codex/signin", headers=H).json()
    assert s["url"] == "https://auth.openai.com/codex/device" and s["code"] == "AB12-CD345" and not s["needs_code"]
    for _ in range(60):
        s = client.get(f"/api/plans/signin/{s['id']}", headers=H).json()
        if s["status"] != "waiting":
            break
        time.sleep(0.1)
    assert s["status"] == "done"
    assert client.get("/api/plans", headers=H).json()["codex"]["signed_in"]


def test_missing_program_and_unknown_plan(client, monkeypatch, tmp_path):
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(tmp_path / "nope"))
    assert client.get("/api/plans", headers=H).json()["claude"]["installed"] is False
    assert client.post("/api/plans/claude/signin", headers=H).status_code == 400
    assert client.post("/api/plans/gemini/signin", headers=H).status_code == 404


def test_plans_need_a_session(client):
    from starlette.testclient import TestClient

    assert TestClient(client.app).get("/api/plans").status_code == 401


def test_new_workspace_is_sent_to_the_welcome(client):
    from app import db

    before = db.get_setting("desktop_welcome_done")
    db.set_setting("desktop_welcome_done", "0")
    try:
        assert client.get("/api/status").json()["welcome_pending"] is True
        client.post("/api/desktop/welcome-done", headers=H)
        s = client.get("/api/status").json()
        assert s["welcome_pending"] is False and s["welcome_done"] is True
    finally:
        db.set_setting("desktop_welcome_done", before or "")
