"""The local HTTP API: Workbench's job API shape, approvals over HTTP, auth rules."""

import json
import threading
import time
import urllib.error
import urllib.request

import pytest
from conftest import ENGINE

from rooks_browser import server


def call(base, method, path, body=None, token=None, host=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"} if data else {}
    if token:
        h["Authorization"] = f"Bearer {token}"
    if host:
        h["Host"] = host
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if r.headers.get("Content-Type", "").startswith("application/json") else raw)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


@pytest.fixture
def api(tmp_path):
    srv, jobs = server.serve("127.0.0.1", 0, "", engine=ENGINE, approval_timeout=20)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", jobs
    srv.shutdown()


def script(tmp_path, steps):
    p = tmp_path / "script.json"
    p.write_text(json.dumps(steps))
    return str(p)


def wait(base, jid, cond, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        _, j = call(base, "GET", f"/api/jobs/{jid}")
        if cond(j):
            return j
        time.sleep(0.2)
    raise AssertionError(f"timed out: {j}")


def test_job_with_approval_over_http(api, site, tmp_path):
    base, _ = api
    steps = [{"action": "open", "args": {"url": f"{site}/widget.html"}},
             {"thought": "buy it", "action": "click", "args": {"id": 7}},
             {"action": "done", "args": {"answer": "ordered", "success": True}}]
    code, job = call(base, "POST", "/api/jobs", {"goal": "buy a widget", "provider": "scripted", "model": script(tmp_path, steps)})
    assert code == 200 and job["status"] in ("queued", "running")
    j = wait(base, job["id"], lambda j: j["pending_approvals"])
    ap = j["pending_approvals"][0]
    assert ap["category"] == "purchase" and "Buy now" in ap["description"] and j["status"] == "running"
    assert call(base, "POST", f"/api/jobs/{job['id']}/approvals/{ap['id']}", {"approve": True})[0] == 200
    j = wait(base, job["id"], lambda j: j["status"] == "done")
    assert j["result"]["success"] and any(u.endswith("/order.html") for u in j["result"]["urls"])
    # the Workbench Browser page reads these fields
    s = j["steps"][0]
    assert {"step", "url", "title", "evaluation", "next_goal", "actions"} <= set(s)
    assert j["has_screenshot"]
    code, png = call(base, "GET", f"/api/jobs/{job['id']}/screenshot")
    assert code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
    code, rows = call(base, "GET", f"/api/jobs/{job['id']}/receipt")
    assert any(r["kind"] == "decision" and r["outcome"] == "approved" for r in rows)
    code, lst = call(base, "GET", "/api/jobs")
    assert lst[0]["id"] == job["id"]
    assert call(base, "DELETE", f"/api/jobs/{job['id']}")[0] == 200
    assert call(base, "GET", f"/api/jobs/{job['id']}")[0] == 404


def test_denied_over_http(api, site, tmp_path):
    base, _ = api
    steps = [{"action": "open", "args": {"url": f"{site}/widget.html"}}, {"action": "click", "args": {"id": 7}},
             {"action": "done", "args": {"answer": "not bought", "success": False}}]
    _, job = call(base, "POST", "/api/jobs", {"goal": "buy", "provider": "scripted", "model": script(tmp_path, steps)})
    ap = wait(base, job["id"], lambda j: j["pending_approvals"])["pending_approvals"][0]
    call(base, "POST", f"/api/jobs/{job['id']}/approvals/{ap['id']}", {"approve": False})
    j = wait(base, job["id"], lambda j: j["status"] == "done")
    assert j["result"]["denied"] and not any("order.html" in u for u in j["result"]["urls"])


def test_providers_read_and_errors(api, site):
    base, _ = api
    code, p = call(base, "GET", "/api/providers")
    assert code == 200 and {x["name"] for x in p["providers"]} >= {"openai", "anthropic", "openrouter", "ollama", "workbench"}
    code, r = call(base, "POST", "/api/read", {"url": f"{site}/widget.html"})
    assert code == 200 and "Price: $12.50" in r["text"]
    assert call(base, "POST", "/api/jobs", {"goal": "x"})[0] == 400
    assert call(base, "POST", "/api/jobs", {"goal": "hello", "provider": "nope"})[0] == 400
    assert call(base, "GET", "/api/jobs/missing")[0] == 404


def test_without_token_only_loopback_host_header(api):
    base, _ = api
    assert call(base, "GET", "/api/jobs", host="evil.example")[0] == 401  # DNS rebinding from a web page
    assert call(base, "GET", "/healthz", host="evil.example")[0] == 200


def test_token_required_when_set():
    srv, _ = server.serve("127.0.0.1", 0, "t" * 30)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert call(base, "GET", "/api/jobs")[0] == 401
        assert call(base, "GET", "/api/jobs", token="t" * 30)[0] == 200
    finally:
        srv.shutdown()


def test_public_bind_needs_token():
    with pytest.raises(SystemExit):
        server.serve("0.0.0.0", 0, "")
