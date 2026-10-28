"""v13: work started from a conversation reports back into it; spreadsheets get real figures; coding agents run on the
paired computer only after approval. The model is the scripted stand-in (conftest); the companion is faked here at
the computer.request boundary (the companion side is tested in companion/tests/test_agents.py)."""

import asyncio
import io
import json
import time
import zipfile

import pytest

from conftest import SCRIPT, events, tool_call


def _wait(fn, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


def _updates(client, cid):
    return [m for m in client.get(f"/api/conversations/{cid}").json()["messages"] if m.get("update")]


def test_a_mission_started_in_chat_posts_its_plan_then_its_result(client):
    SCRIPT[:] = [tool_call("start_mission", {"goal": "Write a short note about our office move"}), {"role": "assistant", "content": "Planning it."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Plan the office move note as a mission"}))
    cid = ev[0]["conversation_id"]
    mid = next(e for e in ev if e["type"] == "tool_result")["blocks"][0]["id"]
    # the coordinator loop posts the plan, waiting for approval, into this conversation
    plan = _wait(lambda: _updates(client, cid))
    assert "The plan is ready" in plan[0]["content"] and "Nothing has run yet" in plan[0]["content"]
    job = plan[0]["blocks"][0]
    assert job["type"] == "job" and job["kind"] == "mission" and job["id"] == mid and job["status"] == "review" and job["steps"]
    assert client.get(f"/api/missions/{mid}").json()["status"] == "review"  # nothing ran on its own
    # approving from the conversation is the same endpoint, with the same checks, as the mission page
    assert client.post(f"/api/missions/{mid}/start").status_code == 200
    done = _wait(lambda: len(_updates(client, cid)) >= 2 and _updates(client, cid), 60)
    final = done[1]
    assert final["content"].startswith("**Mission") and final["blocks"][0]["status"] in ("done", "partial", "failed")
    assert client.get(f"/api/conversations/{cid}/jobs").json()["jobs"] == []


def test_research_started_in_chat_posts_the_report(client, monkeypatch):
    from app import coordinator, db, research

    monkeypatch.setattr(research, "spawn", lambda *a: None)  # stand in for the engine (its pipeline: test_research.py)

    SCRIPT[:] = [tool_call("start_research", {"question": "What changed in the 2026 tax rules?"}), {"role": "assistant", "content": "Started."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Research the 2026 tax rules"}))
    cid = ev[0]["conversation_id"]
    rid = next(e for e in ev if e["type"] == "tool_result")["blocks"][0]["id"]
    assert db.one("SELECT 1 FROM jobs WHERE conversation_id = ? AND kind = 'research' AND ref_id = ?", cid, rid)
    time.sleep(0.5)
    assert not _updates(client, cid)  # still running: nothing posted
    db.run("UPDATE research SET status = 'done', report = '## Findings\nThe threshold rose [1].', cancel = 0 WHERE id = ?", rid)
    coordinator.wake()
    up = _wait(lambda: [m for m in _updates(client, cid) if "Research finished" in m["content"]])
    assert "The threshold rose" in up[0]["content"] and up[0]["blocks"][0]["kind"] == "research"
    # a posted result counts as outside content: later persistent actions in this conversation need approval
    from app import assistant

    assert assistant.has_external_content(cid)


def test_nothing_is_posted_while_a_turn_is_running(client):
    from app import assistant, coordinator, db, research

    cid = assistant.new_conversation("web")
    rid = db.run("INSERT INTO research(question, model, status, report, created_at, updated_at) VALUES ('Q here?', 'x', 'done', 'R', ?, ?)",
                 db.now(), db.now())
    coordinator.register(cid, "research", rid, "Q here?")
    assistant._active[cid] = 1
    try:
        assert coordinator.deliver_due() == 0
    finally:
        assistant._active.pop(cid)
    assert _wait(lambda: _updates(client, cid))
    assert coordinator.deliver_due() == 0  # never posted twice
    assert research  # imported for the table


def test_unattended_conversations_get_no_job(client):
    from app import assistant, coordinator

    assert coordinator.register(assistant.new_conversation("schedule"), "research", 1) is None


def _xlsx(rows: list[list]) -> bytes:
    shared: list[str] = []

    def cell(ref, v):
        if isinstance(v, (int, float)):
            return f'<c r="{ref}"><v>{v}</v></c>'
        shared.append(str(v))
        return f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>'
    body = "".join(f'<row r="{i + 1}">' + "".join(cell(f"{chr(65 + j)}{i + 1}", v) for j, v in enumerate(r)) + "</row>" for i, r in enumerate(rows))
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("xl/workbook.xml", '<workbook xmlns:r="r"><sheets><sheet name="Sales" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId1" Type="ws" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{body}</sheetData></worksheet>")
        z.writestr("xl/sharedStrings.xml", "<sst>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
    return b.getvalue()


def _upload(client, name, data, mime):
    return client.post(f"/api/files?name={name}", content=data, headers={"Content-Type": mime}).json()


@pytest.mark.parametrize("kind", ["csv", "xlsx"])
def test_a_spreadsheet_gets_computed_figures_and_a_chart(client, kind):
    rows = [["Region", "Revenue", "Units"], ["North", 1200.5, 10], ["South", 800, 4], ["North", 300, 2], ["East", "", 1]]
    if kind == "csv":
        data = "\n".join(",".join(str(c) for c in r) for r in rows).encode()
        f = _upload(client, "sales.csv", data, "text/csv")
    else:
        f = _upload(client, "sales.xlsx", _xlsx(rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    from app import tables

    a = tables.analyze(f["id"], group_by="Region", value="Revenue")
    rev = next(c for c in a["summary"] if c["column"] == "Revenue")
    assert a["rows"] == 4 and rev["type"] == "number" and rev["sum"] == 2300.5 and rev["empty"] == 1 and rev["max"] == 1200.5
    region = next(c for c in a["summary"] if c["column"] == "Region")
    assert region["type"] == "text" and region["top"][0] == {"value": "North", "count": 2}
    assert a["groups"]["items"][0] == {"label": "North", "value": 1500.5}
    SCRIPT[:] = [tool_call("analyze_table", {"file_id": f["id"], "group_by": "Region", "value": "Revenue"}), {"role": "assistant", "content": "North leads."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Which region sells most?", "file_ids": [f["id"]]}))
    blocks = next(e for e in ev if e["type"] == "tool_result")["blocks"]
    assert [b["type"] for b in blocks] == ["kpis", "table", "chart", "records"]
    assert blocks[0]["items"][0] == {"label": "Rows", "value": 4, "format": "number"}
    if kind == "xlsx":
        assert "North" in client.get(f"/api/files/{f['id']}").json()["text"]  # searchable too


def test_a_bad_column_or_file_says_what_exists(client):
    from fastapi import HTTPException

    from app import tables

    f = _upload(client, "small.csv", b"a,b\n1,2\n", "text/csv")
    with pytest.raises(HTTPException) as e:
        tables.analyze(f["id"], group_by="nope")
    assert "Columns: a, b" in e.value.detail
    t = _upload(client, "words.txt", b"hello", "text/plain")
    with pytest.raises(HTTPException) as e:
        tables.analyze(t["id"])
    assert "not a spreadsheet" in e.value.detail


@pytest.fixture
def companion(monkeypatch):
    """A paired computer that has Codex installed and ready."""
    from app import computer, computer_tools, db

    sent: list[tuple[str, dict, bool]] = []
    state = {"polls": 0}
    dev = {"id": 1, "name": "Test Mac", "capabilities": ["files", "coding"]}

    async def request(kind, args, *, conversation_id=None, mission_id=None, approved=False, unattended=False, timeout=None):
        computer.check(kind, args, approved=approved, unattended=unattended)
        sent.append((kind, args, approved))
        if kind == "agents_detect":
            return {"ok": True, "status": "done", "error": "", "result": {
                "codex": {"installed": True, "ready": True, "sign_in": "chatgpt", "sandbox": "enforced", "billing": "Your ChatGPT plan's Codex usage limits"},
                "claude": {"installed": True, "ready": False, "reason": "ROOKS can run Claude Code only with an Anthropic API key"}}}
        if kind == "agent_start":
            return {"ok": True, "status": "done", "error": "", "result": {"run": "r1", "folder": args["folder"]}}
        if kind == "agent_poll":
            state["polls"] += 1
            done = state["polls"] >= 2
            return {"ok": True, "status": "done", "error": "", "result": {
                "status": "done" if done else "running", "next": 2, "result": "Added README.md with setup steps." if done else "",
                "events": [{"kind": "command", "text": "ls"}] if not done else [{"kind": "final", "text": "Added README.md"}]}}
        if kind == "agent_stop":
            return {"ok": True, "status": "done", "error": "", "result": {"status": "cancelled"}}
        raise AssertionError(kind)
    monkeypatch.setattr(computer, "request", request)
    monkeypatch.setattr(computer, "online_device", lambda: dev)
    monkeypatch.setattr(computer_tools, "available", lambda ctx: ctx.channel not in computer_tools.UNATTENDED)
    monkeypatch.setattr(computer_tools, "_caps", lambda: set(dev["capabilities"]))
    from app import coding

    monkeypatch.setattr(coding, "POLL_SECONDS", 0.2)
    db.set_setting("coding_agents", "{}")
    computer.update_settings(enabled=True)
    yield sent
    computer.update_settings(enabled=False)


def test_a_coding_task_always_waits_for_approval_then_reports_back(client, companion):
    SCRIPT[:] = [tool_call("coding_agents", {}, "a"), tool_call("run_coding_agent", {"task": "Add a README", "folder": "/Users/me/app"}, "b"),
                 {"role": "assistant", "content": "It needs your approval."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "Use Codex to add a README to my app"}))
    cid = ev[0]["conversation_id"]
    appr = next(e for e in ev if e["type"] == "approval")
    assert appr["name"] == "run_coding_agent"
    assert not any(k == "agent_start" for k, _, _ in companion)  # not started by the model's call
    a = next(x for x in client.get("/api/approvals").json() if x["id"] == appr["id"])
    assert "Codex will change files in /Users/me/app" in a["reason"] and "ChatGPT plan" in a["reason"] and "spending limit does not cover it" in a["reason"]
    r = client.post(f"/api/approvals/{appr['id']}", json={"approve": True}).json()
    assert r["status"] == "approved", r
    start = next((k, args, approved) for k, args, approved in companion if k == "agent_start")
    assert start[2] is True and start[1]["agent"] == "codex"
    up = _wait(lambda: [m for m in _updates(client, cid) if "finished" in m["content"]])
    assert "Added README.md with setup steps." in up[0]["content"] and up[0]["blocks"][0]["kind"] == "coding"
    rid = up[0]["blocks"][0]["id"]
    assert client.get(f"/api/coding/{rid}").json()["status"] == "done"


def test_a_coding_task_cannot_start_without_approval_or_from_unattended_runs(client, companion):
    from fastapi import HTTPException

    from app import assistant, coding

    ctx = assistant.Ctx(channel="web", conversation_id=assistant.new_conversation("web"))
    with pytest.raises(HTTPException) as e:
        asyncio.run(coding.start("x", "/a", "codex", ctx))
    assert "approves" in e.value.detail
    ctx = assistant.Ctx(channel="schedule", conversation_id=assistant.new_conversation("schedule"), approved=True)
    assert "run_coding_agent" not in [t.name for t in assistant.available_tools(ctx)]
    with pytest.raises(HTTPException):
        asyncio.run(coding.start("x", "/a", "codex", ctx))
    assert not any(k == "agent_start" for k, _, _ in companion)


def test_an_agent_that_is_not_ready_explains_why(client, companion):
    from fastapi import HTTPException

    from app import assistant, coding

    ctx = assistant.Ctx(channel="web", conversation_id=assistant.new_conversation("web"), approved=True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(coding.start("x", "/a", "claude", ctx))
    assert "only with an Anthropic API key" in e.value.detail


def test_coding_agents_endpoint_without_a_computer(client):
    r = client.get("/api/coding/agents").json()
    assert r["agents"] == {} and "No computer is paired" in r["reason"]


def test_turned_off_computer_use_refuses_a_coding_task(client, companion):
    from fastapi import HTTPException

    from app import assistant, coding, computer

    computer.update_settings(enabled=False)
    ctx = assistant.Ctx(channel="web", conversation_id=assistant.new_conversation("web"), approved=True)
    with pytest.raises(HTTPException) as e:
        asyncio.run(coding.start("x", "/a", "codex", ctx))
    assert "turned off" in str(e.value.detail)
    assert json.dumps(companion)  # nothing reached agent_start
    assert not any(k == "agent_start" for k, _, _ in companion)


def test_a_check_in_asked_for_in_chat_waits_for_approval_and_keeps_quiet_hours(client):
    from app import db

    SCRIPT[:] = [tool_call("create_checkin", {"name": "Deadlines", "sources": "tasks,calendar", "cron": "0 8 * * 1-5", "notify": "call",
                                              "quiet_start": "21:00", "quiet_end": "07:30"}), {"role": "assistant", "content": "Waiting for you."}]
    before = db.one("SELECT COUNT(*) AS n FROM topics")["n"]
    ev = events(client.post("/api/assistant/chat", json={"message": "Check in with me on weekday mornings about deadlines"}))
    appr = next(e for e in ev if e["type"] == "approval")
    assert appr["name"] == "create_checkin" and db.one("SELECT COUNT(*) AS n FROM topics")["n"] == before
    a = next(x for x in client.get("/api/approvals").json() if x["id"] == appr["id"])
    assert "may interrupt you" in a["reason"]
    assert client.post(f"/api/approvals/{appr['id']}", json={"approve": True}).json()["status"] == "approved"
    t = db.one("SELECT * FROM topics ORDER BY id DESC LIMIT 1")
    assert t["name"] == "Deadlines" and t["quiet_start"] == "21:00" and t["quiet_end"] == "07:30" and t["notify"] == "call"


def test_stop_ends_research_at_once_even_during_a_slow_search(client, monkeypatch):
    from app import db, research, search

    async def stuck(q, n=8):
        await asyncio.sleep(120)
        return []
    monkeypatch.setattr(search, "web_search_all", stuck)
    r = client.post("/api/research", json={"question": "Slow search question here", "depth": 1}).json()
    _wait(lambda: "Searching" in (db.one("SELECT progress FROM research WHERE id = ?", r["id"])["progress"] or ""))
    started = time.time()
    assert client.post(f"/api/research/{r['id']}/cancel").status_code == 200
    _wait(lambda: db.one("SELECT status FROM research WHERE id = ?", r["id"])["status"] == "cancelled", 10)
    assert time.time() - started < 5
    # a later failure of the stopped run cannot overwrite it
    research._end(r["id"], status="error", error="late")
    assert db.one("SELECT status FROM research WHERE id = ?", r["id"])["status"] == "cancelled"
