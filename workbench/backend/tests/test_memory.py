"""Hybrid memory retrieval: benchmark against the keyword baseline, corrections, revert, dedup, provenance, embeddings."""

import json
from pathlib import Path

import pytest

DATA = Path(__file__).parent / "data"


def score(fn, data: dict) -> tuple[float, dict[str, int]]:
    from app import db

    db.run("DELETE FROM memories")
    ids = {k: db.run("INSERT INTO memories(content, kind, source, created_at, updated_at) VALUES (?, ?, 'user', ?, ?)",
                     c, kind, db.now(), db.now()) for k, (c, kind) in data["memories"].items()}
    back = {v: k for k, v in ids.items()}
    hits = 0
    for item in data["queries"]:
        got = [back.get(m["id"]) for m in fn(item["q"], 5)]
        hits += any(w in got for w in item["want"])
    return hits / len(data["queries"]), ids


@pytest.mark.parametrize("name", ["memory_eval.json", "memory_eval_heldout.json"])
def test_hybrid_beats_keyword_baseline(client, name):
    from app import assistant, memory

    data = json.loads((DATA / name).read_text())
    base, _ = score(assistant.search_memories_keyword, data)
    hybrid, _ = score(memory.search, data)
    # Measured when written: dev 0.62 -> 1.00, held-out 0.44 -> 0.92 (both sets written by the same author as the
    # retriever; see docs/MEMORY.md). The thresholds guard against regressions, not a claim of general accuracy.
    assert hybrid >= base + 0.3 and hybrid >= 0.85, (base, hybrid)


def test_typos_and_stopwords(client):
    from app import db, memory

    db.run("DELETE FROM memories")
    mid = db.run("INSERT INTO memories(content, kind, source, created_at, updated_at) VALUES ('Accountant is Rachel at Clearview Tax', 'fact', 'user', ?, ?)",
                 db.now(), db.now())
    assert memory.search("accountnt", 3)[0]["id"] == mid
    assert memory.search("who is my accountant?", 3)[0]["id"] == mid
    assert memory.search("", 3) == [] and memory.search("the of and", 3) == []
    assert memory.search('") OR 1=1 --', 3) == []  # query text never reaches SQL or FTS syntax unescaped


def test_near_duplicates_are_folded(client):
    from app import db, memory

    db.run("DELETE FROM memories")
    for c in ("Prefers tea with milk in the morning", "Prefers tea with milk in the morning.", "Likes green tea at night"):
        db.run("INSERT INTO memories(content, kind, source, created_at, updated_at) VALUES (?, 'preference', 'auto', ?, ?)", c, db.now(), db.now())
    got = [m["content"] for m in memory.search("tea", 5)]
    assert len(got) == 2 and "Likes green tea at night" in got


def test_correction_revert_and_provenance(client):
    m = client.post("/api/memories", json={"content": "Lives in Sydney", "kind": "fact"}).json()
    assert m["source"] == "user" and m["confidence"] == 1.0
    c = client.put(f"/api/memories/{m['id']}", json={"content": "Lives in Brisbane", "kind": "fact"}).json()
    assert c["content"] == "Lives in Brisbane" and c["previous"] == "Lives in Sydney"
    r = client.post(f"/api/memories/{m['id']}/revert").json()
    assert r["content"] == "Lives in Sydney" and r["previous"] == "Lives in Brisbane"
    hit = client.get("/api/memories", params={"q": "home city"}).json()[0]
    assert hit["id"] == m["id"] and hit["matched_by"] and "confidence" in hit
    assert client.delete(f"/api/memories/{m['id']}").json() == {"ok": True}
    assert client.post(f"/api/memories/{m['id']}/revert").status_code == 404
    assert any(a["action"] == "memory corrected" for a in client.get("/api/audit").json())


def test_learned_memories_have_lower_confidence_than_user_ones(client):
    from app import db, memory

    db.run("DELETE FROM memories")
    db.run("INSERT INTO memories(content, kind, source, confidence, created_at, updated_at) VALUES ('Favourite colour is blue', 'fact', 'auto', 0.6, ?, ?)", db.now(), db.now())
    db.run("INSERT INTO memories(content, kind, source, confidence, created_at, updated_at) VALUES ('Favourite colour is green', 'fact', 'user', 1.0, ?, ?)", db.now(), db.now())
    assert memory.search("favourite colour", 2)[0]["content"] == "Favourite colour is green"


def test_optional_embeddings(client):
    from app import db, memory

    db.run("DELETE FROM memories")
    db.run("INSERT INTO memories(content, kind, source, created_at, updated_at) VALUES ('Allergic to penicillin', 'fact', 'user', ?, ?)", db.now(), db.now())
    assert client.put("/api/memory/embeddings", json={"model": "999/nothing"}).status_code == 400
    st = client.put("/api/memory/embeddings", json={"model": f"{client.pid}/fake-embed"}).json()
    assert st["embed_model"].endswith("/fake-embed")
    hits = memory.search("penicillin", 3)
    assert hits and "meaning" in hits[0]["matched_by"]
    assert client.get("/api/memory/status").json()["indexed"] == 1
    # deleting a memory removes its vector too
    db.run("DELETE FROM memories")
    assert db.one("SELECT COUNT(*) AS n FROM memory_vectors")["n"] == 0
    client.put("/api/memory/embeddings", json={"model": ""})
    assert memory.semantic("anything", 5) == []
