"""Memory tiers, provenance, dedupe, contradictions, pinning, scopes, why, forget, consolidation."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from .. import db
from . import crypto, graph, index, schema
from .text import words

TIERS = ("profile", "episodic", "project", "procedural")
PRIVACY = ("normal", "private", "sensitive")
SECRETISH = re.compile(r"password|passcode|\bpin\b|card number|cvv|api key|secret|\btoken\b", re.I)
# Plain patterns that give a fact a (predicate, value) so a newer fact can replace an older one about the same thing
PREDICATES = [
    ("lives_in", re.compile(r"\b(?:live|lives|living|based|moved) (?:in|to) (.+)", re.I)),
    ("works_at", re.compile(r"\b(?:work|works|working|employed) (?:at|for) (.+)", re.I)),
    ("role", re.compile(r"\bis (?:a|an|the) (.+?)(?: at | for |$)", re.I)),
    ("phone", re.compile(r"\b(?:phone|mobile|number) (?:is|:)\s*(.+)", re.I)),
    ("email", re.compile(r"\bemail (?:is|:)\s*(\S+@\S+)", re.I)),
    ("birthday", re.compile(r"\bbirthday (?:is|on|:)\s*(.+)", re.I)),
]


def predicate_of(content: str) -> str:
    for name, rx in PREDICATES:
        if rx.search(content):
            return name
    m = re.search(r"\bprefers? (?:to )?(\w+)", content, re.I)
    return f"prefers:{m.group(1).lower()}" if m else ""


def fingerprint(content: str) -> str:
    return " ".join(sorted({crypto.blind(w) for w in words(content)}))


def _jaccard(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x and y else 0.0


def _out(r: dict) -> dict:
    return {"id": r["id"], "uid": r["uid"], "tier": r["tier"], "content": crypto.open_text(r["content_enc"]),
            "subject_id": r["subject_id"], "predicate": r["predicate"], "project_id": r["project_id"], "scope": r["scope"],
            "privacy": r["privacy"], "pinned": bool(r["pinned"]), "confidence": r["confidence"], "status": r["status"],
            "superseded_by": r["superseded_by"], "source_kind": r["source_kind"], "source_ref": r["source_ref"],
            "evidence": crypto.open_text(r["evidence_enc"]), "observed_at": r["observed_at"], "created_at": r["created_at"],
            "updated_at": r["updated_at"], "last_used_at": r["last_used_at"], "uses": r["uses"], "weight": r["weight"]}


def get(mid: int) -> dict:
    schema.ensure()
    r = db.one("SELECT * FROM kn_memories WHERE id = ?", mid)
    if not r:
        raise HTTPException(404, "Not found")
    return _out(r)


def _index(m: dict) -> None:
    index.put("memory", str(m["id"]), m["content"], "", m["scope"], m["privacy"], m["project_id"], m["observed_at"])


def remember(content: str, tier: str = "profile", *, subject_id: int | None = None, predicate: str | None = None,
             project_id: int | None = None, scope: str = "shared", privacy: str = "normal", pinned: bool = False,
             confidence: float = 0.7, source_kind: str = "user", source_ref: str = "", evidence: str = "",
             observed_at: str | None = None) -> dict:
    """Save one memory. Returns {"memory": ..., "outcome": created|duplicate|superseded|conflict}."""
    schema.ensure()
    content = " ".join((content or "").split())[:1000]
    if len(content) < 3:
        raise HTTPException(400, "Write a short sentence to remember.")
    if tier not in TIERS:
        raise HTTPException(400, f"Unknown memory tier: {tier}")
    if SECRETISH.search(content) and source_kind != "user":
        raise HTTPException(400, "That looks like a password or secret. Keep those in the Vault, not in memory.")
    if privacy not in PRIVACY:
        privacy = "normal"
    fp = fingerprint(content)
    pred = predicate if predicate is not None else (predicate_of(content) if tier == "profile" else "")
    # 1. duplicate: same scope, near-identical words -> refresh the old one instead
    for r in db.all("SELECT * FROM kn_memories WHERE status IN ('active','conflict') AND scope = ? AND tier = ? "
                    "AND COALESCE(subject_id, 0) = COALESCE(?, 0)", scope, tier, subject_id):
        if _jaccard(fp, r["fingerprint"]) >= 0.75:
            db.run("UPDATE kn_memories SET confidence = MIN(1.0, MAX(confidence, ?) + 0.05), updated_at = ?, "
                   "observed_at = MAX(observed_at, ?) WHERE id = ?", confidence, db.now(), observed_at or db.now(), r["id"])
            return {"memory": get(r["id"]), "outcome": "duplicate"}
    now = db.now()
    mid = db.run("INSERT INTO kn_memories(uid, tier, content_enc, fingerprint, subject_id, predicate, project_id, scope, privacy, "
                 "pinned, confidence, source_kind, source_ref, evidence_enc, observed_at, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 uuid.uuid4().hex, tier, crypto.seal(content), fp, subject_id, pred, project_id, scope, privacy, int(pinned),
                 float(confidence), source_kind, source_ref, crypto.seal(evidence[:400]), observed_at or now, now, now)
    outcome = "created"
    # 2. contradiction: same subject + predicate, different words
    if pred:
        for old in db.all("SELECT * FROM kn_memories WHERE id != ? AND status = 'active' AND predicate = ? AND scope = ? "
                          "AND COALESCE(subject_id, 0) = COALESCE(?, 0)", mid, pred, scope, subject_id):
            about_someone_else = subject_id is not None and graph.get(subject_id)["type"] != "user"
            if about_someone_else and old["source_kind"] != "user" or (source_kind == "user" and old["source_kind"] != "user"):
                # facts about other people: newer wins (old kept as history); the user's own word beats a guess
                db.run("UPDATE kn_memories SET status = 'superseded', superseded_by = ?, updated_at = ? WHERE id = ?", mid, now, old["id"])
                index.drop("memory", str(old["id"]))
                outcome = "superseded"
            else:
                db.run("UPDATE kn_memories SET status = 'conflict' WHERE id IN (?, ?)", mid, old["id"])
                db.run("INSERT INTO kn_conflicts(old_id, new_id, created_at) VALUES (?, ?, ?)", old["id"], mid, now)
                outcome = "conflict"
    m = get(mid)
    _index(m)
    return {"memory": m, "outcome": outcome}


def update(mid: int, content: str | None = None, pinned: bool | None = None, privacy: str | None = None,
           scope: str | None = None, tier: str | None = None) -> dict:
    cur = get(mid)
    content = " ".join((content if content is not None else cur["content"]).split())[:1000]
    changed = content != cur["content"]
    db.run("UPDATE kn_memories SET content_enc = ?, fingerprint = ?, pinned = ?, privacy = ?, scope = ?, tier = ?, "
           "source_kind = CASE WHEN ? THEN 'user' ELSE source_kind END, confidence = CASE WHEN ? THEN 1.0 ELSE confidence END, "
           "updated_at = ? WHERE id = ?", crypto.seal(content), fingerprint(content),
           int(cur["pinned"] if pinned is None else pinned), privacy or cur["privacy"], scope or cur["scope"], tier or cur["tier"],
           changed, changed, db.now(), mid)
    m = get(mid)
    if m["status"] in ("active", "conflict"):
        _index(m)
    return m


def forget(mid: int) -> dict:
    """Hard delete (the change log keeps only the id, so a synced phone deletes it too)."""
    schema.ensure()
    get(mid)
    db.run("UPDATE kn_memories SET superseded_by = NULL WHERE superseded_by = ?", mid)
    db.run("UPDATE kn_memories SET consolidated_into = NULL WHERE consolidated_into = ?", mid)
    db.run("DELETE FROM kn_memories WHERE id = ?", mid)
    index.drop("memory", str(mid))
    return {"forgotten": mid}


def list_(tier: str | None = None, scopes: list[str] | None = None, status: str = "active", subject_id: int | None = None,
          project_id: int | None = None, limit: int = 500) -> list[dict]:
    schema.ensure()
    sql, args = "SELECT * FROM kn_memories WHERE 1 = 1", []
    if status:
        sql += " AND status = ?"
        args.append(status)
    for col, val in (("tier", tier), ("subject_id", subject_id), ("project_id", project_id)):
        if val is not None:
            sql += f" AND {col} = ?"
            args.append(val)
    if scopes:
        sql += f" AND scope IN ({','.join('?' * len(scopes))})"
        args += scopes
    return [_out(r) for r in db.all(sql + " ORDER BY pinned DESC, observed_at DESC LIMIT ?", *args, limit)]


def why(mid: int) -> dict:
    """'Why do you know this?': source, when, confidence, what it replaced, how often it was used."""
    m = get(mid)
    src = {"user": "You told me", "chat": "From a chat", "file": "From a file", "email": "From an email", "site": "From a work site",
           "task": "From a task", "teach": "From a 'Show me how'", "feedback": "From your correction",
           "import": "Imported", "consolidation": "Summarised from earlier notes", "adapter": "From your earlier memory"}.get(
        m["source_kind"], m["source_kind"])
    replaced = [{"id": r["id"], "content": crypto.open_text(r["content_enc"]), "observed_at": r["observed_at"]}
                for r in db.all("SELECT id, content_enc, observed_at FROM kn_memories WHERE superseded_by = ?", mid)]
    from_eps = db.one("SELECT COUNT(*) AS n FROM kn_memories WHERE consolidated_into = ?", mid)["n"]
    subject = graph.get(m["subject_id"])["name"] if m["subject_id"] else "you"
    when = m["observed_at"][:10]
    sentence = f"{src} on {when}" + (f" (“{m['evidence'][:120]}”)" if m["evidence"] else "") + \
        f". It's about {subject}, I'm {round(m['confidence'] * 100)}% sure, and I've used it {m['uses']} times."
    return {"memory": m, "summary": sentence, "source": src, "source_ref": m["source_ref"], "replaced": replaced,
            "summarised_from": from_eps, "scope": m["scope"], "privacy": m["privacy"]}


def conflicts() -> list[dict]:
    schema.ensure()
    out = []
    for c in db.all("SELECT * FROM kn_conflicts WHERE status = 'open' ORDER BY id"):
        try:
            out.append({"id": c["id"], "old": get(c["old_id"]), "new": get(c["new_id"]),
                        "question": "These two don't agree. Which one is right now?"})
        except HTTPException:
            pass
    return out


def resolve(cid: int, keep: str) -> dict:
    """keep: old | new | both."""
    c = db.one("SELECT * FROM kn_conflicts WHERE id = ?", cid)
    if not c or c["status"] != "open":
        raise HTTPException(404, "Nothing to decide")
    now = db.now()
    if keep == "both":
        db.run("UPDATE kn_memories SET status = 'active', predicate = '' WHERE id IN (?, ?)", c["old_id"], c["new_id"])
    else:
        win, lose = (c["old_id"], c["new_id"]) if keep == "old" else (c["new_id"], c["old_id"])
        db.run("UPDATE kn_memories SET status = 'active', source_kind = 'user', confidence = 1.0 WHERE id = ?", win)
        db.run("UPDATE kn_memories SET status = 'superseded', superseded_by = ? WHERE id = ?", win, lose)
        index.drop("memory", str(lose))
    db.run("UPDATE kn_conflicts SET status = ?, resolved_at = ? WHERE id = ?", f"kept_{keep}", now, cid)
    for mid in (c["old_id"], c["new_id"]):
        m = get(mid)
        if m["status"] == "active":
            _index(m)
    return {"resolved": cid, "kept": keep}


def mark_used(ids: list[int]) -> None:
    if ids:
        db.run(f"UPDATE kn_memories SET uses = uses + 1, last_used_at = ? WHERE id IN ({','.join('?' * len(ids))})", db.now(), *ids)


def consolidate(older_than_days: int = 14, summarise=None) -> dict:
    """Nightly: episodic notes older than N days, grouped by project (or none), become one durable project/profile
    memory each; the episodes are archived with consolidated_into so 'why' can still show them.
    `summarise(list[str]) -> str` may call a model; the offline default keeps the most-repeated sentences."""
    schema.ensure()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat(timespec="seconds")
    rows = db.all("SELECT * FROM kn_memories WHERE tier = 'episodic' AND status = 'active' AND pinned = 0 AND observed_at < ? "
                  "ORDER BY observed_at", cutoff)
    groups: dict = {}
    for r in rows:
        groups.setdefault((r["project_id"], r["scope"]), []).append(_out(r))
    made = 0
    for (pid, scope), eps in groups.items():
        if len(eps) < 2:
            continue
        texts = [e["content"] for e in eps]
        summary = (summarise or _extractive)(texts)
        first, last = eps[0]["observed_at"][:10], eps[-1]["observed_at"][:10]
        res = remember(f"{summary} ({first} to {last})", "project" if pid else "profile", project_id=pid, scope=scope,
                       confidence=0.8, source_kind="consolidation", source_ref=f"episodes:{len(eps)}", evidence=texts[0][:200])
        new_id = res["memory"]["id"]
        for e in eps:
            db.run("UPDATE kn_memories SET status = 'archived', consolidated_into = ? WHERE id = ?", new_id, e["id"])
            index.drop("memory", str(e["id"]))
        made += 1
    return {"episodes": len(rows), "summaries": made}


def _extractive(texts: list[str]) -> str:
    from collections import Counter

    freq = Counter(w for t in texts for w in set(words(t)))
    ranked = sorted(texts, key=lambda t: -sum(freq[w] for w in set(words(t))) / (1 + len(words(t)) ** 0.5))
    return " ".join(ranked[:2])[:600]
