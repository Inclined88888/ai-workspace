"""Bring your history with you (feat/memory-core): ChatGPT, Claude and Grok chat exports, Notion and Obsidian notes.

Accepts the export file as-is (a .zip or the .json inside it). Chats become conversations you can search and the
assistant can recall; notes become Notes. Nothing is sent anywhere; it's all filed and indexed on this computer.
Re-importing the same export does not create duplicates.
"""
from __future__ import annotations

import io
import re
import json
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException

from .. import db
from . import graph, index, live, schema

SOURCES = {"chatgpt": "ChatGPT", "claude": "Claude", "grok": "Grok", "notion": "Notion", "obsidian": "Obsidian"}


def _ts(v) -> str:
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, timezone.utc).isoformat(timespec="seconds")
    return str(v or db.now())


def _json_from(data: bytes, prefer: str = "conversations.json"):
    if data[:2] == b"PK":
        z = zipfile.ZipFile(io.BytesIO(data))
        names = [n for n in z.namelist() if n.endswith(".json")]
        pick = next((n for n in names if n.endswith(prefer)), names[0] if names else None)
        if not pick:
            raise HTTPException(400, "I couldn't find the chats in that file.")
        return json.loads(z.read(pick))
    return json.loads(data)


def _chatgpt(convs) -> list[dict]:
    out = []
    for c in convs:
        msgs = []
        for node in (c.get("mapping") or {}).values():
            m = node.get("message") or {}
            role = (m.get("author") or {}).get("role")
            parts = (m.get("content") or {}).get("parts") or []
            text = "\n".join(p for p in parts if isinstance(p, str)).strip()
            if role in ("user", "assistant") and text:
                msgs.append({"role": role, "content": text, "at": _ts(m.get("create_time") or c.get("create_time"))})
        msgs.sort(key=lambda x: x["at"])
        out.append({"id": c.get("id") or c.get("conversation_id") or c.get("title"), "title": c.get("title") or "Imported chat",
                    "at": _ts(c.get("create_time")), "messages": msgs})
    return out


def _claude(convs) -> list[dict]:
    out = []
    for c in convs:
        msgs = []
        for m in c.get("chat_messages") or []:
            text = m.get("text") or " ".join(x.get("text", "") for x in m.get("content") or [] if isinstance(x, dict))
            role = {"human": "user", "assistant": "assistant"}.get(m.get("sender"), "")
            if role and text.strip():
                msgs.append({"role": role, "content": text.strip(), "at": _ts(m.get("created_at"))})
        out.append({"id": c.get("uuid") or c.get("name"), "title": c.get("name") or "Imported chat", "at": _ts(c.get("created_at")), "messages": msgs})
    return out


def _grok(convs) -> list[dict]:
    """Grok exports vary; accept {conversations:[{conversation:{id,title}, responses:[{response:{sender,message}}]}]} and simple lists."""
    if isinstance(convs, dict):
        convs = convs.get("conversations") or convs.get("data") or []
    out = []
    for c in convs:
        meta = c.get("conversation") or c
        msgs = []
        for r in c.get("responses") or c.get("messages") or []:
            r = r.get("response") or r
            role = "user" if str(r.get("sender") or r.get("role")).lower() in ("human", "user") else "assistant"
            text = r.get("message") or r.get("content") or ""
            if isinstance(text, str) and text.strip():
                msgs.append({"role": role, "content": text.strip(), "at": _ts(r.get("create_time") or r.get("created_at"))})
        out.append({"id": meta.get("id") or meta.get("title"), "title": meta.get("title") or "Imported chat", "at": _ts(meta.get("create_time")), "messages": msgs})
    return out


def _save_chats(source: str, chats: list[dict]) -> dict:
    added = skipped = 0
    for c in chats:
        ext = f"import:{source}:{c['id']}"
        if not c["messages"] or db.one("SELECT 1 AS x FROM conversations WHERE external_id = ?", ext):
            skipped += 1
            continue
        cid = db.run("INSERT INTO conversations(title, channel, external_id, created_at, updated_at) VALUES (?, 'import', ?, ?, ?)",
                     f"{c['title']} ({SOURCES[source]})", ext, c["at"], c["messages"][-1]["at"])
        for m in c["messages"]:
            db.run("INSERT INTO messages(conversation_id, role, content, created_at) VALUES (?, ?, ?, ?)", cid, m["role"], m["content"], m["at"])
        live.after_turn(cid)
        added += 1
    return {"chats": added, "skipped": skipped}


def _notes(source: str, data: bytes) -> dict:
    if data[:2] != b"PK":
        files = {"note.md": data}
    else:
        z = zipfile.ZipFile(io.BytesIO(data))
        files = {n: z.read(n) for n in z.namelist() if n.lower().endswith((".md", ".markdown", ".txt")) and "/." not in n}
    added = skipped = 0
    for name, raw in files.items():
        body = raw.decode("utf-8", "replace")
        title = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        if source == "notion":
            title = title.rsplit(" ", 1)[0] if len(title.rsplit(" ", 1)[-1]) == 32 else title  # drop Notion's id suffix
        if db.one("SELECT 1 AS x FROM docs WHERE title = ? AND body = ?", title, body):
            skipped += 1
            continue
        now = db.now()
        nid = db.run("INSERT INTO docs(title, body, created_at, updated_at) VALUES (?, ?, ?, ?)", title, body, now, now)
        index.put("note", str(nid), body[:20000], title, at=now)
        ent = graph.upsert("note", title, ref=("docs", nid), source="import")
        for link in sorted(set(re.findall(r"\[\[([^\]|#]+)", body)))[:20]:  # Obsidian [[links]] become linked notes
            graph.link(ent["id"], "mentions", graph.upsert("note", link.strip(), source="import")["id"], source="import")
        live.file_item("note", str(nid), f"{title} {body[:500]}", ent["id"])
        added += 1
    return {"notes": added, "skipped": skipped}


def run(source: str, data: bytes) -> dict:
    if source not in SOURCES:
        raise HTTPException(400, "Unknown source")
    schema.ensure()
    try:
        if source in ("notion", "obsidian"):
            res = _notes(source, data)
        else:
            raw = _json_from(data)
            res = _save_chats(source, {"chatgpt": _chatgpt, "claude": _claude, "grok": _grok}[source](raw))
    except (ValueError, KeyError, zipfile.BadZipFile) as e:
        raise HTTPException(400, f"That doesn't look like a {SOURCES[source]} export ({e.__class__.__name__}).") from e
    return {"source": SOURCES[source], **res}
