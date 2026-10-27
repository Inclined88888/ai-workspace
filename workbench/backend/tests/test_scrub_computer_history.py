"""Legacy privacy cleanup remains opt-in and preserves a backup."""
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_cleanup_previews_then_sanitizes_a_copy(tmp_path):
    dbfile = tmp_path / "legacy.sqlite3"
    c = sqlite3.connect(dbfile)
    c.executescript("""
        CREATE TABLE computer_actions (id INTEGER PRIMARY KEY, args TEXT NOT NULL, result TEXT NOT NULL, idem_key TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE tool_runs (id INTEGER PRIMARY KEY, tool TEXT NOT NULL, args TEXT NOT NULL, error TEXT NOT NULL);
        CREATE TABLE approvals (id INTEGER PRIMARY KEY, tool TEXT NOT NULL, args TEXT NOT NULL, result TEXT NOT NULL, status TEXT NOT NULL);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, tool_calls TEXT NOT NULL);
    """)
    private = "PRIVATE-DESKTOP-CONTENT-TEST-ONLY"
    c.execute("INSERT INTO computer_actions VALUES (1, ?, ?, ?, 'done')", ('{"text":"'+private+'"}', private, 'type_text{"text":"'+private+'"}'))
    c.execute("INSERT INTO tool_runs VALUES (1, 'computer_type', ?, '')", (private,))
    c.execute("INSERT INTO approvals VALUES (1, 'computer_type', ?, ?, 'approved')", (private, private))
    c.execute("INSERT INTO messages VALUES (1, ?)", ('[{"function":{"name":"computer_type","arguments":"{\\"text\\":\\"'+private+'\\"}"}}]',))
    c.commit()
    c.close()
    script = Path(__file__).parents[1] / "scripts" / "scrub_computer_history.py"
    def call(*args):
        return subprocess.run([sys.executable, str(script), "--db", str(dbfile), *args],
                              text=True, capture_output=True, check=True)
    assert "Dry run only" in call().stdout
    with sqlite3.connect(dbfile) as db:
        assert private in db.execute("SELECT result FROM computer_actions").fetchone()[0]
    backup = tmp_path / "private-backup.sqlite3"
    assert '"modified": true' in call("--apply", "--confirm", "PURGE-COMPUTER-HISTORY", "--backup", str(backup)).stdout
    with sqlite3.connect(dbfile) as db:
        for table, col in [("computer_actions", "args"), ("computer_actions", "result"), ("computer_actions", "idem_key"),
                           ("tool_runs", "args"), ("approvals", "args"), ("messages", "tool_calls")]:
            assert private not in str(db.execute(f"SELECT {col} FROM {table}").fetchone()[0])
    with sqlite3.connect(backup) as db:
        assert db.execute("SELECT result FROM computer_actions").fetchone()[0] == private
