"""Chat search (messages_fts): a streamed answer's final text is searchable, deletes keep the index healthy, and the
database passes PRAGMA integrity_check with tool messages in it. Older databases are rebuilt by migrate_v4."""

import sqlite3

OLD_FTS = """
CREATE VIRTUAL TABLE messages_fts USING fts5(content, content='messages', content_rowid='id', tokenize='porter unicode61');
CREATE TRIGGER messages_ai AFTER INSERT ON messages WHEN new.role IN ('user', 'assistant') BEGIN
  INSERT INTO messages_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER messages_ad AFTER DELETE ON messages WHEN old.role IN ('user', 'assistant') BEGIN
  INSERT INTO messages_fts(messages_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
"""


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_SECRET", "a" * 40)
    monkeypatch.setenv("TOOLS_API_KEY", "k" * 30)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))


def _search(c, q):
    return [r[0] for r in c.execute("SELECT rowid FROM messages_fts WHERE messages_fts MATCH ? ORDER BY rowid", (q,))]


def _stream_an_answer(c, final="The invoice total is 4200 dollars"):
    c.execute("INSERT INTO conversations(id, title, created_at, updated_at) VALUES (1, 'x', 't', 't')")
    c.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (1, 'user', 'what is the invoice total', 't')")
    c.execute("INSERT INTO messages(conversation_id, role, content, tool_call_id, created_at) VALUES (1, 'tool', 'raw tool output', 'c1', 't')")
    mid = c.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (1, 'assistant', '', 't')").lastrowid
    c.execute("UPDATE messages SET content = ? WHERE id = ?", (final, mid))  # what assistant.py does after streaming
    return mid


def test_new_database_indexes_the_final_answer_and_stays_healthy(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    from app import db

    c = sqlite3.connect(tmp_path / "new.db", isolation_level=None)
    db.migrate(c, tmp_path / "new.db")
    c.execute("PRAGMA foreign_keys=ON")
    mid = _stream_an_answer(c)
    assert _search(c, "dollars") == [mid]
    assert _search(c, "raw") == []  # tool output is not chat text
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.execute("DELETE FROM conversations WHERE id = 1")  # cascades to messages
    assert _search(c, "invoice") == []
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_v3_database_is_rebuilt_with_every_chat_message_searchable(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    from app import db

    path = tmp_path / "v3.db"
    c = sqlite3.connect(path, isolation_level=None)
    c.executescript(db.SCHEMA.replace("CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts", "CREATE VIRTUAL TABLE IF NOT EXISTS ignored_fts")
                    .split("CREATE TRIGGER IF NOT EXISTS messages_ai")[0])
    c.executescript(OLD_FTS)
    c.execute("PRAGMA user_version = 3")
    mid = _stream_an_answer(c)
    assert _search(c, "dollars") == []  # the bug: the final text was never indexed

    db.migrate(c, path)

    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 4
    assert _search(c, "dollars") == [mid] and len(_search(c, "invoice")) == 2
    assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    c.execute("UPDATE messages SET content = 'edited reply about rent' WHERE id = ?", (mid,))
    assert _search(c, "rent") == [mid] and _search(c, "dollars") == []
