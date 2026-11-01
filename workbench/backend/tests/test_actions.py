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
    c.execute("INSERT INTO providers(id, name, kind, base_url, api_key_enc, models, created_at) VALUES "
              "(1, 'Ollama (local)', 'ollama', 'http://host.docker.internal:11434/v1', '', '', 't'), "
              "(3, 'Anthropic', 'anthropic', 'https://api.anthropic.com/v1', 'enc-key', '', 't'), "
              "(4, 'LM Studio', 'openai', 'http://192.168.1.5:1234/v1', '', 'qwen', 't'), "
              "(7, 'OpenRouter', 'openai', 'https://openrouter.ai/api/v1', 'enc-or', '', 't')")
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
    assert not names & {*LEGACY, "providers"} and {"actions", "connections"} <= names
    conns = [dict(r) for r in c.execute("SELECT id, auth_kind, protocol, secret_enc, residency FROM connections ORDER BY id")]
    # ids survive (model refs "<id>/<model>" keep working); behaviour is unchanged: Anthropic stays on the compat protocol
    assert conns == [{"id": 1, "auth_kind": "local_ollama", "protocol": "ollama_native", "secret_enc": "", "residency": "local"},
                     {"id": 3, "auth_kind": "api_key", "protocol": "chat_completions", "secret_enc": "enc-key", "residency": "cloud"},
                     {"id": 4, "auth_kind": "openai_compatible", "protocol": "chat_completions", "secret_enc": "", "residency": "cloud"},
                     {"id": 7, "auth_kind": "api_key", "protocol": "chat_completions", "secret_enc": "enc-or", "residency": "cloud"}]
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    backups = list(tmp_path.glob(f"workbench.db.pre-v{db.SCHEMA_VERSION}-*.bak"))
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


def test_conversation_id_autoincrement_prevents_reuse(client):
    """Conversation IDs must not be reused after deletion (AUTOINCREMENT)."""
    from app import db

    # Create conversation A with an action
    cid_a = db.run("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES (?, ?, ?, ?)",
                   "Conv A", "web", db.now(), db.now())
    db.run("INSERT INTO actions(ts, kind, conversation_id, outcome, request_id) VALUES (?, ?, ?, ?, ?)",
           db.now(), "turn", cid_a, "backed", "req-a")

    # Delete conversation A
    r = client.delete(f"/api/conversations/{cid_a}")
    assert r.status_code == 200

    # Verify action was detached (conversation_id nulled)
    action = db.one("SELECT conversation_id FROM actions WHERE request_id = 'req-a'")
    assert action["conversation_id"] is None

    # Create conversation B
    cid_b = db.run("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES (?, ?, ?, ?)",
                   "Conv B", "web", db.now(), db.now())

    # B's ID must be different from A's (AUTOINCREMENT prevents reuse)
    assert cid_b != cid_a

    # B's trace must not contain A's action
    b_actions = db.all("SELECT * FROM actions WHERE conversation_id = ?", cid_b)
    assert len(b_actions) == 0

    # A's action should still exist but with NULL conversation_id
    a_action = db.one("SELECT * FROM actions WHERE request_id = 'req-a'")
    assert a_action is not None
    assert a_action["conversation_id"] is None
    assert a_action["outcome"] == "backed"


def test_migration_v3_adds_autoincrement_to_old_schema(tmp_path, monkeypatch):
    """Migration v3 rebuilds conversations with AUTOINCREMENT on an old schema DB."""
    import sqlite3
    import os

    # Set up required env vars for config module
    monkeypatch.setenv("APP_SECRET", "a" * 40)
    monkeypatch.setenv("TOOLS_API_KEY", "k" * 30)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from app import db

    # Create old schema DB without AUTOINCREMENT
    path = tmp_path / "old.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA user_version = 2")  # simulate pre-v3
    c.executescript("""
        CREATE TABLE conversations (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL DEFAULT 'New chat',
            channel TEXT NOT NULL DEFAULT 'web',
            external_id TEXT NOT NULL DEFAULT '',
            agent_id INTEGER,
            model TEXT NOT NULL DEFAULT '',
            memory_cursor INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX conversations_channel ON conversations(channel, external_id);
        INSERT INTO conversations(id, title, channel, created_at, updated_at) VALUES (1, 'Old chat', 'web', 't', 't');
        INSERT INTO conversations(id, title, channel, created_at, updated_at) VALUES (5, 'Another', 'web', 't', 't');
    """)

    # Run migration
    db.migrate(c, path)

    # Check AUTOINCREMENT is present
    table_sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone()[0]
    assert "AUTOINCREMENT" in table_sql.upper()

    # Data preserved
    rows = c.execute("SELECT id, title FROM conversations ORDER BY id").fetchall()
    assert [(r["id"], r["title"]) for r in rows] == [(1, "Old chat"), (5, "Another")]

    # sqlite_sequence seeded with max id (5)
    seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name = 'conversations'").fetchone()
    assert seq is not None and seq["seq"] == 5

    # Next insert gets id 6 (not reusing 1 or 5)
    cur = c.cursor()
    cur.execute("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES (?, ?, ?, ?)",
              ("New", "web", "t", "t"))
    next_id = cur.lastrowid
    assert next_id == 6

    # Migration is idempotent: running again doesn't change anything
    db.migrate(c, path)
    table_sql2 = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='conversations'").fetchone()[0]
    assert "AUTOINCREMENT" in table_sql2.upper()
    rows2 = c.execute("SELECT id, title FROM conversations ORDER BY id").fetchall()
    assert [(r["id"], r["title"]) for r in rows2] == [(1, "Old chat"), (5, "Another"), (6, "New")]


def test_trace_columns_are_redacted_but_held_actions_keep_runnable_arguments(client):
    from app import actions, db

    secret = 'note https://api.telegram.org/bot123456:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw/getMe'
    raw = '{"content": "' + secret + '"}'
    aid = actions.add("tool_call", "held", trace={"conversation_id": 1}, tool="remember", effect="persistent", args=raw, payload=raw)
    row = db.one("SELECT args, payload_enc FROM actions WHERE id = ?", aid)
    assert "AAHdq" not in row["args"] and "/bot<redacted>/" in row["args"]
    assert "AAHdq" not in row["payload_enc"]  # encrypted, not plain text
    assert actions.raw_args(aid) == raw
