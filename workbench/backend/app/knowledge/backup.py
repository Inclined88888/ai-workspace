"""Automatic local backups, restore, and a full export with your files (feat/memory-core). Free on every plan.

Backups are consistent SQLite snapshots (online backup API, safe while the app runs) in DATA_DIR/backups. The database
is already encrypted where it matters (memories, attrs, mail), so a backup is no more readable than the live file.
One a day is kept for KEEP_DAILY days. Restore first snapshots the current state, so a restore can itself be undone.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException

from .. import config, db

log = logging.getLogger("knowledge.backup")
KEEP_DAILY = 14
NAME = re.compile(r"^workbench-\d{8}-\d{6}(-\d+)?(-[a-z]+)?\.db$")


def folder() -> Path:
    p = config.DATA_DIR / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def create(tag: str = "") -> dict:
    stamp, n = datetime.now().strftime('%Y%m%d-%H%M%S'), 0
    name = f"workbench-{stamp}{'-' + tag if tag else ''}.db"
    while (folder() / name).exists():  # never overwrite a snapshot (two in one second)
        n += 1
        name = f"workbench-{stamp}-{n}{'-' + tag if tag else ''}.db"
    dst = sqlite3.connect(folder() / name)
    try:
        db.conn().backup(dst)
    finally:
        dst.close()
    prune()
    return info(name)


def info(name: str) -> dict:
    p = folder() / name
    return {"name": name, "size": p.stat().st_size, "at": datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "kind": "before restore" if name.endswith("-prerestore.db") else ("manual" if name.endswith("-manual.db") else "automatic")}


def list_() -> list[dict]:
    return [info(p.name) for p in sorted(folder().glob("workbench-*.db"), reverse=True) if NAME.match(p.name)]


def prune() -> None:
    auto = [p for p in sorted(folder().glob("workbench-*.db"), reverse=True) if NAME.match(p.name) and not p.name.endswith(("-manual.db", "-prerestore.db"))]
    days: set[str] = set()
    for p in auto:
        d = p.name[10:18]
        if d in days or len(days) >= KEEP_DAILY:
            p.unlink(missing_ok=True)
        else:
            days.add(d)


def restore(name: str) -> dict:
    if not NAME.match(name) or not (folder() / name).exists():
        raise HTTPException(404, "No such backup")
    src = sqlite3.connect(folder() / name)
    try:
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise HTTPException(400, "That backup is damaged, so I left everything as it is.")
        safety = create("prerestore")
        src.backup(db.conn())
    finally:
        src.close()
    from . import crypto, schema

    schema.reset_ready()
    crypto.reset_cache()
    return {"restored": name, "undo_with": safety["name"]}


def due() -> bool:
    today = datetime.now().strftime("%Y%m%d")
    return db.get_setting("kn_backup_auto", "1") == "1" and not any(b["name"][10:18] == today for b in list_() if b["kind"] == "automatic")


async def loop() -> None:
    while True:
        await asyncio.sleep(900)
        try:
            if due():
                await asyncio.to_thread(_thread)
        except Exception as e:  # noqa: BLE001
            log.warning("automatic backup failed: %s", e)


def _thread() -> None:
    try:
        create()
    finally:
        c = getattr(db._local, "conn", None)
        if c is not None:
            c.close()
            db._local.conn = None


def _file_path(p: str) -> Path:
    q = Path(p)
    return q if q.is_absolute() else config.DATA_DIR / q


def export_zip() -> bytes:
    """Everything you own, in open formats: knowledge.json, chats.json, notes as Markdown, tasks.json, and your files."""
    from . import export_all

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", "Your AI Workspace export. JSON files open in any text editor; notes are Markdown; files are as you added them.\n")
        z.writestr("knowledge.json", json.dumps(export_all(), indent=1, default=str))
        if _has("conversations"):
            chats = []
            for c in db.all("SELECT id, title, created_at FROM conversations ORDER BY id"):
                msgs = db.all("SELECT role, content, created_at FROM messages WHERE conversation_id = ? AND role IN ('user','assistant') ORDER BY id", c["id"])
                chats.append({**c, "messages": msgs})
            z.writestr("chats.json", json.dumps(chats, indent=1))
        if _has("docs"):
            for d in db.all("SELECT id, title, body FROM docs"):
                z.writestr(f"notes/{d['id']}-{_safe(d['title'] or 'note')}.md", f"# {d['title']}\n\n{d['body']}")
        if _has("tasks"):
            z.writestr("tasks.json", json.dumps(db.all("SELECT * FROM tasks"), indent=1, default=str))
        if _has("work_files"):
            for f in db.all("SELECT id, name, path FROM work_files"):
                p = _file_path(f["path"] or "")
                if p.is_file():
                    z.write(p, f"files/{f['id']}-{_safe(f['name'])}")
    return buf.getvalue()


def _safe(s: str) -> str:
    return re.sub(r"[^\w.\- ]+", "_", s)[:80].strip() or "item"


def _has(t: str) -> bool:
    return bool(db.one("SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = ?", t))
