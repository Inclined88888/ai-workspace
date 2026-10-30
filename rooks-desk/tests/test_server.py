import json
import time
import urllib.request

import pytest

from conftest import el
from rooks_desk import llm, server
from rooks_desk.backends.fake import FakeBackend


@pytest.fixture
def api():
    scripts = []
    b = FakeBackend()
    srv, jobs = server.serve("127.0.0.1", 0, backend_obj=b, overlay=False, approval_timeout=20,
                             model_factory=lambda s: llm.ScriptedModel(scripts.pop(0)), mailer=lambda m: "sent")
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(method, path, body=None, raw=False):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = r.read()
            return data if raw else json.loads(data)

    yield call, scripts, b, jobs
    srv.shutdown()


def wait_for(call, jid, pred, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        j = call("GET", f"/api/jobs/{jid}")
        if pred(j):
            return j
        time.sleep(0.1)
    raise AssertionError(f"timed out: {j['status']}")


def test_job_with_approval_over_http(api):
    call, scripts, b, jobs = api
    scripts.append([{"action": "open_app", "args": {"name": "Mail"}}, el("To", "type", text="a@b.c"), el("Send"),
                    {"action": "done", "args": {"answer": "sent it", "success": True}}])
    j = call("POST", "/api/jobs", {"goal": "send a mail"})
    j = wait_for(call, j["id"], lambda j: j["pending_approvals"])
    ap = j["pending_approvals"][0]
    assert ap["category"] == "message" and j["status"] == "waiting"
    assert call("GET", f"/api/jobs/{j['id']}/screenshot", raw=True)[:4] == b"\x89PNG"
    call("POST", f"/api/jobs/{j['id']}/approvals/{ap['id']}", {"approve": True})
    j = wait_for(call, j["id"], lambda j: j["status"] == "done")
    assert j["result"]["success"] and b.sent and b.sent[0]["to"] == "a@b.c"
    assert [s["action"] for s in j["steps"]] == ["open_app", "type", "click", "done"]
    assert call("GET", f"/api/jobs/{j['id']}/screenshot/1", raw=True)[:4] == b"\x89PNG"
    rows = call("GET", f"/api/jobs/{j['id']}/receipt")
    assert any(r["kind"] == "decision" and r["outcome"] == "approved" for r in rows)


def test_stop_button(api):
    call, scripts, b, jobs = api
    scripts.append([{"action": "wait", "args": {"seconds": 30}}, {"action": "open_app", "args": {"name": "Mail"}}])
    j = call("POST", "/api/jobs", {"goal": "wait a while"})
    wait_for(call, j["id"], lambda j: j["status"] == "running" and j["steps"] == [] and j.get("receipt"))
    time.sleep(0.3)
    call("POST", "/api/stop", {})
    j = wait_for(call, j["id"], lambda j: j["status"] not in ("queued", "running", "waiting"))
    assert j["status"] == "stopped" and "Mail" not in b.apps


def test_status_and_settings(api):
    call, *_ = api
    st = call("GET", "/api/status")
    assert st["backend"] == "fake" and st["ok"] and st["hotkey"]
    s = call("PUT", "/api/settings", {"folders": ["~/Projects"], "deny_apps": ["Banking"]})
    assert s["folders"] == ["~/Projects"] and call("GET", "/api/settings")["deny_apps"] == ["Banking"]
    assert call("GET", "/api/screen", raw=True)[:4] == b"\x89PNG"


def test_remote_bind_needs_token():
    with pytest.raises(SystemExit):
        server.serve("0.0.0.0", 0, token="short")


def test_deleting_waiting_and_queued_jobs_frees_the_mouse(api):
    """Deleting a task that waits for an answer, and one queued behind it, must not wedge the next task."""
    call, scripts, b, jobs = api
    scripts += [[{"action": "open_app", "args": {"name": "Mail"}}, el("Send")],
                [{"action": "done", "args": {"answer": "ran", "success": True}}]]  # the deleted queued job never takes one
    first = call("POST", "/api/jobs", {"goal": "send it"})
    wait_for(call, first["id"], lambda j: j["pending_approvals"])
    queued = call("POST", "/api/jobs", {"goal": "queued one"})
    assert call("GET", f"/api/jobs/{queued['id']}")["status"] == "queued"
    call("DELETE", f"/api/jobs/{queued['id']}")
    call("DELETE", f"/api/jobs/{first['id']}")
    time.sleep(0.5)
    nxt = call("POST", "/api/jobs", {"goal": "next one"})
    j = wait_for(call, nxt["id"], lambda j: j["status"] == "done")
    assert j["result"]["final"] == "ran"
