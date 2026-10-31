"""feat/memory-core live wiring: chat context, indexing as things happen, auto-filing, sensitive memories."""
import pytest

from conftest import CALLS, events


@pytest.fixture(autouse=True)
def quiet_learning(monkeypatch):
    from app.adapt import knowledge

    async def nothing(*a, **k):
        return []

    monkeypatch.setattr(knowledge, "model_json", nothing)


@pytest.fixture
def kn(client):
    from app import db, knowledge

    knowledge.ensure()
    for t in ("kn_conflicts", "kn_filings", "kn_memories", "kn_edges", "kn_aliases", "kn_entities", "kn_vectors", "kn_docs", "kn_fts"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings WHERE key LIKE 'kn_cursor_%' OR key LIKE 'kn_conv_%'")
    yield knowledge


def test_context_reaches_the_model_and_sensitive_is_held_back(kn, client):
    kn.remember("Smith St invoices go to accounts@smith.com", "procedural", source_kind="user")
    kn.remember("Smith St gate code is kept with the site supervisor", "project", privacy="sensitive", source_kind="user")
    CALLS.clear()
    events(client.post("/api/assistant/chat", json={"message": "Where do Smith St invoices go?"}))
    system = next(c for c in CALLS if c["messages"][0]["role"] == "system")["messages"][0]["content"]
    assert "accounts@smith.com" in system
    assert "gate code" not in system


def test_chat_indexed_and_searchable(kn, client):
    ev = events(client.post("/api/assistant/chat", json={"message": "Remind me the Toowong switchboard needs a new RCD"}))
    hits = client.get("/api/knowledge/search", params={"q": "Toowong switchboard RCD"}).json()
    assert any(h["kind"] == "message" for h in hits)
    assert kn.find_entity
    cid = ev[0]["conversation_id"]
    from app import db

    assert db.one("SELECT 1 AS x FROM kn_entities WHERE ref_table = 'conversations' AND ref_id = ?", str(cid))


def test_sync_indexes_and_autofiles_then_change(kn, client):
    from app import db
    from app.knowledge import live

    proj = kn.upsert_entity("project", "Smith St reno", {"status": "active", "keywords": "SS-42"})
    other = kn.upsert_entity("project", "Toowong job", {"status": "active"})
    now = db.now()
    tid = db.run("INSERT INTO tasks(title, notes, created_at, updated_at) VALUES ('Order cable for SS-42', '', ?, ?)", now, now)
    nid = db.run("INSERT INTO docs(title, body, created_at, updated_at) VALUES ('Site notes', 'Smith St reno: wall chased', ?, ?)", now, now)
    out = live.sync_once()
    assert out["tasks"] >= 1 and out["notes"] >= 1
    f = live.filing_for("task", str(tid))
    assert f["project_name"] == "Smith St reno" and f["chip"].endswith("change?")
    assert live.filing_for("note", str(nid))["project_id"] == proj["id"]
    task_ent = kn.find_entity("Order cable for SS-42", "task")
    assert any(n["id"] == proj["id"] for n in kn.neighbours(task_ent["id"]))
    changed = client.post(f"/api/knowledge/filings/{f['id']}/change", json={"project_id": other["id"]}).json()
    assert changed["project_name"] == "Toowong job"
    assert not any(n["id"] == proj["id"] and n["rel"] == "part_of" and n.get("source") == "auto-filing" for n in kn.neighbours(task_ent["id"]))
    # the user's choice sticks on the next sync
    db.run("UPDATE tasks SET notes = 'more SS-42 cable', updated_at = ? WHERE id = ?", "2999-01-01T00:00:00+00:00", tid)
    live.sync_once()
    assert live.filing_for("task", str(tid))["project_name"] == "Toowong job"
    assert client.get("/api/knowledge/search", params={"q": "order cable"}).json()


def test_forgotten_adapt_fact_is_forgotten_in_knowledge(kn, client):
    from app.adapt import knowledge as adapt
    from app.knowledge import live

    f = adapt.add("The user is a structural engineer", "about", "user")
    live.sync_once()
    assert any("structural engineer" in m["content"] for m in kn.list_memories())
    adapt.delete(f["id"])
    live.sync_once()
    assert not any("structural engineer" in m["content"] for m in kn.list_memories())
