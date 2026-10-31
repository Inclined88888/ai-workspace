"""Nightly self-improvement pass (feat/memory-core), alongside consolidation.

Runs once a day, only while the app is running and you're idle (no chats or actions for IDLE_MINUTES), and only if
"Get better overnight" is on. It reviews the last day: failed runs, things you undid, approvals you turned down and
feedback you gave. Then it:
  * applies safe, inward changes straight away: lessons become "how you like things done" memories (prompt hints),
    memories that proved useful rank higher, stale ones fade, old happenings are summarised (consolidation);
  * proposes anything that changes how it acts for you (e.g. "Ask before sending invoices") - nothing changes until
    you approve it on Home;
  * writes a plain-English "What I improved" note for Home.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from .. import db
from . import crypto, memory, schema

log = logging.getLogger("knowledge.improve")
IDLE_MINUTES = 20
CHECK_SECONDS = 600
FAILED = ("failed", "error", "check_failed", "model_error", "unbacked")


def enabled() -> bool:
    return db.get_setting("kn_improve", "1") == "1"


def set_enabled(on: bool) -> None:
    db.set_setting("kn_improve", "1" if on else "0")


def _ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


def _has(t: str) -> bool:
    return bool(db.one("SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = ?", t))


def idle(minutes: int = IDLE_MINUTES) -> bool:
    since = _ago(minutes=minutes)
    if _has("messages") and db.one("SELECT 1 AS x FROM messages WHERE created_at > ? AND role = 'user' LIMIT 1", since):
        return False
    if _has("actions") and db.one("SELECT 1 AS x FROM actions WHERE ts > ? AND channel NOT IN ('automation','system') LIMIT 1", since):
        return False
    return True


def today() -> str:
    return datetime.now().astimezone().date().isoformat()


def due() -> bool:
    return enabled() and db.get_setting("kn_improve_last", "") != today() and datetime.now().astimezone().hour >= 1 and idle()


def _signals(since: str) -> dict:
    s = {"failed": [], "denied": [], "undone": [], "lessons": [], "runs_failed": []}
    if _has("actions"):
        s["failed"] = db.all(f"SELECT tool, COUNT(*) AS n, MAX(failure_reason) AS why FROM actions WHERE ts > ? AND kind = 'tool_call' AND tool != '' "
                             f"AND outcome IN ({','.join('?' * len(FAILED))}) GROUP BY tool ORDER BY n DESC", since, *FAILED)
        s["denied"] = db.all("SELECT tool, COUNT(*) AS n FROM actions WHERE ts > ? AND kind = 'tool_call' AND outcome = 'denied' "
                             "AND channel NOT IN ('automation') GROUP BY tool ORDER BY n DESC", since)
    if _has("history_entries"):
        s["undone"] = db.all("SELECT tool, COUNT(*) AS n, MAX(title) AS title FROM history_entries WHERE status = 'undone' AND undone_at > ? "
                             "GROUP BY tool ORDER BY n DESC", since)
    if _has("adapt_lessons"):
        from ..security import decrypt
        for r in db.all("SELECT id, lesson_enc, rating FROM adapt_lessons WHERE created_at > ?", since):
            try:
                s["lessons"].append({"id": r["id"], "text": decrypt(r["lesson_enc"]), "rating": r["rating"]})
            except Exception:  # noqa: BLE001
                pass
    if _has("adapt_runs"):
        s["runs_failed"] = db.all("SELECT playbook_id, COUNT(*) AS n FROM adapt_runs WHERE updated_at > ? AND (status = 'failed' OR rating < 0) "
                                  "GROUP BY playbook_id", since)
    return s


def _rerank(since: str) -> dict:
    up = db.conn().execute("UPDATE kn_memories SET weight = MIN(2.0, weight + 0.1) WHERE status = 'active' AND last_used_at > ?", (since,)).rowcount
    faded = db.conn().execute("UPDATE kn_memories SET weight = MAX(0.3, weight * 0.9) WHERE status = 'active' AND pinned = 0 AND tier = 'episodic' "
           "AND COALESCE(last_used_at, created_at) < ?", (_ago(days=30),)).rowcount
    return {"boosted": up, "faded": faded}


def _propose(day: str, title: str, change: dict, why: str) -> bool:
    key = json.dumps(change, sort_keys=True)
    if db.one("SELECT 1 AS x FROM kn_improvements WHERE kind = 'proposal' AND change = ? AND status IN ('proposed','approved','declined')", key):
        return False  # never nag twice about the same thing
    db.run("INSERT INTO kn_improvements(day, kind, title, detail_enc, change, status, created_at) VALUES (?, 'proposal', ?, ?, ?, 'proposed', ?)",
           day, title, crypto.seal(why), key, db.now())
    return True


def run_once(force: bool = False, summarise=None) -> dict:
    """One pass. Returns what it did. force=True skips the idle/once-a-day checks (the "Run now" button and tests)."""
    schema.ensure()
    if not force and not due():
        return {"ran": False}
    day, since = today(), (db.get_setting("kn_improve_since", "") or _ago(days=1))
    started = db.now()
    sig = _signals(since)
    lines, applied = [], 0
    for les in sig["lessons"]:
        res = memory.remember(les["text"], "procedural", source_kind="lesson", source_ref=f"adapt_lesson:{les['id']}", confidence=0.8)
        if res["outcome"] in ("created", "superseded"):
            applied += 1
    if applied:
        lines.append(f"I took {applied} piece{'s' if applied != 1 else ''} of your feedback on board, so I'll do it your way next time.")
    for r in sig["runs_failed"]:
        memory.remember(f"Playbook {r['playbook_id']} went wrong {r['n']} time(s) recently: check each step before relying on it.", "procedural",
                        source_kind="improve", source_ref=f"playbook:{r['playbook_id']}", confidence=0.6)
    if sig["failed"]:
        top = sig["failed"][0]
        lines.append(f"{sum(f['n'] for f in sig['failed'])} step(s) failed since I last looked, mostly “{top['tool']}”. I've noted what went wrong so I check it first next time.")
        for f in sig["failed"][:3]:
            memory.remember(f"When using {f['tool']}, double-check first: it failed {f['n']} time(s) ({(f['why'] or 'no reason given')[:120]}).",
                            "procedural", source_kind="improve", source_ref=f"tool:{f['tool']}", confidence=0.6)
    proposed = 0
    for d in sig["denied"]:
        if d["n"] >= 2 and _propose(day, f"Always ask before “{d['tool']}”", {"type": "ask_before", "tool": d["tool"]},
                                    f"You turned this down {d['n']} times recently."):
            proposed += 1
    for u in sig["undone"]:
        if u["n"] >= 2 and u["tool"] and _propose(day, f"Check with you before “{u['tool']}”", {"type": "ask_before", "tool": u["tool"]},
                                                  f"You undid this {u['n']} times recently (last: {u['title']})."):
            proposed += 1
    if proposed:
        lines.append(f"I have {proposed} suggestion{'s' if proposed != 1 else ''} about how I act for you. Nothing changes until you say yes.")
    rr = _rerank(since)
    if rr["boosted"]:
        lines.append(f"{rr['boosted']} thing{'s' if rr['boosted'] != 1 else ''} I remembered turned out useful, so I'll reach for them sooner.")
    cons = memory.consolidate(older_than_days=14, summarise=summarise)
    if cons.get("summaries"):
        lines.append(f"I tidied {cons['episodes']} old notes into {cons['summaries']} short summaries.")
    if not lines:
        lines.append("Quiet day. Nothing needed fixing.")
    note = " ".join(lines)
    db.run("INSERT INTO kn_improvements(day, kind, title, detail_enc, status, created_at) VALUES (?, 'note', 'What I improved', ?, 'done', ?)",
           day, crypto.seal(note), db.now())
    db.set_setting("kn_improve_last", day)
    db.set_setting("kn_improve_since", started)
    return {"ran": True, "note": note, "applied": applied, "proposed": proposed, **rr, "consolidated": cons}


def _out(r: dict) -> dict:
    return {"id": r["id"], "day": r["day"], "kind": r["kind"], "title": r["title"], "detail": crypto.open_text(r["detail_enc"]),
            "change": json.loads(r["change"] or "{}"), "status": r["status"], "created_at": r["created_at"]}


def latest() -> dict:
    schema.ensure()
    note = db.one("SELECT * FROM kn_improvements WHERE kind = 'note' ORDER BY id DESC LIMIT 1")
    props = db.all("SELECT * FROM kn_improvements WHERE kind = 'proposal' AND status = 'proposed' ORDER BY id")
    return {"enabled": enabled(), "idle_minutes": IDLE_MINUTES, "last_run": db.get_setting("kn_improve_last", ""),
            "note": _out(note) if note else None, "proposals": [_out(p) for p in props]}


def decide(pid: int, approve: bool) -> dict:
    r = db.one("SELECT * FROM kn_improvements WHERE id = ? AND kind = 'proposal' AND status = 'proposed'", pid)
    if not r:
        from fastapi import HTTPException
        raise HTTPException(404, "Nothing to decide")
    db.run("UPDATE kn_improvements SET status = ? WHERE id = ?", "approved" if approve else "declined", pid)
    change = json.loads(r["change"] or "{}")
    if approve and change.get("type") == "ask_before":
        rules = set(ask_before())
        rules.add(change["tool"])
        db.set_setting("kn_ask_before", json.dumps(sorted(rules)))
        memory.remember(f"Always ask before {change['tool']}.", "procedural", source_kind="user", pinned=True, confidence=1.0)
    return _out(db.one("SELECT * FROM kn_improvements WHERE id = ?", pid))


def ask_before() -> list[str]:
    try:
        return json.loads(db.get_setting("kn_ask_before", "[]") or "[]")
    except ValueError:
        return []


def hold_reason(tool_name: str) -> str:
    """Approved 'ask before' rules. Only ever makes the assistant more careful."""
    try:
        return "You asked me to check with you before doing this." if tool_name in ask_before() else ""
    except Exception:  # noqa: BLE001
        return ""


async def loop() -> None:
    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            if due():
                await asyncio.to_thread(_thread)
        except Exception as e:  # noqa: BLE001
            log.warning("nightly improvement failed: %s", e)


def _thread() -> None:
    try:
        run_once()
    finally:
        c = getattr(db._local, "conn", None)
        if c is not None:
            c.close()
            db._local.conn = None
