"""Regression coverage for the shipped pre-receipts ROOKS database."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest

SCHEMA = (Path(__file__).parent / 'fixtures' / 'schema_rooks.sql').read_text()


def legacy(path, populated=True):
    c = sqlite3.connect(path, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    c.execute("INSERT INTO settings VALUES ('workspace_name', 'Preserved workspace')")
    c.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES (1,'Preserved chat','before','before')")
    c.execute("INSERT INTO docs(title,body,created_at,updated_at) VALUES ('Preserved note','Keep me','before','before')")
    if populated:
        for ok in [1, 0]:
            c.execute("INSERT INTO tool_runs(at,tool,actor,conversation_id,mission_id,ok,ms,args,error) "
                      "VALUES ('2026-10-28T00:00:00+00:00','create_task','assistant',1,42,?,123,?,?)",
                      (ok, '{"password":"planted-private-value"}', '' if ok else 'password=planted-private-value'))
        c.execute("INSERT INTO usage(ts,model,conversation_id,prompt_tokens,completion_tokens) VALUES ('before','local',1,5,7)")
    return c


@pytest.fixture
def migration(monkeypatch):
    monkeypatch.setenv('APP_SECRET', 'a'*40)
    monkeypatch.setenv('TOOLS_API_KEY', 'k'*30)
    from app import db
    return db


@pytest.mark.parametrize('populated', [False, True])
def test_rooks_upgrade_preserves_history_and_backup(migration, tmp_path, populated):
    db = migration
    path = tmp_path / 'workbench.db'
    c = legacy(path, populated)
    db.migrate(c, path)
    assert c.execute('PRAGMA user_version').fetchone()[0] == db.SCHEMA_VERSION
    assert c.execute('SELECT title FROM conversations WHERE id=1').fetchone()[0] == 'Preserved chat'
    assert c.execute('SELECT body FROM docs').fetchone()[0] == 'Keep me'
    rows = [dict(r) for r in c.execute("SELECT * FROM actions WHERE kind='tool_call' ORDER BY id")]
    assert len(rows) == (2 if populated else 0)
    if populated:
        assert [r['outcome'] for r in rows] == ['ok', 'tool_error']
        assert all(r['verified'] is None and r['ts'] == '2026-10-28T00:00:00+00:00' and r['latency_ms'] == 123 for r in rows)
        assert all(json.loads(r['detail'])['mission_id'] == 42 for r in rows)
        assert all('planted-private-value' not in str(r) for r in rows)
        assert c.execute("SELECT tokens_in,tokens_out FROM actions WHERE kind='model_call'").fetchone()[:] == (5,7)
    backups = list(tmp_path.glob('*.bak'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as original:
        assert original.execute('SELECT COUNT(*) FROM tool_runs').fetchone()[0] == (2 if populated else 0)
    count = c.execute('SELECT COUNT(*) FROM actions').fetchone()[0]
    db.migrate(c, path)
    assert c.execute('SELECT COUNT(*) FROM actions').fetchone()[0] == count
    c.close()
    code = "from app.main import app\nfrom fastapi.testclient import TestClient\nwith TestClient(app) as c:\n assert c.get('/healthz').status_code == 200\n assert c.get('/api/status').json()['workspace_name'] == 'Preserved workspace'\n"
    result = subprocess.run([sys.executable, '-c', code], env={
        **os.environ,
        'PATH': os.environ['PATH'], 'APP_SECRET': 'a'*40, 'TOOLS_API_KEY': 'k'*30,
        'OPENAI_API_KEY': '', 'ANTHROPIC_API_KEY': '', 'OPENROUTER_API_KEY': '', 'GEMINI_API_KEY': '',
        'OLLAMA_URL': '', 'BROWSER_URL': '', 'SEARXNG_URL': '',
        'DATA_DIR': str(tmp_path), 'PYTHONPATH': str(Path(__file__).resolve().parents[1]),
    }, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_unknown_layout_keeps_original_rows(migration, tmp_path):
    path = tmp_path / 'workbench.db'
    c = legacy(path)
    c.execute('ALTER TABLE tool_runs RENAME COLUMN at TO unknown_timestamp')
    with pytest.raises(RuntimeError, match='Unsupported legacy tool_runs schema'):
        migration.migrate(c, path)
    assert c.execute('PRAGMA user_version').fetchone()[0] == 0
    assert c.execute('SELECT COUNT(*) FROM tool_runs').fetchone()[0] == 2
    assert c.execute('SELECT COUNT(*) FROM usage').fetchone()[0] == 1
    assert c.execute('SELECT COUNT(*) FROM actions').fetchone()[0] == 0
    c.close()
