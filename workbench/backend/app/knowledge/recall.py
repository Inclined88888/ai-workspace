"""One recall API: hybrid BM25 + vectors + graph expansion + recency + pinning, then a context budgeter."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import db
from . import graph, index, memory, schema
from .text import norm_name, words

HALF_LIFE_DAYS = {"profile": 365.0, "procedural": 180.0, "project": 60.0, "episodic": 14.0}


def _age_days(iso: str) -> float:
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 86400)
    except ValueError:
        return 0.0


def visible_scopes(assistant: str | None = None, team: str | None = None, include_private: bool = True) -> list[str]:
    s = ["shared"]
    if include_private:
        s.append("private")
    if assistant:
        s.append(f"assistant:{assistant}")
    if team:
        s.append(f"team:{team}")
    return s


def _mentioned_entities(query: str) -> list[int]:
    """Entities named in the query (whole name or alias)."""
    ws = norm_name(query).split()
    hits = set()
    for n in (3, 2, 1):
        for i in range(len(ws) - n + 1):
            e = graph.find(" ".join(ws[i:i + n]))
            if e:
                hits.add(e["id"])
    return list(hits)


def recall(query: str, *, scopes: list[str] | None = None, project_id: int | None = None, k: int = 10,
           tiers: tuple[str, ...] | None = None, use_vectors: bool = True, use_graph: bool = True) -> list[dict]:
    """Ranked memories for a request. Scores fuse (reciprocal rank) lexical, vector and graph hits, then weight by
    tier recency, confidence, pinning, the nightly re-rank weight and the current project."""
    schema.ensure()
    scopes = scopes or visible_scopes()
    fused: dict[int, float] = {}
    lex = index.bm25(query, 80, ("memory",))
    for rank, (did, _) in enumerate(lex):
        fused[did] = fused.get(did, 0) + 1.0 / (60 + rank)
    if use_vectors:
        for rank, (did, _) in enumerate(index.vector_search(query, 80)):
            fused[did] = fused.get(did, 0) + 0.8 / (60 + rank)
    by_doc = {r["id"]: int(r["ref"]) for r in db.all(
        f"SELECT id, ref FROM kn_docs WHERE kind = 'memory' AND id IN ({','.join('?' * len(fused)) or 'NULL'})", *fused)}
    scores: dict[int, float] = {by_doc[d]: s for d, s in fused.items() if d in by_doc}
    if use_graph:
        ents = _mentioned_entities(query)
        related = set(ents)
        for e in ents:
            related |= {n["id"] for n in graph.neighbours(e, 1)}
        if related:
            ph = ",".join("?" * len(related))
            for r in db.all(f"SELECT id, subject_id FROM kn_memories WHERE status = 'active' AND (subject_id IN ({ph}) OR project_id IN ({ph}))",
                            *related, *related):
                direct = r["subject_id"] in ents
                scores[r["id"]] = scores.get(r["id"], 0) + (1.0 / 61 if direct else 0.5 / 61)
    if not scores:
        return []
    ph = ",".join("?" * len(scores))
    rows = db.all(f"SELECT * FROM kn_memories WHERE id IN ({ph}) AND status = 'active' AND scope IN ({','.join('?' * len(scopes))})",
                  *scores, *scopes)
    out = []
    top = max(scores.values())
    for r in rows:
        m = memory._out(r)
        if tiers and m["tier"] not in tiers:
            continue
        rel = scores[m["id"]] / top
        recency = 0.5 ** (_age_days(m["observed_at"]) / HALF_LIFE_DAYS.get(m["tier"], 60))
        s = rel * (0.6 + 0.25 * recency + 0.15 * m["confidence"]) * m["weight"]
        if project_id and m["project_id"] == project_id:
            s *= 1.3
        if m["pinned"]:
            s *= 1.15
        out.append({**m, "score": round(s, 4)})
    out.sort(key=lambda x: -x["score"])
    return out[:k]


def search_all(query: str, scopes: list[str] | None = None, limit: int = 30) -> list[dict]:
    """Universal search (Cmd/Ctrl+K): everything in the index, grouped by kind on the client."""
    schema.ensure()
    scopes = scopes or visible_scopes()
    hits = dict(index.bm25(query, limit * 3))
    for did, s in index.vector_search(query, limit):
        hits[did] = hits.get(did, 0) + s
    if not hits:
        return []
    ph = ",".join("?" * len(hits))
    docs = db.all(f"SELECT * FROM kn_docs WHERE id IN ({ph}) AND scope IN ({','.join('?' * len(scopes))})", *hits, *scopes)
    for d in docs:
        d["score"] = round(hits[d["id"]], 4)
    docs.sort(key=lambda d: -d["score"])
    for d in docs:
        if d["kind"] == "memory" and not d["title"]:
            try:
                d["title"] = memory.get(int(d["ref"]))["content"][:120]
            except Exception:  # noqa: BLE001
                pass
    return [{"kind": d["kind"], "ref": d["ref"], "title": d["title"], "project_id": d["project_id"], "at": d["at"],
             "score": d["score"]} for d in docs[:limit]]


def _tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def context_for(query: str, *, budget_tokens: int = 600, scopes: list[str] | None = None, project_id: int | None = None) -> dict:
    """Context budgeter: pinned profile facts first (capped at a third of the budget), then recalled memories by
    score per token, skipping near-duplicates, until the budget is spent. Returns the prompt block and the ids used."""
    scopes = scopes or visible_scopes()
    chosen, used, fps = [], 0, []
    pinned = [m for m in memory.list_(scopes=scopes) if m["pinned"]]
    pool = [(m, 10.0) for m in pinned] + [(m, m["score"]) for m in recall(query, scopes=scopes, project_id=project_id, k=40)]
    for m, _ in pool:
        if any(m["id"] == c["id"] for c in chosen):
            continue
        line = f"- [{m['id']}] {m['content']}"
        cost = _tokens(line)
        if m["pinned"] and used + cost > budget_tokens / 3 and chosen:
            continue
        if used + cost > budget_tokens:
            continue
        fp = set(m.get("content", "").lower().split())
        if any(len(fp & f) / max(1, len(fp | f)) > 0.8 for f in fps):
            continue
        fps.append(fp)
        chosen.append(m)
        used += cost
    memory.mark_used([m["id"] for m in chosen])
    block = ("What you know that matters for this request (ids in brackets; say where a fact came from if asked):\n" +
             "\n".join(f"- [{m['id']}] {m['content']}" for m in chosen)) if chosen else ""
    return {"block": block, "ids": [m["id"] for m in chosen], "tokens": used}


def precision_at_k(cases: list[dict], k: int = 5, **kw) -> float:
    """Benchmark helper: cases = [{"query": str, "relevant": set(ids)}]."""
    total = 0.0
    for c in cases:
        got = [m["id"] for m in recall(c["query"], k=k, **kw)]
        total += len([g for g in got if g in c["relevant"]]) / k
    return total / max(1, len(cases))


def recall_at_k(cases: list[dict], k: int = 5, **kw) -> float:
    total = 0.0
    for c in cases:
        got = {m["id"] for m in recall(c["query"], k=k, **kw)}
        total += len(got & c["relevant"]) / max(1, len(c["relevant"]))
    return total / max(1, len(cases))
