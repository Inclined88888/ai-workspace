"""Sites: any website, no per-site code. Four different fake sites are discovered, tracked, scheduled and acted on."""

import asyncio
import json

import pytest

import fake_sites
from conftest import SCRIPT, events, tool_call


@pytest.fixture(scope="module")
def portal():
    srv, url = fake_sites.start()
    yield url
    srv.shutdown()


@pytest.fixture(autouse=True)
def fresh():
    fake_sites.reset()
    yield


def run(coro):
    return asyncio.run(coro)


def _site(client, url, name):
    r = client.post("/api/sites", json={"name": name, "url": url, "mode": "http"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_job_tracker_is_understood_without_a_recipe(client, portal):
    from app import sites

    sid = _site(client, f"{portal}/jobs", "TradeTrack")
    res = client.post(f"/api/sites/{sid}/check").json()
    assert res["status"] == "ok" and "Jobs" in res["collections"]
    assert "First look at TradeTrack" in res["digest"]
    recs = {r["key"]: r for r in sites.records(sid, "Jobs")}
    j = recs["J-1002"]
    assert j["status"] == "Quote" and j["due"] == "2026-10-25" and j["start"] == "2026-10-20" and j["person"] == "Sam"
    assert j["overdue"] and not recs["J-1003"]["overdue"]  # complete jobs are never overdue
    assert j["url"].endswith("/jobs/J-1002") and "12 Lee St" in json.dumps(j["fields"])  # detail page merged in
    s = sites.get(sid)
    assert any(f["submit"] == "Save status" for f in s["structure"]["forms"])
    assert any(f["url"].endswith("materials.csv") for f in s["structure"]["files"])
    assert not fake_sites.STATE.get("logged_out") and not fake_sites.STATE.get("deleted")  # danger links never followed
    assert not fake_sites.SUBMITS  # reading never submits anything


def test_changes_become_a_plain_english_digest(client, portal):
    sid = _site(client, f"{portal}/jobs", "Digest test")
    client.post(f"/api/sites/{sid}/check")
    fake_sites.STATE["jobs"][0]["status"] = "In progress"
    fake_sites.STATE["jobs"].append({"no": "J-1004", "customer": "Ng Florist", "desc": "Install exhaust fan", "status": "Scheduled",
                                     "scheduled": "2026-11-04", "due": "2026-11-05", "tech": "Sam"})
    fake_sites.STATE["jobs"].pop(2)
    res = client.post(f"/api/sites/{sid}/check").json()
    d = res["digest"]
    assert "New job: Install exhaust fan" in d or "New job: J-1004" in d or "J-1004" in d
    assert "Scheduled → In progress" in d
    assert "No longer listed" in d
    assert "Overdue" in d


def test_roster_behind_sign_in(client, portal):
    from app import sites

    sid = _site(client, f"{portal}/roster", "ShiftHub")
    res = client.post(f"/api/sites/{sid}/check").json()
    assert res["status"] == "needs_sign_in" and "sign in" in res["digest"].lower()
    # after the user signs in once (the browser profile keeps the cookie), the same check reads the shifts
    f = sites.HttpFetcher(sites.get(sid))
    f.client.cookies.set("session", "signed-in-in-the-browser-profile")
    res = run(sites.check(sid, f))
    run(f.close())
    assert res["status"] == "ok", res
    recs = sites.records(sid)
    assert len(recs) == 3 and recs[0]["start"] == "2026-11-02" and recs[0]["location"] == "Kangaroo Point"
    assert recs[0]["title"] == "Site supervisor"


def test_project_portal_lists_and_tables(client, portal):
    from app import sites

    sid = _site(client, f"{portal}/project", "BuildSpace")
    res = client.post(f"/api/sites/{sid}/check").json()
    assert res["status"] == "ok"
    cols = set(res["collections"])
    assert "Schedule" in cols and "Requests for information" in cols, cols
    tasks = {r["title"]: r for r in sites.records(sid, "Schedule")}
    assert tasks["Order windows"]["finish"] == "2026-10-16" and tasks["Order windows"]["status"] == "Late"
    rfi = {r["key"]: r for r in sites.records(sid, "Requests for information")}["RFI-07"]
    assert rfi["due"] == "2026-10-28" and rfi["overdue"]
    tl = client.get(f"/api/sites/{sid}/timeline").json()
    assert tl[0]["start"] <= tl[-1]["start"] and any(i["title"] == "Pour level 3 slab" and i["end"] == "2026-11-06" for i in tl)
    ics = client.get(f"/api/sites/{sid}/calendar.ics")
    assert ics.status_code == 200 and "BEGIN:VEVENT" in ics.text and "Pour level 3 slab" in ics.text
    first = client.post(f"/api/sites/{sid}/to-calendar").json()
    again = client.post(f"/api/sites/{sid}/to-calendar").json()
    assert first["created"] >= 3 and again["created"] == 0


def test_unfamiliar_app_and_file_download(client, portal):
    from app import analysis, sites

    sid = _site(client, f"{portal}/lab", "LIMS")
    client.post(f"/api/sites/{sid}/check")
    recs = {r["key"]: r for r in sites.records(sid)}
    s = recs["S-501"]
    assert s["person"] == "Dr Nguyen" and s["start"] == "2026-10-19" and s["due"] == "2026-10-26" and s["status"] == "Pending"
    forms = sites.get(sid)["structure"]["forms"]
    assert [f["submit"] for f in forms] == ["Record"]  # the search box is not an action
    jid = _site(client, f"{portal}/jobs", "Files from a site")
    client.post(f"/api/sites/{jid}/check")
    f = client.post(f"/api/sites/{jid}/fetch-file", json={"url": f"{portal}/files/materials.csv"}).json()
    assert f["name"] == "materials.csv" and f["source"] == f"site:{jid}"
    assert "Tempering valve" in str(analysis.read(f["id"]))
    assert client.post(f"/api/sites/{jid}/fetch-file", json={"url": "https://elsewhere.example/x.pdf"}).status_code == 400


def test_actions_need_approval_every_time(client, portal):
    sid = _site(client, f"{portal}/jobs", "Act")
    client.post(f"/api/sites/{sid}/check")
    a = client.post(f"/api/sites/{sid}/actions", json={"form": "Save status", "values": {"New status": "Complete", "note": "Done today"}}).json()
    assert a["status"] == "pending" and "New status = Complete" in a["summary"]
    assert not fake_sites.SUBMITS
    bad = client.post(f"/api/sites/{sid}/actions", json={"form": "Save status", "values": {"New status": "Exploded"}})
    assert bad.status_code == 400 and "not a choice" in bad.json()["detail"]
    d = client.post(f"/api/site-actions/{a['id']}", json={"approve": True}).json()
    assert d["status"] == "done" and fake_sites.SUBMITS[-1]["data"]["status"] == "Complete"
    assert client.post(f"/api/site-actions/{a['id']}", json={"approve": True}).status_code == 409
    a2 = client.post(f"/api/sites/{sid}/actions", json={"form": "Save status", "values": {"status": "Quote"}}).json()
    assert client.post(f"/api/site-actions/{a2['id']}", json={"approve": False}).json()["status"] == "denied"
    assert len(fake_sites.SUBMITS) == 1


def test_assistant_site_action_is_always_held(client, portal):
    from app import sites

    sid = _site(client, f"{portal}/jobs", "Chat act")
    run(sites.check(sid))
    SCRIPT[:] = [tool_call("site_action", {"site_id": sid, "form": "Save status", "values": json.dumps({"status": "Complete"})}, "a"),
                 {"role": "assistant", "content": "Waiting for your OK."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "mark J-1001 complete"}))
    held = [e for e in ev if e["type"] == "approval_required"]
    assert [h["tool"] for h in held] == ["site_action"] and not fake_sites.SUBMITS
    ok = client.post(f"/api/approvals/{held[0]['id']}", json={"approve": True}).json()
    assert ok["status"] == "done" and fake_sites.SUBMITS and fake_sites.SUBMITS[-1]["data"]["status"] == "Complete"


def test_plain_english_watch(client, portal):
    from app import db, scheduler, sites

    r = sites.parse_request("check my portal every morning and tell me which jobs are overdue")
    assert r["cron"] == "0 7 * * *" and r["show"] == ["overdue"] and r["about"] == "job"
    assert sites.parse_request("every weekday at 6:30am show new shifts")["cron"] == "30 6 * * 1-5"
    assert sites.parse_request("every 2 hours tell me what changed")["cron"].startswith("0 */2")
    assert sites.parse_request("on Fridays at 4pm list upcoming tasks")["cron"] == "0 16 * * 5"
    assert sites.describe_cron("30 6 * * 1-5") == "every weekday at 6:30 AM"
    sid = _site(client, f"{portal}/jobs", "My portal")
    w = client.post("/api/sites/watches", json={"request": "check My portal every morning and tell me which jobs are overdue"}).json()
    assert w["site_id"] == sid and w["in_words"] == "every day at 7:00 AM" and scheduler.validate(w["cron"]) == "0 7 * * *"
    text = client.post(f"/api/sites/watches/{w['id']}/run").json()["result"]
    assert "Overdue (1)" in text and "Quote for split system" in text and "Smith" not in text
    assert db.one("SELECT * FROM inbox WHERE source = ?", f"site_watch:{w['id']}")


def test_servicem8_recipe_uses_the_official_api(client, monkeypatch):
    import httpx

    from app import sites

    seen = {}

    def handler(req: httpx.Request):
        seen.setdefault("keys", []).append(req.headers.get("X-API-Key"))
        if req.url.path.endswith("/job.json"):
            return httpx.Response(200, json=[{"uuid": "u1", "generated_job_id": "88", "job_description": "Fix pump", "status": "Work Order",
                                              "date": "2026-11-03 00:00:00", "job_address": "1 Main St", "active": 1}])
        return httpx.Response(200, json=[])

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    s = client.post("/api/sites", json={"name": "ServiceM8", "url": "https://go.servicem8.com", "api_key": "smk-test-123456"}).json()
    assert s["recipe"] == "servicem8" and s["has_key"] and "secret_enc" not in s and "Settings → API Keys" in s["recipe_info"]["help"]
    res = run(sites.check(s["id"]))
    assert res["status"] == "ok" and set(seen["keys"]) == {"smk-test-123456"}
    job = sites.records(s["id"], "Jobs")[0]
    assert job["title"] == "Fix pump" and job["status"] == "Work Order" and job["location"] == "1 Main St"
    raw = sites.db.one("SELECT secret_enc FROM sites WHERE id = ?", s["id"])["secret_enc"]
    assert "smk-test-123456" not in raw
