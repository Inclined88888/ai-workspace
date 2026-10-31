"""Recording a person's actions for Workbench's "Show me how": events by meaning, passwords never captured."""

import asyncio
import time

from test_server import api, call  # noqa: F401  (fixture)


def drive(jobs, coro):
    return asyncio.run_coroutine_threadsafe(coro, jobs.loop).result(30)


def until(base, rid, n, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        _, r = call(base, "GET", f"/api/recordings/{rid}")
        if len(r["events"]) >= n:
            return r
        time.sleep(0.1)
    return r


def test_recording_captures_steps_by_meaning_and_hides_passwords(api, site, monkeypatch):  # noqa: F811
    monkeypatch.setenv("ROOKS_BROWSER_RECORD_HEADLESS", "1")
    base, jobs = api
    code, rec = call(base, "POST", "/api/recordings", {"start_url": f"{site}/contact.html"})
    assert code == 200 and rec["status"] == "recording", rec
    page = jobs.recordings.items[rec["id"]].page

    async def person():
        await page.get_by_label("Your email").fill("sam@example.com")
        await page.get_by_label("Your email").dispatch_event("change")
        await page.get_by_label("Message").fill("Where is my order?")
        await page.get_by_label("Message").dispatch_event("change")
        await page.get_by_role("button", name="Send message").click()
        await page.wait_for_url("**/sent.html*")
        await page.goto(f"{site}/login.html")
        await page.get_by_label("Password").fill("hunter2-secret")
        await page.get_by_label("Password").dispatch_event("change")

    drive(jobs, person())
    r = until(base, rec["id"], 7)
    code, r = call(base, "POST", f"/api/recordings/{rec['id']}/stop")
    assert code == 200 and r["status"] == "stopped"
    ev = r["events"]
    kinds = [e["kind"] for e in ev]
    assert kinds[0] == "navigate" and "contact.html" in ev[0]["url"]
    typed = [e for e in ev if e["kind"] == "type"]
    assert typed[0]["target"]["label"] == "Your email" and typed[0]["value"] == "sam@example.com"
    msg = [e for e in typed if e["target"].get("label") == "Message"]
    assert msg and msg[-1]["target"]["role"] == "textbox" and msg[-1]["value"] == "Where is my order?"
    click = next(e for e in ev if e["kind"] == "click")
    assert click["target"] == {**click["target"], "role": "button", "name": "Send message"}
    assert "submit" in kinds and any(e["kind"] == "navigate" and "sent.html" in e["url"] for e in ev)
    pw = [e for e in ev if e["kind"] == "type" and e["target"].get("label") == "Password"]
    assert pw and pw[0]["secret"] is True and not pw[0].get("value")
    assert "hunter2" not in str(ev)  # the password never leaves the page
    assert call(base, "GET", "/api/recordings/nope")[0] == 404
