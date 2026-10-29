"""R2: one run log. Every model call, tool call, turn and approval decision is a row in `actions`."""

import sqlite3
from pathlib import Path

import pytest
from conftest import SCRIPT, events, tool_call

LEGACY = ("usage", "tool_runs", "turn_checks", "pending_actions")
V0 = (Path(__file__).parent / "fixtures" / "schema_v0.sql").read_text()


def _legacy_db(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(V0)
    c.execute("INSERT INTO conversations(id, title, channel, created_at, updated_at) VALUES (1, 'old', 'web', 't', 't')")
    c.execute("INSERT INTO usage(ts, model, agent_id, conversation_id, channel, prompt_tokens, completion_tokens, cost_cents, priced) "
              "VALUES ('2026-10-01T00:00:00+00:00', 'gpt-x', 7, 1, 'agent', 1000, 500, 1.05, 1), "
              "('2026-10-02T00:00:00+00:00', 'qwen3:4b', NULL, 1, 'web', 900, 50, 0, 0)")
    c.execute("INSERT INTO tool_runs(conversation_id, model, tool, outcome, verified, evidence, from_text, ms, created_at) "
              "VALUES (1, '1/qwen3:4b', 'create_task', 'verified', 1, 'task #3 saved', 0, 12, '2026-10-02T00:00:01+00:00')")
    c.execute("INSERT INTO turn_checks(conversation_id, model, outcome, detail, created_at) VALUES (1, '1/qwen3:4b', 'unbacked', 'said done', 't')")
    for status, decided, result in (("pending", None, ""), ("denied", None, ""), ("denied", "2026-10-03T00:00:00+00:00", ""),
                                    ("done", "2026-10-03T00:00:00+00:00", '{"saved": 9}')):
        c.execute("INSERT INTO pending_actions(conversation_id, tool, args, effect, reason, status, result, created_at, decided_at) "
                  "VALUES (1, 'remember', '{\"content\": \"x\"}', 'persistent', 'untrusted', ?, ?, '2026-10-03T00:00:00+00:00', ?)",
                  (status, result, decided))
    return c


def test_migration_moves_every_legacy_row_and_backs_up_first(client, tmp_path):
    from app import db

    path = tmp_path / "workbench.db"
    c = _legacy_db(path)
    db.migrate(c, path)
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not names & set(LEGACY) and "actions" in names
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    backups = list(tmp_path.glob("workbench.db.pre-v1-*.bak"))
    assert len(backups) == 1 and sqlite3.connect(backups[0]).execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 2

    rows = [dict(r) for r in c.execute("SELECT * FROM actions ORDER BY id")]
    calls = [r for r in rows if r["kind"] == "model_call"]
    assert [(r["model"], r["agent_id"], r["tokens_in"], r["usd_cost"]) for r in calls] == [("gpt-x", 7, 1000, 0.0105), ("qwen3:4b", None, 900, None)]
    assert [r["outcome"] for r in rows if r["kind"] == "turn"] == ["unbacked"]
    tools = [r for r in rows if r["kind"] == "tool_call"]
    # 1 tool run + 4 held/refused calls + 1 executed approval
    assert sorted(r["outcome"] for r in tools) == ["denied", "held", "held", "held", "ok", "verified"]
    decisions = [r for r in rows if r["kind"] == "decision"]
    assert sorted(r["outcome"] for r in decisions) == ["approved", "denied"]
    assert all(r["request_id"] and len(r["request_id"]) == 32 for r in rows)
    assert len({r["request_id"] for r in rows}) == len(rows)
    # rerunning is a no-op: no second backup, no duplicated rows
    db.migrate(c, path)
    assert c.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == len(rows)
    assert len(list(tmp_path.glob("*.bak"))) == 1


def test_a_failed_migration_leaves_the_legacy_data_untouched(client, tmp_path, monkeypatch):
    from app import db

    path = tmp_path / "workbench.db"
    c = _legacy_db(path)

    def boom(conn, legacy):
        conn.execute("INSERT INTO actions(ts, kind, outcome, request_id) VALUES ('t', 'turn', 'x', 'partial')")
        raise RuntimeError("disk full")

    monkeypatch.setattr(db, "migrate_v1", boom)
    with pytest.raises(RuntimeError):
        db.migrate(c, path)
    assert c.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 2
    assert c.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 0
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0


def test_one_ledger_and_every_step_of_a_turn_is_linked(client):
    from app import db

    names = {r["name"] for r in db.all("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not names & set(LEGACY)
    SCRIPT[:] = [tool_call("create_task", {"title": "Ledger check"}), {"role": "assistant", "content": "Task created successfully!"}]
    ev = events(client.post("/api/assistant/chat", json={"message": "add ledger check"}))
    turn = db.one("SELECT * FROM actions WHERE kind = 'turn' AND conversation_id = ? ORDER BY id DESC", ev[0]["conversation_id"])
    assert turn["outcome"] == "backed" and turn["model"].endswith("/fake-model") and turn["latency_ms"] >= 0
    children = db.all("SELECT * FROM actions WHERE parent_action_id = ? ORDER BY id", turn["id"])
    assert [c["kind"] for c in children] == ["model_call", "tool_call", "model_call"]
    call = children[0]
    assert call["tokens_in"] == 1000 and call["tokens_out"] == 500 and call["provider"] == "Fake"
    assert call["auth_kind"] == "openai_compatible" and call["protocol"] == "chat_completions" and call["channel"] == "web"
    assert children[1]["tool"] == "create_task" and children[1]["outcome"] == "verified" and "Ledger check" in children[1]["args"]


def test_model_calls_outside_chat_are_recorded_too(client, monkeypatch):
    """Before R2, research, writing assist, compare and task breakdown spent tokens without a ledger row."""
    from app import db

    before = db.one("SELECT COUNT(*) AS n FROM actions WHERE kind = 'model_call'")["n"]
    client.post("/api/ai/assist", json={"action": "fix", "text": "teh cat"})
    SCRIPT[:] = [{"role": "assistant", "content": '["one", "two"]'}]
    client.post("/api/tasks/breakdown", json={"goal": "plan a trip"})
    rows = db.all("SELECT channel, outcome FROM actions WHERE kind = 'model_call' ORDER BY id DESC LIMIT 2")
    assert db.one("SELECT COUNT(*) AS n FROM actions WHERE kind = 'model_call'")["n"] == before + 2
    assert {r["channel"] for r in rows} == {"assist", "tasks"}


def test_a_failed_model_call_is_a_row_with_its_reason(client):
    from app import db

    pid = client.post("/api/providers", json={"name": "Down", "kind": "openai", "base_url": "http://127.0.0.1:9/v1", "models": "m"}).json()["id"]
    events(client.post("/api/assistant/chat", json={"message": "hi", "model": f"{pid}/m"}))
    row = db.one("SELECT * FROM actions WHERE kind = 'model_call' AND model = ? ORDER BY id DESC", f"{pid}/m")
    assert row["outcome"] == "error" and "unreachable" in row["failure_reason"] and row["usd_cost"] is None
    client.delete(f"/api/providers/{pid}")


def test_trace_columns_are_redacted_but_held_actions_keep_runnable_arguments(client):
    from app import actions, db

    secret = 'note https://api.telegram.org/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe'
    raw = '{"content": "' + secret + '"}'
    aid = actions.add("tool_call", "held", trace={"conversation_id": 1}, tool="remember", effect="persistent", args=raw, payload=raw)
    row = db.one("SELECT args, payload_enc FROM actions WHERE id = ?", aid)
    assert "AAHdq" not in row["args"] and "/bot<redacted>/" in row["args"]
    assert "AAHdq" not in row["payload_enc"]  # encrypted, not plain text
    assert actions.raw_args(aid) == raw
