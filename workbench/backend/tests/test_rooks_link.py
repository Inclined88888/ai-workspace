"""The rooks terminal shares the app's memory and skills, and its requests teach them (rooks_link.py)."""


import pytest

KEY = "k" * 32


@pytest.fixture
def auth(monkeypatch):
    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", KEY)
    return {"Authorization": f"Bearer {KEY}"}


def test_needs_the_bridge_key(client, auth):
    assert client.get("/v1/rooks/context").status_code == 401
    assert client.get("/v1/rooks/context", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/rooks/context", headers=auth).status_code == 200


def test_skills_index_and_steps_reach_the_terminal(client, auth):
    from app import db, learn

    learn.upsert_skill("weekly-sales-report", "When asked for the weekly sales report", "1. read sales.csv\n2. sum by week")
    try:
        ctx = client.get("/v1/rooks/context", params={"q": "sales"}, headers=auth).json()
        assert {"name": "weekly-sales-report", "description": "When asked for the weekly sales report"} in ctx["skills"]
        got = client.get("/v1/rooks/skills/weekly-sales-report", headers=auth).json()
        assert got["steps"].startswith("1. read sales.csv")
        assert db.one("SELECT uses FROM skills WHERE name = 'weekly-sales-report'")["uses"] == 1
        assert client.get("/v1/rooks/skills/nope", headers=auth).status_code == 404
    finally:
        db.run("DELETE FROM skills WHERE name = 'weekly-sales-report'")


def test_a_fact_saved_in_the_terminal_is_the_one_chat_sees(client, auth):
    from app.adapt import knowledge

    r = client.post("/v1/rooks/remember", headers=auth, json={"content": "Prefers tabs over spaces in Python files", "kind": "preference"})
    assert r.status_code == 200 and r.json().get("saved")
    assert any("tabs over spaces" in f["content"] for f in knowledge.all_facts())
    again = client.post("/v1/rooks/remember", headers=auth, json={"content": "Prefers tabs over spaces in Python files"}).json()
    assert again["saved"] is None  # already known


def test_a_terminal_request_lands_in_chat_history_and_is_learned_from(client, auth, monkeypatch):
    from app import db, learn

    seen = []

    async def fake_after(cid, tool_calls, channel):
        seen.append((cid, tool_calls, channel))

    monkeypatch.setattr(learn, "_after", fake_after)
    monkeypatch.setattr(learn, "enabled", lambda: True)
    body = {"session": "20261103-abc", "prompt": "add up the totals in bank.csv", "answer": "The total is 42.",
            "calls": [{"name": "read_file", "arguments": "{\"path\": \"bank.csv\"}", "result": "1|a,40\n2|b,2"},
                      {"name": "run_command", "arguments": "{\"command\": \"python sum.py\"}", "result": "42"},
                      {"name": "write_file", "arguments": "{}", "result": "ok"}]}
    first = client.post("/v1/rooks/turn", headers=auth, json=body).json()
    second = client.post("/v1/rooks/turn", headers=auth, json={**body, "prompt": "now by month", "calls": [], "untrusted": True}).json()
    cid = first["conversation_id"]
    assert second["conversation_id"] == cid  # one terminal session is one conversation
    conv = db.one("SELECT * FROM conversations WHERE id = ?", cid)
    assert conv["channel"] == "cli" and conv["title"] == "Terminal: add up the totals in bank.csv"
    rows = db.all("SELECT role, name FROM messages WHERE conversation_id = ? ORDER BY id", cid)
    assert [r["role"] for r in rows][:2] == ["user", "assistant"] and rows[2]["name"] == "read_file"
    listed = [c["id"] for c in client.get("/api/conversations", headers={"X-Workbench": "1"}).json()]
    assert cid in listed  # visible in the app's chat history
    assert (cid, 3, "rooks-code") in seen and (cid, 0, "rooks-code") in seen  # web-reading turn: facts only, no skill
    db.run("DELETE FROM conversations WHERE id = ?", cid)
