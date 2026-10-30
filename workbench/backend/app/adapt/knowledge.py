"""Personal memory: "What I know about you".

Facts are learned automatically from conversations (after each turn), from files and tasks (observe_text), from
teaching sessions and from the user's corrections. They are stored encrypted (adapt_facts.content_enc) and found again
with BM25 over the decrypted text, held in memory per process and rebuilt on change. Lightweight and offline: no
embedding model to download. Switch it off with the setting adapt_memory (learning) or adapt_memory_context (use in
chat). The user sees, edits and deletes everything on the "What I know about you" page.
"""

import math
import re
import threading
from collections import Counter

from fastapi import HTTPException

from .. import db, llm
from . import store

CATEGORIES = {
    "about": "About you",
    "work": "Your work",
    "people": "People, clients and projects",
    "preference": "How you like things done",
    "format": "Formats and tone",
    "fact": "Other things to remember",
}
# The fake test model and older prompts use profile | preference | fact
_KIND_ALIAS = {"profile": "about", "identity": "about", "job": "work", "project": "people", "client": "people",
               "tone": "format", "style": "format"}
STOP = set("a an the and or of to in on for with at by from is are was were be been it this that my your our i me you "
           "we they he she them his her their as do does did not no yes so if but than then there here have has had "
           "will would can could should about into over under very just also".split())
_cache: dict = {"key": None, "docs": []}
_cache_lock = threading.Lock()


def learning_on() -> bool:
    return store.flag("adapt_memory", True)


def context_on() -> bool:
    return store.flag("adapt_memory_context", True)


def words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9][a-z0-9'+#.-]*", (text or "").lower()):
        w = w.strip(".'-")
        if len(w) < 2 or w in STOP:
            continue
        w = stem(w)
        out.append(w)
    return out


def stem(w: str) -> str:
    """Tiny stemmer so invoices ~ invoice and chasing ~ chase."""
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 4 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:-1] if len(w) > 3 and w.endswith("e") else w


def fingerprint(text: str) -> str:
    return " ".join(sorted(set(words(text))))


def _category(value) -> str:
    v = str(value or "fact").lower()
    v = _KIND_ALIAS.get(v, v)
    return v if v in CATEGORIES else "fact"


def _row(r: dict) -> dict:
    return {"id": r["id"], "content": store.open_text(r["content_enc"]), "category": r["category"],
            "category_label": CATEGORIES.get(r["category"], r["category"]), "source": r["source"],
            "evidence": store.open_text(r["evidence_enc"]), "confidence": r["confidence"], "pinned": bool(r["pinned"]),
            "uses": r["uses"], "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _invalidate() -> None:
    with _cache_lock:
        _cache["key"] = None


def all_facts() -> list[dict]:
    store.ensure()
    rows = db.all("SELECT * FROM adapt_facts ORDER BY pinned DESC, updated_at DESC")
    return [_row(r) for r in rows]


def get(fid: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM adapt_facts WHERE id = ?", fid)
    if not r:
        raise HTTPException(404, "Not found")
    return _row(r)


def _similar(fp: str, existing: list[str]) -> bool:
    a = set(fp.split())
    if len(a) < 2:
        return fp in existing
    for e in existing:
        b = set(e.split())
        if b and len(a & b) / len(a | b) >= 0.75:
            return True
    return False


def add(content: str, category: str = "fact", source: str = "user", evidence: str = "", confidence: float = 0.7,
        pinned: bool = False, conversation_id: int | None = None, dedupe: bool = True) -> dict | None:
    store.ensure()
    content = " ".join((content or "").split())[:500]
    if len(content) < 4:
        raise HTTPException(400, "Write a short sentence to remember.")
    fp = fingerprint(content)
    if dedupe and _similar(fp, [r["fingerprint"] for r in db.all("SELECT fingerprint FROM adapt_facts")]):
        return None
    cat = _category(category)
    fid = db.run("INSERT INTO adapt_facts(content_enc, category, source, evidence_enc, confidence, pinned, fingerprint, "
                 "conversation_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 store.seal(content), cat, source, store.seal(evidence[:300]), float(confidence), int(pinned or cat == "about"),
                 fp, conversation_id, db.now(), db.now())
    _invalidate()
    return get(fid)


def update(fid: int, content: str | None = None, category: str | None = None, pinned: bool | None = None) -> dict:
    cur = get(fid)
    content = " ".join((content if content is not None else cur["content"]).split())[:500]
    if len(content) < 4:
        raise HTTPException(400, "Write a short sentence to remember.")
    db.run("UPDATE adapt_facts SET content_enc = ?, category = ?, pinned = ?, fingerprint = ?, source = CASE WHEN ? THEN 'user' "
           "ELSE source END, confidence = CASE WHEN ? THEN 1.0 ELSE confidence END, updated_at = ? WHERE id = ?",
           store.seal(content), _category(category or cur["category"]), int(cur["pinned"] if pinned is None else pinned),
           fingerprint(content), content != cur["content"], content != cur["content"], db.now(), fid)
    _invalidate()
    return get(fid)


def delete(fid: int) -> dict:
    store.ensure()
    db.run("DELETE FROM adapt_facts WHERE id = ?", fid)
    _invalidate()
    return {"deleted": fid}


def forget_everything() -> dict:
    store.ensure()
    n = db.one("SELECT COUNT(*) AS n FROM adapt_facts")["n"]
    db.run("DELETE FROM adapt_facts")
    _invalidate()
    return {"deleted": n}


# Retrieval -----------------------------------------------------------------------------------------------------------
def _docs() -> list[dict]:
    store.ensure()
    key = db.one("SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS u, COALESCE(MAX(id), 0) AS m FROM adapt_facts")
    key = (key["n"], key["u"], key["m"])
    with _cache_lock:
        if _cache["key"] != key:
            docs = []
            for f in all_facts():
                docs.append({**f, "terms": Counter(words(f["content"] + " " + f["category_label"]))})
            _cache.update(key=key, docs=docs)
        return _cache["docs"]


def search(query: str, limit: int = 8) -> list[dict]:
    """BM25 (k1 1.4, b 0.75) over the user's facts."""
    docs = _docs()
    q = set(words(query))
    if not docs or not q:
        return []
    n = len(docs)
    avg = sum(sum(d["terms"].values()) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d["terms"]) if t in q)
    scored = []
    for d in docs:
        dl = sum(d["terms"].values()) or 1
        s = 0.0
        for t in q:
            tf = d["terms"].get(t, 0)
            if tf:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf * 2.4 / (tf + 1.4 * (1 - 0.75 + 0.75 * dl / avg))
        if s > 0:
            scored.append((s, d))
    scored.sort(key=lambda x: -x[0])
    return [{k: v for k, v in d.items() if k != "terms"} | {"score": round(s, 3)} for s, d in scored[:limit]]


def context_block(user_text: str, limit: int = 8) -> str:
    """The lines added to the assistant's system prompt: pinned facts always, then the most relevant ones."""
    if not context_on():
        return ""
    docs = _docs()
    if not docs:
        return ""
    chosen, seen = [], set()
    for d in [*[d for d in docs if d["pinned"]][:15], *search(user_text, limit), *docs[:3]]:
        if d["id"] not in seen:
            seen.add(d["id"])
            chosen.append(d)
    if chosen:
        db.run(f"UPDATE adapt_facts SET uses = uses + 1 WHERE id IN ({','.join('?' * len(chosen))})", *[d["id"] for d in chosen])
    lines = [f"- ({d['category_label']}) {d['content']}" for d in chosen]
    return ("What you have learned about this person and their work (adapt to it: their field, clients, tone and "
            "formats; never mention this list unless asked):\n" + "\n".join(lines))


# Learning ------------------------------------------------------------------------------------------------------------
# Keep the phrase "long term memory": the offline test model answers prompts containing it with a profile fact.
MINE_PROMPT = (
    "You keep the long term memory for ONE person so their assistant adapts to them, whatever their field (trades, "
    "office, law, science, study, maths, design, music, anything). From the new material, extract durable facts worth "
    "remembering: who they are and what they do, their field and role, recurring clients, projects, sites or courses, "
    "important people, how they like things done, tone and document formats they use. Only what the person stated, "
    "showed or clearly confirmed; never guess; skip one-off requests, small talk, secrets (passwords, card numbers) and "
    "anything already known. "
    'Reply with only a JSON array (possibly empty) of {"content": "one short sentence", '
    '"kind": "about|work|people|preference|format|fact"}. Max 5 items.'
)
SECRETISH = re.compile(r"password|passcode|\bpin\b|card number|cvv|api key|token|secret", re.I)


async def model_json(prompt: str, payload: str, model: str | None = None, channel: str = "adapt"):
    """One JSON reply from the conversation's model (patched in tests)."""
    res = await llm.chat(model or None, [{"role": "system", "content": prompt}, {"role": "user", "content": payload}],
                         trace={"channel": f"learn:{channel}"})
    return llm.parse_json(res["message"].get("content") or "")


async def learn_from(material: str, source: str, evidence: str = "", model: str | None = None,
                     conversation_id: int | None = None) -> list[dict]:
    if not learning_on() or len((material or "").strip()) < 40:
        return []
    known = [f["content"] for f in all_facts()[:80]]
    payload = "Already known:\n" + "\n".join(f"- {k}" for k in known) + f"\n\nNew material ({source}):\n" + material[:20000]
    items = await model_json(MINE_PROMPT, payload, model, source)
    added = []
    for it in (items if isinstance(items, list) else [])[:5]:
        if not isinstance(it, dict) or not isinstance(it.get("content"), str) or SECRETISH.search(it["content"]):
            continue
        try:
            f = add(it["content"], it.get("kind") or it.get("category") or "fact", source, evidence, 0.7,
                    conversation_id=conversation_id)
        except HTTPException:
            continue
        if f:
            added.append(f)
    return added


async def mine_conversation(conv_id: int) -> list[dict]:
    """Replaces learn.mine_memories when adapt memory is on: same cursor, encrypted store."""
    conv = db.one("SELECT * FROM conversations WHERE id = ?", conv_id)
    if not conv:
        return []
    rows = db.all("SELECT id, role, content FROM messages WHERE conversation_id = ? AND id > ? AND role IN ('user', 'assistant') "
                  "ORDER BY id", conv_id, conv["memory_cursor"])
    if not rows or sum(len(r["content"]) for r in rows if r["role"] == "user") < 40:
        return []
    db.run("UPDATE conversations SET memory_cursor = ? WHERE id = ?", rows[-1]["id"], conv_id)
    text = "\n".join(f"{r['role'].upper()}: {r['content'][:2000]}" for r in rows)
    first_user = next((r["content"] for r in rows if r["role"] == "user"), "")
    return await learn_from(text, "chat", first_user[:200], conv["model"] or None, conv_id)
