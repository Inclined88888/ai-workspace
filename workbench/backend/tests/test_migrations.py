import importlib.util
from pathlib import Path
import shutil
import sqlite3

import pytest


@pytest.fixture
def migration_db(client, tmp_path):
    # An independent module keeps the app's live test database and background loops untouched.
    from app import db
    spec = importlib.util.spec_from_file_location('app._migration_test_db', db.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    c = sqlite3.connect(tmp_path / 'copy.db', isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    module.conn = lambda: c
    yield module, c
    c.close()


def test_upgrades_a_copy_of_old_database(client, tmp_path):
    from app.security import encrypt, decrypt
    from app import db
    original, copy = tmp_path / 'old.db', tmp_path / 'upgrade.db'
    secret = encrypt('old-provider-key')
    with sqlite3.connect(original) as old:
        old.executescript((Path(__file__).parent / 'fixtures/schema_v0.sql').read_text())
        old.execute("INSERT INTO settings VALUES ('timezone', 'Australia/Brisbane')")
        old.execute("INSERT INTO providers(name, base_url, api_key_enc, created_at) VALUES ('Old', 'http://localhost:11434/v1', ?, 'old')", (secret,))
        old.execute("INSERT INTO memories(content, created_at, updated_at) VALUES ('Legacy basil memory', 'old', 'old')")
        old.execute("INSERT INTO docs(title, body, created_at, updated_at) VALUES ('Legacy note', 'Keep this document', 'old', 'old')")
        old.execute("INSERT INTO conversations(title, channel, created_at, updated_at) VALUES ('Old conversation', 'cli', 'old', 'old')")
        old.execute("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (1, 'user', 'Legacy conversation', 'old')")
    shutil.copy2(original, copy)
    spec = importlib.util.spec_from_file_location('app._migration_test_db', db.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with sqlite3.connect(copy, isolation_level=None) as c:
        c.row_factory = sqlite3.Row
        module.conn = lambda: c
        module.init()
        module.init()  # restart is idempotent
        assert module.get_setting('schema_version') == '1'
        assert module.get_setting('timezone') == 'Australia/Brisbane'
        stored = module.one('SELECT * FROM providers')['api_key_enc']
        assert stored == secret and decrypt(stored) == 'old-provider-key'
        assert module.one('SELECT content FROM memories')['content'] == 'Legacy basil memory'
        assert module.one("SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'basil'")
        assert module.one("SELECT rowid FROM docs_fts WHERE docs_fts MATCH 'document'")
        assert module.one("SELECT rowid FROM messages_fts WHERE messages_fts MATCH 'conversation'")
        assert c.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
    with sqlite3.connect(original) as old:
        assert old.execute("SELECT value FROM settings WHERE key='schema_version'").fetchone() is None


def test_migrations_are_ordered_and_run_once(migration_db):
    db, c = migration_db
    seen = []
    def second(c):
        seen.append(2)
        c.execute('CREATE TABLE migration_test (value TEXT)')
    def third(c):
        seen.append(3)
        c.execute("INSERT INTO migration_test VALUES ('kept')")
    db.MIGRATIONS = [*db.MIGRATIONS, second, third]
    db.init()
    db.init()
    assert seen == [2, 3] and db.get_setting('schema_version') == '3'
    assert db.one('SELECT * FROM migration_test')['value'] == 'kept'


def test_failed_migration_rolls_back_schema_data_and_version(migration_db):
    db, c = migration_db
    db.init()
    def broken(c):
        c.execute('CREATE TABLE failed_migration (id INTEGER)')
        c.execute("INSERT INTO settings VALUES ('should_rollback', 'yes')")
        raise RuntimeError('broken migration')
    db.MIGRATIONS.append(broken)
    with pytest.raises(RuntimeError, match='broken migration'):
        db.init()
    assert db.get_setting('schema_version') == '1'
    assert db.get_setting('should_rollback') is None
    assert not db.one("SELECT name FROM sqlite_master WHERE name = 'failed_migration'")
    assert not c.in_transaction


def test_newer_database_version_is_refused(migration_db):
    db, c = migration_db
    db.init()
    db.set_setting('schema_version', '999')
    with pytest.raises(RuntimeError, match='Unsupported database schema version 999'):
        db.init()
    assert db.get_setting('schema_version') == '999' and not c.in_transaction
