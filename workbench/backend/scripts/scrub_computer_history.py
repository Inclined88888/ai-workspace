#!/usr/bin/env python3
"""Opt-in migration for historical ROOKS Computer Use data.

Dry-run by default. Run only while ROOKS and its companion are stopped.
The required backup contains historical private data; protect and eventually
destroy it separately. Existing exports and remote backups are unaffected.
"""
import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

KINDS = ("computer_", "browser_", "files_", "sheet_", "doc_")
HASH = re.compile(r"^[0-9a-f]{64}$")


def _is_computer_tool(name):
    return bool(name and name.startswith(KINDS))


def _hide_calls(raw):
    if not raw:
        return raw
    try:
        calls = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(calls, list):
        return raw
    changed = False
    for c in calls:
        fn = c.get("function", {}) if isinstance(c, dict) else {}
        if _is_computer_tool(fn.get("name", "")):
            fn["arguments"] = "{}"
            changed = True
    return json.dumps(calls) if changed else raw


def stats(c):
    active = c.execute("SELECT COUNT(*) FROM computer_actions WHERE status IN ('queued','preparing','dispatched')").fetchone()[0]
    action = c.execute("SELECT COUNT(*) FROM computer_actions WHERE (args NOT IN ('','{}') OR result NOT IN ('', '{}') OR length(idem_key) != 64)").fetchone()[0]
    tool = c.execute("SELECT COUNT(*) FROM tool_runs WHERE (tool LIKE 'computer_%' OR tool LIKE 'browser_%' OR tool LIKE 'files_%' OR tool LIKE 'sheet_%' OR tool LIKE 'doc_%') AND (args != '' OR error != '')").fetchone()[0]
    approval = c.execute("SELECT COUNT(*) FROM approvals WHERE (tool LIKE 'computer_%' OR tool LIKE 'browser_%' OR tool LIKE 'files_%' OR tool LIKE 'sheet_%' OR tool LIKE 'doc_%')").fetchone()[0]
    msgs = c.execute("SELECT COUNT(*) FROM messages WHERE tool_calls != ''").fetchone()[0]
    return dict(active_actions=active, action_rows=action, tool_log_rows=tool, approval_rows=approval, messages_to_inspect=msgs)


def scrub(c):
    for aid, key in c.execute("SELECT id, idem_key FROM computer_actions").fetchall():
        fixed = key if HASH.fullmatch(key or "") else hashlib.sha256((key or "").encode()).hexdigest()
        c.execute("UPDATE computer_actions SET args = '{}', result = '', idem_key = ? WHERE id = ?", (fixed, aid))
    c.execute("""UPDATE tool_runs SET args = '', error = ''
                 WHERE tool LIKE 'computer_%' OR tool LIKE 'browser_%'
                    OR tool LIKE 'files_%' OR tool LIKE 'sheet_%' OR tool LIKE 'doc_%'""")
    for mid, raw in c.execute("SELECT id, tool_calls FROM messages WHERE tool_calls != ''").fetchall():
        clean = _hide_calls(raw)
        if clean != raw:
            c.execute("UPDATE messages SET tool_calls = ? WHERE id = ?", (clean, mid))
    c.execute("""UPDATE approvals SET args = '{}', result = ''
                 WHERE (tool LIKE 'computer_%' OR tool LIKE 'browser_%'
                    OR tool LIKE 'files_%' OR tool LIKE 'sheet_%' OR tool LIKE 'doc_%')
                    AND status NOT IN ('pending','running')""")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", required=True, type=Path, help="Existing ROOKS SQLite database file")
    p.add_argument("--apply", action="store_true", help="Modify the database; never implied by a preview")
    p.add_argument("--confirm", default="", help="Required: PURGE-COMPUTER-HISTORY")
    p.add_argument("--backup", type=Path, help="Required new path for a private SQLite backup")
    a = p.parse_args(argv)
    dbpath = a.db.expanduser().resolve()
    if not dbpath.is_file():
        p.error("Database does not exist; no file was created")
    if a.apply and (a.confirm != "PURGE-COMPUTER-HISTORY" or a.backup is None):
        p.error("For --apply, provide --confirm PURGE-COMPUTER-HISTORY and --backup NEW_PATH")
    c = sqlite3.connect(f"file:{dbpath}?mode=rw", uri=True, timeout=5)
    try:
        c.execute("PRAGMA busy_timeout=5000")
        before = stats(c)
        print(json.dumps({"preview": before, "modified": False}))
        if not a.apply:
            print("Dry run only. Stop ROOKS before applying; the backup will contain private information.")
            return 0
        if before["active_actions"]:
            p.error("Active or pending Computer Use actions exist; stop ROOKS and resolve these first")
        pending = c.execute("""SELECT COUNT(*) FROM approvals WHERE status IN ('pending','running')
            AND (tool LIKE 'computer_%' OR tool LIKE 'browser_%' OR tool LIKE 'files_%'
            OR tool LIKE 'sheet_%' OR tool LIKE 'doc_%')""").fetchone()[0]
        if pending:
            p.error("Resolve pending Computer Use approvals before cleanup")
        backup = a.backup.expanduser().resolve()
        if backup == dbpath or backup.exists():
            p.error("Backup path must be new and different from the database")
        # Backup first. The backup intentionally preserves all original data,
        # including anything confidential; never put it in public storage.
        fd = os.open(str(backup), os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(fd)
        try:
            bc = sqlite3.connect(str(backup))
            try:
                c.backup(bc)
            finally:
                bc.close()
        except BaseException:
            backup.unlink(missing_ok=True)
            raise
        with c:
            c.execute("BEGIN IMMEDIATE")
            scrub(c)
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("VACUUM")
        print(json.dumps({"modified": True, "backup": str(backup), "after": stats(c)}))
        print("Old backups/exports and the new backup still contain historical private data; dispose of them securely.")
        return 0
    finally:
        c.close()


if __name__ == "__main__":
    sys.exit(main())
