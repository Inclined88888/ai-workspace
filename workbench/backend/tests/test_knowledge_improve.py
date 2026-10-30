"""feat/memory-core nightly self-improvement: reviews the day, applies safe changes, proposes the rest."""
import uuid

import pytest


@pytest.fixture
def kn(client):
    from app import db, knowledge

    knowledge.ensure()
    for t in ("kn_improvements", "kn_conflicts", "kn_memories", "kn_docs", "kn_fts"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM settings WHERE key LIKE 'kn_improve%' OR key = 'kn_ask_before'")
    from app.knowledge import improve

    db.set_setting("kn_improve_since", improve._ago(seconds=2))  # only review what this test does
    yield knowledge


def _action(tool, outcome, reason=""):
    from app import db

    db.run("INSERT INTO actions(ts, kind, channel, tool, outcome, failure_reason, request_id) VALUES (?, 'tool_call', 'web', ?, ?, ?, ?)",
           db.now(), tool, outcome, reason, uuid.uuid4().hex)


def test_nightly_pass_applies_safe_changes_and_proposes_the_rest(kn, client):
    from app import db
    from app.assistant import Ctx, TOOLS
    from app.knowledge import improve

    m = kn.remember("Quotes go out as one-page PDFs", "procedural")["memory"]
    db.run("UPDATE kn_memories SET last_used_at = ? WHERE id = ?", db.now(), m["id"])
    _action("send_invoice", "denied")
    _action("send_invoice", "denied")
    _action("web_fetch", "failed", "timeout")
    res = improve.run_once(force=True)
    assert res["ran"] and res["proposed"] == 1 and res["boosted"] == 1
    assert "Nothing changes until you say yes" in res["note"]
    assert db.one("SELECT weight FROM kn_memories WHERE id = ?", m["id"])["weight"] > 1.0
    assert any("web_fetch" in x["content"] for x in kn.list_memories(tier="procedural"))
    home = client.get("/api/knowledge/improvements").json()
    assert home["note"]["detail"] == res["note"] and len(home["proposals"]) == 1
    # nothing about how it acts changes until approved
    assert improve.ask_before() == []
    pid = home["proposals"][0]["id"]
    assert client.post(f"/api/knowledge/improvements/{pid}/approve").json()["status"] == "approved"
    assert improve.ask_before() == ["send_invoice"]
    assert improve.hold_reason("send_invoice")
    # same proposal is never repeated
    _action("send_invoice", "denied")
    _action("send_invoice", "denied")
    assert improve.run_once(force=True)["proposed"] == 0
    assert TOOLS and Ctx


def test_toggle_and_idle(kn, client):
    from app.knowledge import improve

    client.put("/api/knowledge/improvements/settings", json={"enabled": False})
    assert not improve.due() and improve.run_once() == {"ran": False}
    client.put("/api/knowledge/improvements/settings", json={"enabled": True})
    _action("web_fetch", "ok")
    assert not improve.idle()  # you were just using it
    assert improve.idle(minutes=0)  # nothing since "now"
