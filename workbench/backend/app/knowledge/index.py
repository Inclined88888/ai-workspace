"""One search index over everything, BM25 (SQLite FTS5) on keyed tokens, plus an optional vector plug point."""
from __future__ import annotations

import math
import struct
import zlib

from .. import db
from . import crypto, schema
from .text import words


def tokens(text: str) -> str:
    return " ".join(crypto.blind(w) for w in words(text))


def put(kind: str, ref: str, text: str, title: str = "", scope: str = "shared", privacy: str = "normal",
        project_id: int | None = None, at: str | None = None) -> int:
    schema.ensure()
    c = db.conn()
    row = c.execute("SELECT id FROM kn_docs WHERE kind = ? AND ref = ?", (kind, str(ref))).fetchone()
    if row:
        did = row[0]
        c.execute("UPDATE kn_docs SET title = ?, scope = ?, privacy = ?, project_id = ?, at = ? WHERE id = ?",
                  (title[:200], scope, privacy, project_id, at or db.now(), did))
        c.execute("DELETE FROM kn_fts WHERE rowid = ?", (did,))
    else:
        did = c.execute("INSERT INTO kn_docs(kind, ref, title, scope, privacy, project_id, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (kind, str(ref), title[:200], scope, privacy, project_id, at or db.now())).lastrowid
    c.execute("INSERT INTO kn_fts(rowid, tokens) VALUES (?, ?)", (did, tokens(f"{title} {text}")))
    emb = embedder()
    if emb:
        c.execute("INSERT OR REPLACE INTO kn_vectors(doc_id, model, vec) VALUES (?, ?, ?)",
                  (did, emb.name, struct.pack(f"{emb.dim}f", *emb.embed(f"{title} {text}"))))
    return did


def drop(kind: str, ref: str) -> None:
    schema.ensure()
    c = db.conn()
    row = c.execute("SELECT id FROM kn_docs WHERE kind = ? AND ref = ?", (kind, str(ref))).fetchone()
    if row:
        c.execute("DELETE FROM kn_fts WHERE rowid = ?", (row[0],))
        c.execute("DELETE FROM kn_docs WHERE id = ?", (row[0],))


def bm25(query: str, limit: int = 50, kinds: tuple[str, ...] | None = None) -> list[tuple[int, float]]:
    schema.ensure()
    q = sorted(set(words(query)))
    if not q:
        return []
    match = " OR ".join(crypto.blind(w) for w in q)
    sql = "SELECT d.id, -bm25(kn_fts) AS s FROM kn_fts JOIN kn_docs d ON d.id = kn_fts.rowid WHERE kn_fts MATCH ?"
    args: list = [match]
    if kinds:
        sql += f" AND d.kind IN ({','.join('?' * len(kinds))})"
        args += list(kinds)
    sql += " ORDER BY bm25(kn_fts) LIMIT ?"
    return [(r[0], r[1]) for r in db.conn().execute(sql, (*args, limit))]


# --- vectors: plug point ---------------------------------------------------------------------------------------------
class HashEmbedder:
    """Built-in, offline, dependency-free: hashed word + character 3-gram features (catches plurals, typos, word parts).
    Not semantic. Replace with a real model via set_embedder(), e.g. fastembed's BAAI/bge-small-en-v1.5."""
    name, dim = "hash-256", 256

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in words(text):
            v[zlib.crc32(w.encode()) % self.dim] += 1.0
            p = f"#{w}#"
            for i in range(len(p) - 2):
                v[zlib.crc32(p[i:i + 3].encode()) % self.dim] += 0.3
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


_embedder: list = [None]


def set_embedder(e) -> None:
    _embedder[0] = e


def embedder():
    return _embedder[0]


def vector_search(query: str, limit: int = 50) -> list[tuple[int, float]]:
    emb = embedder()
    if not emb:
        return []
    qv = emb.embed(query)
    out = []
    for did, blob in db.conn().execute("SELECT doc_id, vec FROM kn_vectors WHERE model = ?", (emb.name,)):
        v = struct.unpack(f"{emb.dim}f", blob)
        out.append((did, sum(a * b for a, b in zip(qv, v))))
    out.sort(key=lambda x: -x[1])
    return [x for x in out[:limit] if x[1] > 0.15]
