"""Structured answer blocks: built by code from real tool results, never from the model's text; malformed or failed
results give no block; links are http(s) only; stored conversations rebuild the same blocks; truncate for edit."""

import json

from conftest import SCRIPT, events, tool_call


def test_blocks_come_from_tool_data_and_reject_bad_input():
    from app import blocks

    t = blocks.from_tool("list_tasks", {}, True, json.dumps([{"id": 3, "title": "Pay rent", "due": "2026-11-01", "status": "todo", "secret": "x"}]))
    assert t == [{"type": "records", "kind": "task", "title": "Tasks", "items": [
        {"id": 3, "title": "Pay rent", "due": "2026-11-01", "status": "todo", "priority": None, "project": ""}]}]  # only known fields
    assert blocks.from_tool("list_tasks", {}, False, "[]") == []                       # a failed tool shows no block
    assert blocks.from_tool("list_tasks", {}, True, "not json") == []                  # unparseable: text only
    assert blocks.from_tool("get_usage", {}, True, json.dumps({"days": 3})) == []      # wrong shape: nothing half built
    assert blocks.from_tool("some_unknown_tool", {}, True, "{}") == []
    src = blocks.from_tool("web_search", {}, True, json.dumps([
        {"title": "ok", "url": "https://example.org/a"}, {"title": "bad", "url": "javascript:alert(1)"},
        {"title": "data", "url": "data:text/html,<script>"}, {"title": "<img src=x onerror=alert(1)>", "url": "http://x.example/b"}]))
    urls = [s["url"] for s in src[0]["items"]]
    assert urls == ["https://example.org/a", "http://x.example/b"]
    assert src[0]["items"][1]["title"] == "<img src=x onerror=alert(1)>"  # kept as text; the frontend renders text, never HTML


def test_usage_blocks_show_only_recorded_figures(client):
    from app import blocks, db, overview

    db.run("DELETE FROM usage")
    empty = blocks.from_tool("get_usage", {}, True, json.dumps(overview.get_usage(30)))
    assert empty == [{"type": "notice", "tone": "info", "text": "No model use was recorded in the last 30 days."}]  # no chart from nothing
    for day, cents, model in (("2026-10-25", 1.5, "a"), ("2026-10-26", 2.0, "a"), ("2026-10-26", 4.0, "b")):
        db.run("INSERT INTO usage(ts, model, channel, prompt_tokens, completion_tokens, cost_cents, priced) VALUES (?, ?, 'web', 10, 5, ?, 1)",
               f"{day}T10:00:00+00:00", model, cents)
    u = overview.get_usage(3650)
    b = blocks.from_tool("get_usage", {}, True, json.dumps(u))
    kpis = next(x for x in b if x["type"] == "kpis")["items"]
    assert kpis[0]["value"] == 7.5 and kpis[1]["value"] == 3
    line = next(x for x in b if x["type"] == "chart" and x["kind"] == "line")
    assert line["points"] == [{"label": "2026-10-25", "value": 1.5}, {"label": "2026-10-26", "value": 6.0}]
    bar = next(x for x in b if x["type"] == "chart" and x["kind"] == "bar")
    assert bar["points"][0] == {"label": "b", "value": 4.0}
    db.run("DELETE FROM usage")


def test_a_turn_streams_blocks_and_the_stored_conversation_rebuilds_them_then_truncates(client):
    client.post("/api/tasks", json={"title": "BLK file the report", "due": "2026-11-02"})
    SCRIPT[:] = [tool_call("list_tasks", {}, "l1"), {"role": "assistant", "content": "You have one task: file the report."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "What tasks do I have?"}))
    res = next(e for e in ev if e["type"] == "tool_result")
    assert res["blocks"][0]["kind"] == "task" and any(i["title"] == "BLK file the report" for i in res["blocks"][0]["items"])
    cid = next(e["conversation_id"] for e in ev if e["type"] == "start")
    conv = client.get(f"/api/conversations/{cid}").json()
    tool_rows = [m for m in conv["messages"] if m["role"] == "tool"]
    assert conv["blocks_version"] == 1 and tool_rows[0]["blocks"] == res["blocks"]
    # edit and resend: removes the user message and everything after it
    first_user = next(m for m in conv["messages"] if m["role"] == "user")
    assert client.post(f"/api/conversations/{cid}/truncate", json={"from_message_id": tool_rows[0]["id"]}).status_code == 400
    assert client.post(f"/api/conversations/{cid}/truncate", json={"from_message_id": first_user["id"]}).json()["removed"] == 4
    assert client.get(f"/api/conversations/{cid}").json()["messages"] == []
    assert any(t["title"] == "BLK file the report" for t in client.get("/api/tasks").json())  # what was done stays
    SCRIPT.clear()


def test_overview_tools_are_read_only_and_start_tools_are_gated(client):
    from app import assistant, permissions

    for t in ("get_usage", "list_models", "project_status", "list_missions"):
        assert permissions.TOOL_RISK[t] == permissions.READ
    for t in ("start_mission", "start_research"):
        assert permissions.TOOL_RISK[t] == permissions.WRITE and t in assistant.GATED_TOOLS
    ctx = assistant.Ctx(channel="mission", conversation_id=0)
    names = {t.name for t in assistant.available_tools(ctx)}
    assert "start_mission" not in names and "start_research" not in names  # unattended work cannot start more work
