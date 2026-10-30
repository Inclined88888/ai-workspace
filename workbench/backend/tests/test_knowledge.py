"""feat/memory-core: schema migrations, entity graph, memory tiers, contradictions, recall benchmarks, API."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def kn(client):
    from app import db, knowledge

    knowledge.ensure()
    for t in ("kn_conflicts", "kn_filings", "kn_memories", "kn_edges", "kn_aliases", "kn_entities", "kn_vectors", "kn_docs", "kn_fts"):
        db.run(f"DELETE FROM {t}")
    knowledge.set_embedder(None)
    yield knowledge
    knowledge.set_embedder(None)


def test_migrations_recorded_and_integrity(kn):
    st = kn.schema.integrity()
    assert st["ok"], st
    assert st["versions"] == [1, 2, 3]


def test_migrate_v01_shaped_db(tmp_path, monkeypatch):
    """A database as v0.1 left it (core tables, lazily-made projects/adapt tables, no kn_*) upgrades with a backup."""
    from app import config, db, knowledge
    from app.adapt import store as adapt_store
    from app.knowledge import crypto, schema
    from app import projects

    path = tmp_path / "workbench.db"
    c = sqlite3.connect(path)
    c.executescript(db.SCHEMA)
    c.executescript(projects.SCHEMA)
    c.executescript(adapt_store.SCHEMA)
    c.execute("PRAGMA user_version = 2")
    now = "2026-01-01T00:00:00+00:00"
    c.execute("INSERT INTO projects(name, kind, created_at, updated_at) VALUES ('Smith St reno', 'job', ?, ?)", (now, now))
    c.execute("INSERT INTO project_items(project_id, kind, title, created_at) VALUES (1, 'person', 'Sam Lee', ?)", (now,))
    c.execute("INSERT INTO adapt_facts(content_enc, category, fingerprint, created_at, updated_at) VALUES (?, 'about', 'x', ?, ?)",
              (adapt_store.seal("The user is an electrician in Brisbane"), now, now))
    c.execute("INSERT INTO tasks(title, project, created_at, updated_at) VALUES ('Order cable', 'Smith St reno', ?, ?)", (now, now))
    c.commit()
    c.close()
    monkeypatch.setattr(config, "DB_PATH", path)
    db._local.conn = None
    schema.reset_ready()
    crypto.reset_cache()
    try:
        db.init()
        knowledge.ensure()
        assert schema.integrity()["ok"]
        assert list(tmp_path.glob("workbench.db.pre-kn*.bak")), "backup taken before migrating"
        proj = knowledge.find_entity("Smith St reno", "project")
        assert proj
        assert any(n["name"] == "Sam Lee" and n["rel"] == "member_of" for n in knowledge.neighbours(proj["id"]))
        assert any(n["type"] == "task" for n in knowledge.neighbours(proj["id"]))
        assert any("electrician" in m["content"] for m in knowledge.list_memories())
        hit = knowledge.recall("where does the electrician work")
        assert hit and "electrician" in hit[0]["content"]
        # re-running is a no-op
        assert schema.migrate() == []
        # nothing readable leaks into the file
        dump = "\n".join(sqlite3.connect(path).iterdump())
        assert "electrician" not in dump
    finally:
        db._local.conn.close()
        db._local.conn = None
        monkeypatch.undo()
        schema.reset_ready()
        crypto.reset_cache()


def test_graph_dedupe_aliases_merge(kn):
    a = kn.upsert_entity("person", "Sam Lee", {"email": "sam@lee.com"})
    b = kn.upsert_entity("person", "sam lee", {"phone": "0400 000 000"})
    assert a["id"] == b["id"] and b["attrs"]["phone"]
    assert kn.find_entity("sam@lee.com")["id"] == a["id"]
    org = kn.upsert_entity("organisation", "Bright Sparks")
    kn.link(a["id"], "works_at", org["id"])
    assert kn.neighbours(org["id"])[0]["rel"] == "works_at"
    dup = kn.upsert_entity("organisation", "Bright Sparks Electrical")
    assert kn.duplicates()
    kn.merge_entities(org["id"], dup["id"])
    assert kn.find_entity("Bright Sparks Electrical")["id"] == org["id"]


def test_memory_dedupe_supersede_conflict_why_forget(kn):
    sam = kn.upsert_entity("person", "Sam Lee")
    m1 = kn.remember("Sam lives in Paddington", "profile", subject_id=sam["id"], source_kind="chat")
    assert kn.remember("Sam lives in Paddington.", "profile", subject_id=sam["id"], source_kind="chat")["outcome"] == "duplicate"
    m2 = kn.remember("Sam moved to Toowong", "profile", subject_id=sam["id"], source_kind="chat")
    assert m2["outcome"] == "superseded"
    assert kn.get_memory(m1["memory"]["id"])["status"] == "superseded"
    w = kn.why(m2["memory"]["id"])
    assert w["replaced"][0]["content"] == "Sam lives in Paddington" and "From a chat" in w["summary"]
    # about the user: the user decides
    u1 = kn.remember("I live in Brisbane", "profile", source_kind="user")
    u2 = kn.remember("I live in Sydney", "profile", source_kind="user")
    assert u2["outcome"] == "conflict"
    card = kn.conflicts()[0]
    kn.resolve(card["id"], "new")
    assert kn.get_memory(u1["memory"]["id"])["status"] == "superseded"
    assert kn.get_memory(u2["memory"]["id"])["status"] == "active"
    kn.forget(u2["memory"]["id"])
    assert not kn.recall("Sydney")
    with pytest.raises(Exception):
        kn.remember("my password is hunter2", "profile", source_kind="chat")


def test_scopes(kn):
    a = kn.scoped("assistant:1")
    b = kn.scoped("assistant:2")
    a.add("Invoices go out on Fridays")
    kn.remember("The business is called Bright Sparks", "profile")
    assert a.recall("invoices fridays") and not b.recall("invoices fridays")
    assert b.recall("business called bright sparks")


def test_consolidation(kn):
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    p = kn.upsert_entity("project", "Smith St reno")
    for t in ("Called Sam about the switchboard", "Switchboard quote sent to Sam", "Sam approved the switchboard quote"):
        kn.remember(t, "episodic", project_id=p["id"], observed_at=old, source_kind="chat")
    r = kn.consolidate(14)
    assert r["summaries"] == 1
    assert kn.list_memories(tier="project", project_id=p["id"])
    assert len(kn.list_memories(tier="episodic", status="archived")) == 3


def test_context_budget(kn):
    kn.remember("Prefers short replies", "procedural", pinned=True)
    for w in ("conduit", "switchboard", "downlights", "smoke alarms", "RCD testing", "meter box", "solar inverter",
              "ceiling fans", "data points", "outdoor lights", "oven circuit", "hot water"):
        kn.remember(f"Smith St cable run for the {w} needs checking", "project")
    ctx = kn.context_for("cable for Smith St", budget_tokens=120)
    assert ctx["tokens"] <= 120 and "Prefers short replies" in ctx["block"] and len(ctx["ids"]) > 2


SYN = [
    ("Sam Lee is the site supervisor at Smith St", "who supervises Smith St"),
    ("The client Priya wants the kitchen tiles in matte white", "Priya kitchen tiles colour"),
    ("Invoices are due 14 days after the job is finished", "when are invoices due"),
    ("The lab samples are stored in freezer B at minus 80", "where are the lab samples stored"),
    ("Contract clause 12 covers termination for convenience", "termination clause in the contract"),
    ("Exam for linear algebra is on 3 December", "linear algebra exam date"),
    ("The studio mix should be delivered as 24-bit WAV", "studio mix delivery format"),
    ("Dayforce timesheets must be submitted by Sunday night", "timesheets deadline dayforce"),
    ("Ana from Bright Sparks handles the purchase orders", "who handles purchase orders"),
    ("Toowong job uses 6mm twin and earth cable", "cable for Toowong job"),
]
NOISE = ["Coffee order is a flat white", "The dog is called Biscuit", "Car rego renews in March", "Likes the Neon theme",
         "Gym on Tuesdays and Thursdays", "Favourite font is Inter", "Brother lives in Perth", "Uses a MacBook Air",
         "The office wifi is slow on Mondays", "Prefers meetings after 10am"] * 3


def _bench(kn):
    rel = []
    for fact, _ in SYN:
        rel.append(kn.remember(fact, "profile")["memory"]["id"])
    for i, n in enumerate(NOISE):
        kn.remember(f"{n} ({i})", "episodic", source_kind="chat")
    return [{"query": q, "relevant": {rel[i]}} for i, (_, q) in enumerate(SYN)]


def test_benchmark_fts_only(kn, capsys):
    cases = _bench(kn)
    r1 = kn._recall.recall_at_k(cases, k=1, use_vectors=False)
    r5 = kn._recall.recall_at_k(cases, k=5, use_vectors=False)
    p1 = kn._recall.precision_at_k(cases, k=1, use_vectors=False)
    print(f"\nBENCH fts-only: precision@1={p1:.2f} recall@1={r1:.2f} recall@5={r5:.2f}")
    assert p1 >= 0.7 and r5 >= 0.9


def test_benchmark_hybrid_hash(kn):
    kn.set_embedder(kn.index.HashEmbedder())
    cases = _bench(kn)
    p1 = kn._recall.precision_at_k(cases, k=1)
    r5 = kn._recall.recall_at_k(cases, k=5)
    print(f"\nBENCH hybrid(hash-256): precision@1={p1:.2f} recall@5={r5:.2f}")
    assert p1 >= 0.7 and r5 >= 0.9


def test_api(kn, client):
    r = client.post("/api/knowledge/memories", json={"content": "Quotes always include GST", "tier": "procedural"})
    assert r.status_code == 200, r.text
    mid = r.json()["memory"]["id"]
    assert client.get(f"/api/knowledge/memories/{mid}/why").json()["source"] == "You told me"
    assert client.get("/api/knowledge/search", params={"q": "GST quotes"}).json()[0]["kind"] == "memory"
    e = client.post("/api/knowledge/entities", json={"type": "person", "name": "Priya Shah"}).json()
    assert client.get(f"/api/knowledge/entities/{e['id']}").json()["name"] == "Priya Shah"
    ex = client.get("/api/knowledge/export").json()
    assert ex["format"] == "ai-workspace-knowledge" and ex["memories"]
    assert client.get("/api/knowledge/status").json()["integrity"]["ok"]
    assert client.delete(f"/api/knowledge/memories/{mid}").json() == {"forgotten": mid}
