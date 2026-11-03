"""Work sites (Workbench's Sites page): sign in once in a window, then read, download and submit with that session.

Offline: a fake portal that only answers signed-in visitors (a session-only cookie, like most real portals)."""

import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

from test_server import api, call  # noqa: F401  (fixture)

STATE = {"updates": []}


class Portal(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _signed_in(self):
        return "sid=s3cr3t" in (self.headers.get("Cookie") or "")

    def _html(self, body, code=200, extra=None):
        data = f"<!doctype html><html><body>{body}</body></html>".encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, to, cookie=None):
        self.send_response(303)
        self.send_header("Location", to)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/login"):
            return self._html('<h1>Sign in</h1><form method="post" action="/login"><label>User <input name="user"></label>'
                              '<label>Password <input type="password" name="pw"></label><button>Sign in</button></form>')
        if not self._signed_in():
            return self._redirect("/login")
        if self.path.startswith("/jobs"):
            rows = "".join(f"<li>{u}</li>" for u in STATE["updates"])
            return self._html('<h1>Jobs</h1><table><tr><td>J-101</td><td>Fit-out level 3</td><td>Due 2026-11-04</td></tr></table>'
                              '<form method="get" action="/search"><input name="q"></form>'
                              '<form method="post" action="/jobs/update"><input name="job" value="J-101">'
                              '<input name="status"><button>Save</button></form>'
                              f'<ul id="log">{rows}</ul><a href="/report.csv">Report</a>')
        if self.path.startswith("/report.csv"):
            data = b"job,status\nJ-101,open\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header("Content-Disposition", 'attachment; filename="weekly report.csv"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self._html("not found", 404)

    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        if self.path == "/login":
            if form.get("user") == ["sam"] and form.get("pw") == ["hunter2"]:
                return self._redirect("/jobs", "sid=s3cr3t; Path=/; HttpOnly")  # session-only cookie
            return self._redirect("/login?bad=1")
        if self.path == "/jobs/update" and self._signed_in():
            STATE["updates"].append(f"{form.get('job', [''])[0]}: {form.get('status', [''])[0]}")
            return self._redirect("/jobs")
        self._html("forbidden", 403)


@pytest.fixture
def portal():
    STATE["updates"].clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Portal)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_sign_in_once_then_read_download_and_submit_with_approval(api, portal, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ROOKS_BROWSER_SITE_HEADLESS", "1")
    base, jobs = api

    # Not signed in yet: the portal sends us to its sign-in page
    code, p = call(base, "POST", "/api/site/page", {"url": f"{portal}/jobs", "profile": "site-7"})
    assert code == 200 and "/login" in p["url"] and "Sign in" in p["html"]

    # Sign in once, in the window (the test plays the person)
    code, w = call(base, "POST", "/api/site/login", {"url": f"{portal}/login", "profile": "site-7"})
    assert code == 200 and w["status"] == "open", w
    page = jobs.sites.windows["site-7"]["page"]

    async def person():
        await page.get_by_label("User").fill("sam")
        await page.get_by_label("Password").fill("hunter2")
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_url("**/jobs")

    jobs.sites.run(person(), 30)
    code, c = call(base, "POST", "/api/site/close", {"profile": "site-7"})
    assert code == 200 and c["status"] == "closed"
    session = jobs.sites._state_file("site-7")
    assert session.exists() and "hunter2" not in session.read_text()  # cookies are kept, the password never is

    # The session is reused (even the session-only cookie) after the window closed
    code, p = call(base, "POST", "/api/site/page", {"url": f"{portal}/jobs", "profile": "site-7"})
    assert code == 200 and p["url"].endswith("/jobs") and "Fit-out level 3" in p["html"] and p["status"] == 200

    code, d = call(base, "POST", "/api/site/download", {"url": f"{portal}/report.csv", "profile": "site-7"})
    assert code == 200 and d["name"] == "weekly report.csv" and base64.b64decode(d["data"]) == b"job,status\nJ-101,open\n"

    # Submitting changes the site: refused without the person's OK
    body = {"url": f"{portal}/jobs", "profile": "site-7", "form_index": 1, "values": {"job": "J-101", "status": "Done"}}
    code, r = call(base, "POST", "/api/site/submit", body)
    assert code == 403 and STATE["updates"] == []
    code, r = call(base, "POST", "/api/site/submit", {**body, "approved": True})
    assert code == 200 and r["url"].endswith("/jobs") and "J-101: Done" in r["html"] and STATE["updates"] == ["J-101: Done"]
    code, r = call(base, "POST", "/api/site/submit", {**body, "form_index": 9, "approved": True})
    assert code == 400 and "form" in r["detail"]

    # Another site's profile has no session; bad profile names are refused
    code, p = call(base, "POST", "/api/site/page", {"url": f"{portal}/jobs", "profile": "site-8"})
    assert "/login" in p["url"]
    assert call(base, "POST", "/api/site/page", {"url": f"{portal}/jobs", "profile": "../x"})[0] == 400
    assert call(base, "POST", "/api/site/page", {"url": "file:///etc/passwd", "profile": "site-7"})[0] == 400
    jobs.sites.run(jobs.sites.shutdown(), 30)


def test_closing_the_window_keeps_the_session(api, portal, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ROOKS_BROWSER_SITE_HEADLESS", "1")
    base, jobs = api
    call(base, "POST", "/api/site/login", {"url": f"{portal}/login", "profile": "p1"})
    page = jobs.sites.windows["p1"]["page"]

    async def person():
        await page.get_by_label("User").fill("sam")
        await page.get_by_label("Password").fill("hunter2")
        await page.get_by_role("button", name="Sign in").click()
        await page.wait_for_url("**/jobs")
        await page.close()  # the person just closes the window

    jobs.sites.run(person(), 30)
    end = time.time() + 10
    while "p1" in jobs.sites.windows and time.time() < end:
        time.sleep(0.1)
    code, p = call(base, "POST", "/api/site/page", {"url": f"{portal}/jobs", "profile": "p1"})
    assert code == 200 and "Fit-out level 3" in p["html"]
    jobs.sites.run(jobs.sites.shutdown(), 30)
