"""Self improvement, run in the background after each turn:

* memories: durable facts about the user are mined from new messages (deduplicated against what is known)
* skills: after a complex task (several tool calls) the model decides whether to save or improve a reusable procedure

Both use the conversation's model and can be switched off in Settings (learning costs extra model calls).
"""

import asyncio
import json
import logging
import re

from fastapi import HTTPException

from . import db, llm, usage

log = logging.getLogger("learn")
_lock = asyncio.Lock()
_tasks: set[asyncio.Task] = set()
SKILL_MIN_TOOL_CALLS = 3
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")


def enabled() -> bool:
    return db.get_setting("learning_enabled", "1") == "1"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", s.lower()).strip()


def upsert_skill(name: str, description: str, steps: str, source: str = "auto") -> dict:
    name = name.strip().lower().replace(" ", "-")
    if not NAME_RE.match(name):
        raise HTTPException(400, "Skill name must be short-kebab-case (letters, digits, dashes).")
    if not steps.strip() or not description.strip():
        raise HTTPException(400, "A skill needs a description and steps.")
    existing = db.one("SELECT id FROM skills WHERE name = ?", name)
    if existing:
        db.run("UPDATE skills SET description = ?, steps = ?, updated_at = ? WHERE id = ?",
               description.strip()[:300], steps.strip()[:20000], db.now(), existing["id"])
        return {"updated": name}
    db.run("INSERT INTO skills(name, description, steps, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
           name, description.strip()[:300], steps.strip()[:20000], source, db.now(), db.now())
    return {"created": name}


async def _model_json(conv: dict, prompt: str, payload: str, channel: str):
    res = await llm.chat(conv["model"] or None, [{"role": "system", "content": prompt}, {"role": "user", "content": payload}])
    usage.record(res, channel=f"learn:{channel}", agent_id=conv.get("agent_id"), conversation_id=conv["id"])
    return llm.parse_json(res["message"].get("content") or "")


MEMORY_PROMPT = (
    "You maintain long term memory about ONE user. From the new messages, extract durable facts worth remembering for "
    "future conversations: who they are (name, work, location, family), goals and ongoing projects, preferences and "
    "how they like things done, important people and commitments. Only facts the USER stated or clearly confirmed. "
    "Skip small talk, one-off requests and anything already known. "
    'Reply with only a JSON array (possibly empty) of {"content": "...", "kind": "profile|preference|fact"}. '
    "Use profile for core identity facts. Max 5 items, each one short sentence."
)


async def mine_memories(conv_id: int) -> int:
    conv = db.one("SELECT * FROM conversations WHERE id = ?", conv_id)
    if not conv:
        return 0
    rows = db.all("SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? AND role IN ('user', 'assistant') "
                  "ORDER BY id", conv_id, conv["memory_cursor"])
    user_chars = sum(len(r["content"]) for r in rows if r["role"] == "user")
    if not rows or user_chars < 40:
        return 0
    known = db.all("SELECT content FROM memories ORDER BY updated_at DESC LIMIT 60")
    payload = ("Already known:\n" + "\n".join(f"- {k['content']}" for k in known) + "\n\nNew messages:\n" +
               "\n".join(f"{r['role'].upper()}: {r['content'][:2000]}" for r in rows))
    items = await _model_json(conv, MEMORY_PROMPT, payload[:24000], "memory")
    db.run("UPDATE conversations SET memory_cursor = ? WHERE id = ?", rows[-1]["id"], conv_id)
    if not isinstance(items, list):
        return 0
    known_norm = {_norm(k["content"]) for k in db.all("SELECT content FROM memories")}
    added = 0
    for it in items[:5]:
        if not isinstance(it, dict) or not isinstance(it.get("content"), str):
            continue
        content = it["content"].strip()[:500]
        n = _norm(content)
        if len(n) < 8 or n in known_norm or any(n in k or k in n for k in known_norm if len(k) > 15):
            continue
        kind = it.get("kind") if it.get("kind") in ("profile", "preference", "fact") else "fact"
        db.run("INSERT INTO memories(content, kind, source, conversation_id, created_at, updated_at) VALUES (?, ?, 'auto', ?, ?, ?)",
               content, kind, conv_id, db.now(), db.now())
        known_norm.add(n)
        added += 1
    return added


SKILL_PROMPT = (
    "You review a completed task by an AI assistant and decide whether its procedure should become a reusable skill: "
    "a recurring kind of task where written steps would make the next run faster or more reliable. "
    "Do not save one-off questions or trivial lookups. If an existing skill covers it, improve that skill (same name). "
    'Reply with only JSON: {"save": true|false, "name": "short-kebab-case", "description": "when to use it, one sentence", '
    '"steps": "numbered Markdown steps naming the tools to use and pitfalls seen"}'
)


async def mine_skill(conv_id: int) -> str:
    conv = db.one("SELECT * FROM conversations WHERE id = ?", conv_id)
    last_user = db.one("SELECT id FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY id DESC LIMIT 1", conv_id)
    if not conv or not last_user:
        return ""
    rows = db.all("SELECT role, content, tool_calls, name FROM messages WHERE conversation_id = ? AND id >= ? ORDER BY id",
                  conv_id, last_user["id"])
    from .assistant import UNTRUSTED_TOOLS

    if any(r["role"] == "tool" and r["name"] in UNTRUSTED_TOOLS for r in rows):
        return ""  # a skill learned from a turn that read web pages or email could carry injected instructions forever
    lines = []
    for r in rows:
        if r["role"] == "tool":
            lines.append(f"TOOL RESULT {r['name']}: {r['content'][:600]}")
        elif r["tool_calls"]:
            calls = ", ".join(f"{c['function']['name']}({c['function']['arguments'][:200]})" for c in json.loads(r["tool_calls"]))
            lines.append(f"ASSISTANT CALLED: {calls}")
        else:
            lines.append(f"{r['role'].upper()}: {r['content'][:2000]}")
    skills = db.all("SELECT name, description FROM skills")
    payload = ("Existing skills:\n" + "\n".join(f"- {s['name']}: {s['description']}" for s in skills) +
               "\n\nTranscript:\n" + "\n".join(lines))
    out = await _model_json(conv, SKILL_PROMPT, payload[:24000], "skill")
    if isinstance(out, dict) and out.get("save") is True:
        try:
            r = upsert_skill(str(out.get("name", "")), str(out.get("description", "")), str(out.get("steps", "")))
            return next(iter(r.values()))
        except HTTPException:
            return ""
    return ""


async def _after(conv_id: int, tool_calls: int) -> None:
    async with _lock:  # one learning job at a time keeps it light
        try:
            await mine_memories(conv_id)
            if tool_calls >= SKILL_MIN_TOOL_CALLS:
                await mine_skill(conv_id)
        except Exception as e:  # noqa: BLE001 - learning is best effort and must never break chat
            log.warning("learning failed for conversation %s: %s", conv_id, e)


def after_turn(ctx) -> None:
    if not enabled():
        return
    try:
        task = asyncio.get_running_loop().create_task(_after(ctx.conversation_id, ctx.tool_calls))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
