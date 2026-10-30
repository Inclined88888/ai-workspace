"""Computer use (Rooks Desk) inside Workbench: the /api/desk proxy the Computer page uses, the assistant's use_computer
tool, the /v1 bridge for the rooks-desk channel, and email with attachments from the user's own account."""

import base64
import email
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from conftest import SCRIPT, _port, events, tool_call

DESK_KEY = "d" * 32
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
desk = FastAPI()
SEEN: dict[str, list] = {"jobs": [], "answers": [], "stops": [], "auth": []}


def _auth(req: Request):
    SEEN["auth"].append(req.headers.get("authorization"))
    return req.headers.get("authorization") == f"Bearer {DESK_KEY}"


@desk.post("/api/jobs")
async def d_create(req: Request):
    if not _auth(req):
        return JSONResponse({"detail": "Invalid or missing bearer token"}, 401)
    SEEN["jobs"].append(await req.json())
    return {"id": "j1", "status": "queued"}


@desk.get("/api/jobs/j1")
def d_job(req: Request):
    _auth(req)
    return {"id": "j1", "status": "done", "steps": [{"step": 1}, {"step": 2}], "pending_approvals": [],
            "result": {"final": "Saved notes.txt", "success": True, "files": ["/home/u/Documents/notes.txt"],
                       "commands": [{"command": "ls", "exit_code": 0}], "denied": []}}


@desk.get("/api/jobs/nope")
def d_missing():
    return JSONResponse({"detail": "Not found"}, 404)


@desk.post("/api/jobs/j1/approvals/{aid}")
async def d_answer(aid: str, req: Request):
    SEEN["answers"].append((aid, await req.json()))
    return {"ok": True}


@desk.post("/api/stop")
def d_stop():
    SEEN["stops"].append(time.time())
    return {"stopped": ["j1"]}


@desk.get("/api/jobs/j1/screenshot/{step}")
def d_shot(step: int):
    return Response(PNG, media_type="image/png")


@desk.get("/api/status")
def d_status():
    return {"backend": "linux-x11", "ok": True, "permissions": {"screen": True, "accessibility": True}}


@pytest.fixture(scope="module")
def desk_url():
    import socket

    port = _port()
    server = uvicorn.Server(uvicorn.Config(desk, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def desk_on(client, desk_url, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DESK_URL", desk_url)
    monkeypatch.setattr(config, "DESK_API_KEY", DESK_KEY)
    for v in SEEN.values():
        v.clear()
    return desk_url


def test_desk_is_off_until_rooks_desk_runs(client, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "DESK_URL", "")
    r = client.get("/api/desk/status")
    assert r.status_code == 503 and "Computer use is off" in r.json()["detail"]
    assert client.get("/api/status").json()["desk_enabled"] is False


def test_computer_page_api_through_the_proxy(client, desk_on):
    assert client.get("/api/status").json()["desk_enabled"] is True
    assert client.get("/api/desk/status").json()["backend"] == "linux-x11"
    j = client.post("/api/desk/jobs", json={"goal": "open the editor and save notes", "dry_run": True}).json()
    assert j["id"] == "j1"
    # thinks with the app's own models through the bridge; marked as coming from the app
    assert SEEN["jobs"] == [{"goal": "open the editor and save notes", "dry_run": True, "provider": "workbench", "source": "workbench"}]
    assert SEEN["auth"][0] == f"Bearer {DESK_KEY}"
    assert client.post("/api/desk/jobs/j1/approvals/a7", json={"approve": "yes please"}).json() == {"ok": True}
    assert SEEN["answers"] == [("a7", {"approve": True})]
    assert client.post("/api/desk/stop").json() == {"stopped": ["j1"]}
    r = client.get("/api/desk/jobs/j1/screenshot/2")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert client.get("/api/desk/jobs/nope").status_code == 404


def test_desk_api_needs_the_signed_in_user(client, desk_on):
    from fastapi.testclient import TestClient

    from app import main

    anon = TestClient(main.app)
    assert anon.post("/api/desk/jobs", json={"goal": "rm -rf ~"}, headers={"X-Workbench": "1"}).status_code == 401
    assert anon.post("/api/desk/stop", headers={"X-Workbench": "1"}).status_code == 401
    assert SEEN["jobs"] == []


def test_assistant_uses_the_computer_on_the_conversations_model(client, desk_on):
    SCRIPT[:] = [tool_call("use_computer", {"goal": "save my notes to Documents/notes.txt"}),
                 {"role": "assistant", "content": "Done: notes.txt is saved."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "save my notes", "model": f"{client.pid}/fake-model"}))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["ok"], res
    job = SEEN["jobs"][0]
    assert job["goal"] == "save my notes to Documents/notes.txt" and job["provider"] == "workbench"
    assert job["model"] == f"{client.pid}/fake-model" and job["dry_run"] is False


def test_use_computer_is_only_offered_when_desk_is_on(client, monkeypatch):
    from app import assistant, config

    monkeypatch.setattr(config, "DESK_URL", "")
    tool = next(t for t in assistant.TOOLS if t.name == "use_computer")
    assert not tool.when(None)
    monkeypatch.setattr(config, "DESK_URL", "http://127.0.0.1:1")
    monkeypatch.setattr(config, "DESK_API_KEY", DESK_KEY)
    assert tool.when(None) and tool.effect == "external"


def test_bridge_accepts_the_desk_key_and_channel(client, desk_on, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", "")
    SCRIPT[:] = [{"role": "assistant", "content": '{"action": "done"}'}]
    r = client.post("/v1/chat/completions", json={"model": f"{client.pid}/fake-model", "messages": [{"role": "user", "content": "hi"}]},
                    headers={"Authorization": f"Bearer {DESK_KEY}", "X-Workbench-Channel": "rooks-desk"})
    assert r.status_code == 200, r.text
    assert client.post("/v1/chat/completions", json={"model": "default", "messages": [{"role": "user", "content": "hi"}]},
                       headers={"Authorization": "Bearer wrong"}).status_code == 401


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, context=None, timeout=None):
        self.host = host

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, password):
        self.user, self.password = user, password

    def send_message(self, msg):
        FakeSMTP.sent.append((self.user, self.password, msg.as_bytes()))


@pytest.fixture
def mailbox(client, monkeypatch):
    from app import db, mail
    from app.security import encrypt

    aid = db.run("INSERT INTO mail_accounts(name, email, imap_host, smtp_host, smtp_port, smtp_security, username, password_enc) "
                 "VALUES ('Me', 'me@example.com', '127.0.0.1', '127.0.0.1', 465, 'ssl', 'me', ?)", encrypt("app-password"))
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTP)
    FakeSMTP.sent.clear()
    yield aid
    db.run("DELETE FROM mail_accounts WHERE id = ?", aid)


def test_desk_emails_a_render_with_attachments(client, desk_on, mailbox):
    body = {"to": "me@example.com", "subject": "Your render", "body": "Attached: the 3D render and the drawing.",
            "attachments": [{"filename": "render.png", "content_type": "image/png", "content_b64": base64.b64encode(PNG).decode()},
                            {"filename": "../part.dwg", "content_type": "application/acad", "content_b64": base64.b64encode(b"AC1032dwg").decode()}]}
    h = {"Authorization": f"Bearer {DESK_KEY}", "X-Workbench-Channel": "rooks-desk"}
    r = client.post("/v1/desk/send_email", json=body, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["from"] == "me@example.com" and r.json()["attachments"] == ["render.png", ".._part.dwg"]
    user, password, raw = FakeSMTP.sent[0]
    assert (user, password) == ("me", "app-password")
    msg = email.message_from_bytes(raw)
    parts = {p.get_filename(): p.get_payload(decode=True) for p in msg.walk() if p.get_filename()}
    assert parts == {"render.png": PNG, ".._part.dwg": b"AC1032dwg"} and msg["Subject"] == "Your render"
    # other channels and bad payloads are refused
    assert client.post("/v1/desk/send_email", json=body, headers={**h, "X-Workbench-Channel": "browser"}).status_code == 403
    bad = {**body, "attachments": [{"filename": "x", "content_b64": "not base64!!"}]}
    assert client.post("/v1/desk/send_email", json=bad, headers=h).status_code == 400
    assert len(FakeSMTP.sent) == 1


def test_desk_email_without_a_mail_account(client, desk_on):
    from app import db

    if db.one("SELECT id FROM mail_accounts LIMIT 1"):
        pytest.skip("a mail account exists in this session")
    r = client.post("/v1/desk/send_email", json={"to": "a@b.co", "subject": "x", "body": "y"},
                    headers={"Authorization": f"Bearer {DESK_KEY}", "X-Workbench-Channel": "rooks-desk"})
    assert r.status_code == 409 and "Mail page" in r.json()["detail"]
