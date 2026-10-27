"""Memory retrieval: keyword search that understands everyday wording, with optional meaning-based search.

Always on, no model needed:
  1. SQLite FTS5 over the memory text (porter stemming), ranked by bm25.
  2. The same search with the query widened by a small table of everyday equivalents (partner -> wife, husband...)
     and by spelling correction against words that actually occur in memories (dentst -> dentist).
Optional: 3. meaning-based search with an embedding model from a provider you already connected (Settings, Memory).
Vectors are stored in SQLite and compared in Python, which is fine for the thousands of memories one person has.

The lists are merged by reciprocal rank fusion, weighted by each memory's confidence, and near duplicates are folded.
"""

import difflib
import logging
import math
import re
import struct

import httpx

from . import db

log = logging.getLogger("memory")

STOP = set("""a an the and or of to in on at for with by from about as is are was were be been being am do does did i me my
mine we our you your he she they them their his her its it this that these those what which who whom whose when where why how
can could should would will shall may might must have has had any some all no not please tell know remember again also than
then there here just very into out up down over under more most much many only own same so such too""".split())

# Everyday equivalents. General vocabulary for a personal assistant, not tuned per user.
ALIASES = [
    "partner wife husband spouse girlfriend boyfriend fiance fiancee married",
    "kid kids child children son sons daughter daughters boy girl",
    "pet dog puppy cat kitten",
    "boss manager supervisor director reports",
    "live lives home house address suburb city town",
    "job work works employer company role career occupation workplace",
    "doctor gp physician medical clinic",
    "dentist dental teeth",
    "gym workout exercise fitness training",
    "car vehicle drive drives rego registration",
    "loan mortgage",
    "holiday trip vacation travel",
    "email emails mail inbox",
    "coffee latte espresso flat cappuccino",
    "tea drink drinks",
    "food diet eat eats vegetarian vegan meal meals",
    "computer laptop macbook pc desktop",
    "phone mobile cell smartphone",
    "bedtime sleep bed",
    "parent parents mum mom mother dad father",
    "sister brother sibling siblings",
    "birthday born",
    "anniversary wedding",
    "school college university study studying course",
    "insurance policy cover",
    "bank banking account",
    "money budget spending finance finances",
    "retirement superannuation super pension",
    "rent lease landlord",
    "meeting meetings call calls",
    "medication medicine tablets pills allergy allergic",
    "asthma inhaler breathing",
    "storm storms thunderstorm thunderstorms thunder",
    "scared afraid fear",
    "recycling diversion landfill waste",
    "garden gardening veggie vegetables plants",
    "roster shift shifts schedule",
    "report reports summary summaries format",
    "commute train bus bike cycling",
]
_ALIAS: dict[str, set[str]] = {}
for _g in ALIASES:
    _words = set(_g.split())
    for _w in _words:
        _ALIAS.setdefault(_w, set()).update(_words - {_w})

_vocab_cache: tuple[tuple, set[str]] = ((), set())


def tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in STOP and len(t) > 1]


def _singular(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _vocab() -> set[str]:
    global _vocab_cache
    key = tuple(db.one("SELECT COUNT(*) AS n, COALESCE(MAX(updated_at), '') AS u FROM memories").values())
    if key != _vocab_cache[0]:
        words: set[str] = set()
        for r in db.all("SELECT content FROM memories"):
            words.update(tokens(r["content"]))
        _vocab_cache = (key, words)
    return _vocab_cache[1]


def expand(query: str) -> tuple[list[str], list[str]]:
    """(the query's own words, extra words: everyday equivalents and spelling corrections)."""
    own = tokens(query)
    vocab = _vocab()
    extra: list[str] = []
    for t in own:
        for w in (t, _singular(t)):
            extra.extend(sorted(_ALIAS.get(w, ())))
        if len(t) >= 4 and t not in vocab and _singular(t) not in vocab and t not in _ALIAS:
            extra.extend(difflib.get_close_matches(t, vocab, n=2, cutoff=0.8))
    seen = set(own)
    return own, [w for w in extra if not (w in seen or seen.add(w))]


EVERYWHERE = -1


def scope_sql(project_id: int | None, alias: str = "") -> tuple[str, list]:
    """Which memories are visible: outside projects only user wide ones; in a project its own plus user wide ones, or
    for an isolated project its own plus only who the user is and how they like things done (profile, preference)."""
    a = f"{alias}." if alias else ""
    if project_id == EVERYWHERE:
        return "1 = 1", []  # the user's own memory page sees everything
    if not project_id:
        return f"{a}project_id IS NULL", []
    p = db.one("SELECT isolated FROM projects WHERE id = ?", project_id)
    if p and p["isolated"]:
        return f"({a}project_id = ? OR ({a}project_id IS NULL AND {a}kind IN ('profile', 'preference')))", [project_id]
    return f"({a}project_id IS NULL OR {a}project_id = ?)", [project_id]


def scope_for(kind: str, project_id: int | None, scope: str = "") -> int | None:
    """Where a new memory is saved: things about the user are user wide; facts learned in a project stay in it."""
    if not project_id or scope == "user" or (kind in ("profile", "preference") and scope != "project"):
        return None
    return project_id


def _fts(words: list[str], limit: int, project_id: int | None = None) -> list[int]:
    if not words:
        return []
    q = " OR ".join(f'"{w}"*' for w in words[:24])
    where, args = scope_sql(project_id, "m")
    try:
        rows = db.all(f"SELECT m.id FROM memories m JOIN memories_fts f ON f.rowid = m.id WHERE memories_fts MATCH ? AND {where} "
                      "ORDER BY bm25(memories_fts) LIMIT ?", q, *args, limit)
    except Exception:  # noqa: BLE001 - a malformed query must not break chat
        return []
    return [r["id"] for r in rows]


def _similar(a: str, b: str) -> bool:
    ta, tb = set(tokens(a)), set(tokens(b))
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.8


def search(query: str, limit: int = 8, project_id: int | None = None) -> list[dict]:
    """Best matching memories visible in this scope (see scope_sql), most relevant first, each with how it was found."""
    own, extra = expand(query)
    channels: list[tuple[str, float, list[int]]] = [("words", 1.0, _fts(own, 30, project_id)), ("related words", 0.8, _fts(extra, 30, project_id))]
    sem = semantic(query, 30, project_id)
    if sem:
        channels.append(("meaning", 1.0, sem))
    scores: dict[int, float] = {}
    how: dict[int, list[str]] = {}
    for name, weight, ids in channels:
        for rank, mid in enumerate(ids):
            scores[mid] = scores.get(mid, 0) + weight / (60 + rank)
            how.setdefault(mid, []).append(name)
    if not scores:
        return []
    where, args = scope_sql(project_id)
    rows = {r["id"]: r for r in db.all(
        f"SELECT id, content, kind, source, confidence, conversation_id, project_id, previous, created_at, updated_at FROM memories "
        f"WHERE id IN ({','.join('?' * len(scores))}) AND {where}", *scores, *args)}
    ranked = sorted((i for i in scores if i in rows),
                    key=lambda i: (scores[i] * (0.85 + 0.15 * float(rows[i]["confidence"] or 0.7)), rows[i]["updated_at"]), reverse=True)
    out: list[dict] = []
    for i in ranked:
        r = rows[i]
        if any(_similar(r["content"], o["content"]) for o in out):
            continue  # fold near duplicates; the better ranked one is kept
        out.append({**r, "score": round(scores[i] * 1000, 2), "matched_by": how[i]})
        if len(out) >= limit:
            break
    return out


# Optional meaning-based search
def embed_model() -> str:
    """Provider model reference used for embeddings ('3/nomic-embed-text'), or '' when off."""
    return db.get_setting("memory_embed_model", "") or ""


def _pack(v: list[float]) -> bytes:
    return struct.pack(f"{len(v)}f", *v)


def _unpack(b: bytes) -> list[float]:
    return list(struct.unpack(f"{len(b) // 4}f", b))


def embed(texts: list[str], ref: str | None = None) -> list[list[float]]:
    """Embeddings from an OpenAI compatible /embeddings endpoint (Ollama, OpenAI, Gemini and others serve one)."""
    from .llm import _headers, split_ref

    p, model = split_ref(ref or embed_model())
    r = httpx.post(p["base_url"].rstrip("/") + "/embeddings", json={"model": model, "input": texts}, headers=_headers(p), timeout=10)
    r.raise_for_status()
    data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
    return [d["embedding"] for d in data]


def _project_requires_local(project_id: int | None) -> bool:
    if project_id == EVERYWHERE:
        # Global memory search may include private projects. Do not export its
        # query to a cloud embedding model while any local-only project exists.
        return bool(db.one("SELECT 1 FROM projects WHERE local_only = 1 LIMIT 1"))
    if not project_id:
        return False
    row = db.one("SELECT local_only FROM projects WHERE id = ?", project_id)
    return bool(row and row["local_only"])


def _embedding_allowed(ref: str, project_id: int | None) -> bool:
    from . import llm
    return not _project_requires_local(project_id) or llm.is_local(ref)


def index_missing(batch: int = 64) -> int:
    """Embed memories that have no vector for the current model yet. Returns how many were added."""
    ref = embed_model()
    if not ref:
        return 0
    # A background index run must never export a local-only project's contents.
    # No project filtering on a subsequent retrieval can undo an earlier leak.
    from . import llm
    where = "" if llm.is_local(ref) else (
        "AND (m.project_id IS NULL OR NOT EXISTS ("
        "SELECT 1 FROM projects p WHERE p.id = m.project_id AND p.local_only = 1)) ")
    rows = db.all("SELECT m.id, m.content FROM memories m LEFT JOIN memory_vectors v ON v.memory_id = m.id AND v.model = ? "
                  "WHERE v.memory_id IS NULL " + where + "LIMIT ?", ref, batch)
    if not rows:
        return 0
    vecs = embed([r["content"] for r in rows], ref)
    for r, v in zip(rows, vecs):
        db.run("INSERT OR REPLACE INTO memory_vectors(memory_id, model, vec) VALUES (?, ?, ?)", r["id"], ref, _pack(v))
    return len(rows)


def semantic(query: str, limit: int, project_id: int | None = None) -> list[int]:
    ref = embed_model()
    if not ref or not query.strip() or not _embedding_allowed(ref, project_id):
        return []  # keyword/FTS retrieval remains available without cloud disclosure
    try:
        index_missing()
        q = embed([query], ref)[0]
    except Exception as e:  # noqa: BLE001 - meaning search is optional: keyword search still answers
        log.warning("memory embeddings unavailable: %s", e)
        db.set_setting("memory_embed_error", str(getattr(e, "detail", e))[:300])
        return []
    db.set_setting("memory_embed_error", "")
    qn = math.sqrt(sum(x * x for x in q)) or 1.0
    scored = []
    where, args = scope_sql(project_id, "m")
    for r in db.all(f"SELECT v.memory_id, v.vec FROM memory_vectors v JOIN memories m ON m.id = v.memory_id WHERE v.model = ? AND {where}", ref, *args):
        v = _unpack(r["vec"])
        if len(v) != len(q):
            continue
        dot = sum(a * b for a, b in zip(q, v))
        scored.append((dot / (qn * (math.sqrt(sum(x * x for x in v)) or 1.0)), r["memory_id"]))
    scored.sort(reverse=True)
    return [mid for s, mid in scored[:limit] if s > 0.2]


def status() -> dict:
    ref = embed_model()
    total = db.one("SELECT COUNT(*) AS n FROM memories")["n"]
    indexed = db.one("SELECT COUNT(*) AS n FROM memory_vectors WHERE model = ?", ref)["n"] if ref else 0
    return {"embed_model": ref, "memories": total, "indexed": indexed, "error": db.get_setting("memory_embed_error", "") or ""}


def default_confidence(source: str) -> float:
    return {"user": 1.0, "agent": 0.8, "import": 0.7, "auto": 0.6}.get(source, 0.7)

