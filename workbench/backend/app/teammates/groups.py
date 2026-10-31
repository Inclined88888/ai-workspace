"""Group chats: several teammates in one conversation, with @mentions and visible handoffs (feat/grok-style).

* You post into the group. `@Name` sends it to that teammate (several mentions = several replies, in order); no mention
  goes to the lead (the first member).
* Each teammate answers from its own persona and memory, in its own hidden conversation, seeing the recent group
  transcript as data. If its reply mentions another member ("@Researcher can you check..."), that teammate picks it up
  next: a handoff. At most MAX_HOPS replies per message, so two teammates can't talk forever.
* Same rule as the best agent apps: approvals, secret forms and sends never happen in a group. Teammates there get no
  tools that act outside the app; they draft or hand the job to you in a one-to-one chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from fastapi import HTTPException

from .. import config, db
from . import store

log = logging.getLogger("teammates.groups")
MAX_HOPS = 4
TRANSCRIPT = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS tm_groups (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  members TEXT NOT NULL DEFAULT '[]',
  section_id INTEGER,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tm_group_messages (
  id INTEGER PRIMARY KEY,
  group_id INTEGER NOT NULL,
  author_id INTEGER,                 -- NULL = you
  content TEXT NOT NULL,
  reply_to INTEGER,
  handoff_from INTEGER,              -- the teammate that handed this on
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tm_reactions (
  scope TEXT NOT NULL,               -- msg (one-to-one chat message) | group (group message)
  ref INTEGER NOT NULL,
  emoji TEXT NOT NULL,
  PRIMARY KEY (scope, ref, emoji)
);
"""
REACTIONS = ("👍", "❤️", "😂", "‼️", "❓", "👀")
_ready: set[str] = set()
_working: dict[int, list[int]] = {}   # group id -> teammates answering now
_tasks: set[asyncio.Task] = set()


def ensure() -> None:
    store.ensure()
    key = str(config.DB_PATH)
    if key not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(key)


def _out(r: dict | None) -> dict:
    if not r:
        raise HTTPException(404, "Group not found")
    ids = json.loads(r["members"] or "[]")
    mates = {m["id"]: m for m in store.listing()["teammates"]}
    return {"id": r["id"], "name": r["name"], "section_id": r["section_id"], "updated_at": r["updated_at"],
            "members": [mates[i] for i in ids if i in mates], "working": _working.get(r["id"], [])}


def listing() -> list[dict]:
    ensure()
    return [_out(r) for r in db.all("SELECT * FROM tm_groups ORDER BY updated_at DESC")]


def get(gid: int) -> dict:
    ensure()
    return _out(db.one("SELECT * FROM tm_groups WHERE id = ?", gid))


def _members(ids: list[int]) -> list[int]:
    have = {m["id"] for m in store.listing()["teammates"]}
    out = []
    for i in ids:
        if int(i) in have and int(i) not in out:
            out.append(int(i))
    if len(out) < 2:
        raise HTTPException(400, "A group needs at least two teammates")
    return out[:8]


def create(name: str, members: list[int]) -> dict:
    ensure()
    ids = _members(members)
    if not (name or "").strip():
        names = [store.get(i)["name"] for i in ids]
        name = ", ".join(names[:3]) + (" +" if len(names) > 3 else "")
    gid = db.run("INSERT INTO tm_groups(name, members, created_at, updated_at) VALUES (?, ?, ?, ?)", name.strip()[:60], json.dumps(ids),
                 db.now(), db.now())
    return get(gid)


def update(gid: int, name: str | None, members: list[int] | None) -> dict:
    get(gid)
    if name is not None and name.strip():
        db.run("UPDATE tm_groups SET name = ? WHERE id = ?", name.strip()[:60], gid)
    if members is not None:
        db.run("UPDATE tm_groups SET members = ? WHERE id = ?", json.dumps(_members(members)), gid)
    return get(gid)


def delete(gid: int) -> dict:
    ensure()
    for tbl, col in (("tm_group_messages", "group_id"), ("tm_groups", "id")):
        db.run(f"DELETE FROM {tbl} WHERE {col} = ?", gid)
    db.run("DELETE FROM tm_conversations WHERE group_id = ?", gid)
    return {"deleted": gid}


def messages(gid: int, after: int = 0) -> dict:
    g = get(gid)
    rows = db.all("SELECT * FROM tm_group_messages WHERE group_id = ? AND id > ? ORDER BY id LIMIT 500", gid, after)
    reacts: dict[int, list[str]] = {}
    if rows:
        for r in db.all(f"SELECT ref, emoji FROM tm_reactions WHERE scope = 'group' AND ref IN ({','.join('?' * len(rows))})",
                        *[r["id"] for r in rows]):
            reacts.setdefault(r["ref"], []).append(r["emoji"])
    for r in rows:
        r["reactions"] = reacts.get(r["id"], [])
    return {"group": g, "messages": rows}


# Mentions -------------------------------------------------------------------------------------------------------
def mentions(text: str, members: list[dict]) -> list[int]:
    """Members mentioned with @Name, in the order they appear. Longest names first so "@Study buddy" beats "@Study"."""
    found: list[tuple[int, int]] = []
    low = text.lower()
    taken: list[tuple[int, int]] = []
    for m in sorted(members, key=lambda x: -len(x["name"])):
        for hit in re.finditer(r"@" + re.escape(m["name"].lower()) + r"(?![\w])", low):
            span = (hit.start(), hit.end())
            if any(a <= span[0] < b for a, b in taken):
                continue
            taken.append(span)
            found.append((hit.start(), m["id"]))
    seen: list[int] = []
    for _, tid in sorted(found):
        if tid not in seen:
            seen.append(tid)
    return seen


def _transcript(gid: int, names: dict[int, str]) -> str:
    rows = db.all("SELECT * FROM (SELECT * FROM tm_group_messages WHERE group_id = ? ORDER BY id DESC LIMIT ?) ORDER BY id", gid, TRANSCRIPT)
    return "\n".join(f"{'You' if r['author_id'] is None else names.get(r['author_id'], 'A teammate')}: {r['content']}" for r in rows)


def _conversation(gid: int, tid: int) -> int:
    from .. import assistant

    r = db.one("SELECT conversation_id FROM tm_conversations WHERE group_id = ? AND teammate_id = ?", gid, tid)
    if r:
        return r["conversation_id"]
    cid = assistant.new_conversation("web", f"Group chat {gid}", f"group:{gid}:{tid}")
    store.link(cid, tid, gid)
    return cid


async def _answer(gid: int, tid: int, handoff_from: int | None) -> tuple[str, int]:
    from .. import assistant

    g = get(gid)
    names = {m["id"]: m["name"] for m in g["members"]}
    others = ", ".join(f"@{n}" for i, n in names.items() if i != tid)
    prompt = (f"[Group chat “{g['name']}”. Members: you ({names[tid]}) and {others}. Recent messages, oldest first:]\n"
              f"{_transcript(gid, names)}\n\n"
              f"[Reply once, as {names[tid]}, to the latest message addressed to you. Keep it short. To hand part of the job to a "
              "member, mention them with @Name and say exactly what you need. You can't send, post or change anything outside "
              "the app from a group: say so and ask the user to open a one-to-one chat for that.]")
    ctx = assistant.Ctx(channel="web", conversation_id=_conversation(gid, tid))
    text, _ = await assistant.ask(ctx, prompt)
    mid = db.run("INSERT INTO tm_group_messages(group_id, author_id, content, handoff_from, created_at) VALUES (?, ?, ?, ?, ?)",
                 gid, tid, (text or "(no reply)").strip(), handoff_from, db.now())
    db.run("UPDATE tm_groups SET updated_at = ? WHERE id = ?", db.now(), gid)
    return text or "", mid


async def run_round(gid: int, targets: list[int]) -> list[int]:
    """Answer each target in turn, then follow handoffs, up to MAX_HOPS replies in total."""
    queue = [(t, None) for t in targets]
    answered: list[int] = []
    g = get(gid)
    try:
        while queue and len(answered) < MAX_HOPS:
            tid, src = queue.pop(0)
            if tid not in _working.setdefault(gid, []):
                _working[gid].append(tid)
            try:
                text, mid = await _answer(gid, tid, src)
                answered.append(mid)
            except Exception as e:  # noqa: BLE001 - one teammate failing must not silence the group
                log.warning("group %s teammate %s failed: %s", gid, tid, e)
                db.run("INSERT INTO tm_group_messages(group_id, author_id, content, created_at) VALUES (?, ?, ?, ?)", gid, tid,
                       "I couldn't answer just now. Try again in a moment.", db.now())
                text = ""
            finally:
                if tid in _working.get(gid, []):
                    _working[gid].remove(tid)
            for nxt in mentions(text, g["members"]):
                if nxt != tid and nxt not in [q[0] for q in queue]:
                    queue.append((nxt, tid))
    finally:
        _working.pop(gid, None)
    return answered


def post(gid: int, content: str, reply_to: int | None = None, background: bool = True) -> dict:
    g = get(gid)
    content = (content or "").strip()[:20000]
    if not content:
        raise HTTPException(400, "Say something")
    mid = db.run("INSERT INTO tm_group_messages(group_id, author_id, content, reply_to, created_at) VALUES (?, NULL, ?, ?, ?)",
                 gid, content, reply_to, db.now())
    db.run("UPDATE tm_groups SET updated_at = ? WHERE id = ?", db.now(), gid)
    targets = mentions(content, g["members"]) or [g["members"][0]["id"]]
    _working[gid] = list(targets)
    if background:
        task = asyncio.get_running_loop().create_task(run_round(gid, targets))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return {"id": mid, "targets": targets}


def react(scope: str, ref: int, emoji: str, on: bool = True) -> dict:
    ensure()
    if scope not in ("msg", "group") or emoji not in REACTIONS:
        raise HTTPException(400, "Unknown reaction")
    if on:
        db.run("INSERT OR IGNORE INTO tm_reactions(scope, ref, emoji) VALUES (?, ?, ?)", scope, ref, emoji)
    else:
        db.run("DELETE FROM tm_reactions WHERE scope = ? AND ref = ? AND emoji = ?", scope, ref, emoji)
    return {"scope": scope, "ref": ref, "reactions": [r["emoji"] for r in db.all("SELECT emoji FROM tm_reactions WHERE scope = ? AND ref = ?", scope, ref)]}
