"""Append-only record of consequential actions. Details pass through the secret filter and are kept short."""

import logging

from . import db, sensitive

log = logging.getLogger("rooks.audit")
KEEP = 20_000  # newest rows kept; older ones are trimmed so the log cannot grow without bound


def record(actor: str, action: str, target: str = "", detail: str = "", ok: bool = True) -> None:
    try:
        db.run("INSERT INTO audit_log(at, actor, action, target, detail, ok) VALUES (?, ?, ?, ?, ?, ?)", db.now(),
               actor[:60], action[:60], sensitive.redact(str(target))[:200], sensitive.redact(str(detail))[:600], int(ok))
        if db.one("SELECT id FROM audit_log ORDER BY id DESC LIMIT 1 OFFSET ?", KEEP):
            db.run("DELETE FROM audit_log WHERE id <= (SELECT id FROM audit_log ORDER BY id DESC LIMIT 1 OFFSET ?)", KEEP)
    except Exception:  # noqa: BLE001 - an audit write must never break the action it records
        log.exception("audit write failed")


def recent(limit: int = 200, actor: str = "", action: str = "") -> list[dict]:
    where, args = [], []
    if actor:
        where.append("actor = ?")
        args.append(actor)
    if action:
        where.append("action = ?")
        args.append(action)
    sql = "SELECT * FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return db.all(sql, *args, max(1, min(limit, 1000)))
