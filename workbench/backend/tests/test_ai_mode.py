"""AI modes: Free Cloud AI is the first-run default and allows only models verified as free, everywhere a model is
called; existing installs keep working exactly as before until a mode is chosen; downloads need consent."""

import asyncio
import json

import httpx
import pytest
from fastapi import HTTPException

from conftest import CALLS

ASK = [{"role": "user", "content": "hi"}]


@pytest.fixture
def mode(client):
    """Set an AI mode for one test; afterwards back to Custom AI (what the suite runs in) with nothing confirmed."""
    from app import db

    def set_(m: str, **kw):
        r = client.put("/api/ai-mode", json={"mode": m, **kw})
        assert r.status_code == 200, r.text
        return r.json()
    yield set_
    db.set_setting("ai_mode", "custom")
    db.set_setting(f"provider_free_confirmed:{client.pid}", "0")
    for k in [r["key"] for r in db.all("SELECT key FROM settings WHERE key LIKE 'free_blocked:%'")]:
        db.run("DELETE FROM settings WHERE key = ?", k)
    db.set_setting("monthly_limit_cents", "0")
    db.set_setting("fallback_models", "")


def test_a_new_workspace_starts_in_free_cloud_ai(client):
    from app import db

    # The suite's setup switched from the first-run default to Custom AI; the activity log shows what it was
    row = db.one("SELECT * FROM audit_log WHERE action = 'AI mode' ORDER BY id LIMIT 1")
    assert row and "was free" in json.dumps(dict(row))


def test_an_existing_install_is_not_changed_until_you_choose(client, mode):
    """Upgrading from before v11.1: no mode is set, so the saved default model and provider keep working as they did."""
    from app import db, llm

    db.run("DELETE FROM settings WHERE key = 'ai_mode'")
    saved = db.get_setting("default_model")
    assert llm.ai_mode() == "" and client.get("/api/ai-mode").json()["mode"] == ""
    st = client.get("/api/models/status").json()
    assert st["default"]["ref"] == saved and st["default"]["source"] == "chosen" and not st["saved_default_blocked"]
    n = len(CALLS)
    assert asyncio.run(llm.chat(None, ASK))["ref"] == saved and len(CALLS) == n + 1
    assert db.get_setting("default_model") == saved  # nothing was rewritten


def test_free_mode_refuses_unverified_models_on_every_path(client, mode):
    from app import checkins, db, llm, memory, missions, research, voice, workers  # noqa: F401

    mode("free")
    ref = f"{client.pid}/fake-model"  # a local server you have not confirmed: not verified as free
    n = len(CALLS)
    # chat with an explicit model
    with pytest.raises(HTTPException) as e:
        asyncio.run(llm.chat(ref, ASK))
    assert "Free Cloud AI" in str(e.value.detail)
    # the default (check-ins, learning, voice summaries call with no model)
    with pytest.raises(HTTPException) as e:
        asyncio.run(llm.chat(None, ASK))
    assert "Free Cloud AI" in str(e.value.detail)
    # streaming (notes, documents, compare)
    async def drain():
        return [c async for c in llm.stream(ref, ASK)]
    out = asyncio.run(drain())
    assert out and "Free Cloud AI" in out[0]["error"]
    # research and worker checks
    with pytest.raises(HTTPException):
        asyncio.run(research._llm(ref, 0, "system", "user"))
    verdict = asyncio.run(workers._check({"goal": "g", "acceptance": "", "expected": "", "conversation_id": None},
                                          {"project_id": None, "id": 0}, "reply", [], ref))
    assert verdict["verdict"] == "cannot_tell" and "Free Cloud AI" in verdict["missing"]
    # vision and connection checks go through the same single call
    with pytest.raises(HTTPException):
        asyncio.run(llm._chat_once(ref, ASK, None, ping=True))
    # embeddings and speech providers
    with pytest.raises(httpx.HTTPError):
        memory.embed(["x"], ref)
    assert "Free Cloud AI" in llm.mode_allows(ref)
    # the assistant chat endpoint
    r = client.post("/api/assistant/chat", json={"message": "hello", "model": ref})
    assert "Free Cloud AI" in r.text
    assert len(CALLS) == n, "no request may reach a model that is not verified as free"


def test_free_mode_mission_planning_does_not_call_a_paid_model(client, mode):
    from app import db, missions

    mode("free")
    mid = client.post("/api/missions", json={"goal": "Plan the offsite"}).json()["id"]
    n = len(CALLS)
    asyncio.run(missions.plan(mid))
    assert len(CALLS) == n
    ev = " ".join(r["text"] for r in db.all("SELECT text FROM mission_events WHERE mission_id = ?", mid))
    assert "Free Cloud AI" in ev


def test_confirming_a_local_server_cannot_bill_makes_it_usable(client, mode):
    from app import llm

    mode("free")
    r = client.put(f"/api/providers/{client.pid}/free-confirm", json={"confirmed": True})
    assert r.status_code == 200
    n = len(CALLS)
    res = asyncio.run(llm.chat(f"{client.pid}/fake-model", ASK))
    assert res["message"]["content"] and len(CALLS) == n + 1
    p = next(x for x in r.json()["status"]["providers"] if x["id"] == client.pid)
    assert p["free_confirmed"] and p["can_confirm_free"]


def test_a_paid_provider_cannot_be_confirmed_free(client, mode):
    from app import db

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenAI', 'openai', 'https://api.openai.com/v1', '', 'gpt-6', 0, ?)", db.now())
    try:
        assert client.put(f"/api/providers/{pid}/free-confirm", json={"confirmed": True}).status_code in (400, 404)
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_free_mode_never_falls_back_to_a_paid_model(client, mode):
    from app import db, llm

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('Paid', 'openai', 'https://paid.example.invalid/v1', '', 'big-model', 1, ?)", db.now())
    try:
        mode("free")
        client.put(f"/api/providers/{client.pid}/free-confirm", json={"confirmed": True})
        primary = f"{client.pid}/fake-model"
        db.set_setting("fallback_models", json.dumps([f"{pid}/big-model"]))  # a backup you once chose
        assert llm.fallback_refs(primary) == []
        db.set_setting("ai_mode", "premium")
        assert llm.fallback_refs(primary) == [f"{pid}/big-model"]  # your choice is kept for when you leave Free mode
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_a_model_that_reports_a_charge_is_blocked_in_free_mode(client, mode, monkeypatch):
    from app import db, llm

    mode("free")
    client.put(f"/api/providers/{client.pid}/free-confirm", json={"confirmed": True})
    ref = f"{client.pid}/fake-model"

    async def charged(p, body):
        return {"choices": [{"message": {"role": "assistant", "content": "hi"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.002}}
    monkeypatch.setattr(llm, "_call", charged)
    asyncio.run(llm.chat(ref, ASK))
    assert db.get_setting(f"free_blocked:{ref}") == "1"
    with pytest.raises(HTTPException) as e:
        asyncio.run(llm.chat(ref, ASK))
    assert "reported a charge" in str(e.value.detail)


def test_free_mode_rechecks_openrouter_prices_and_refuses_a_model_that_stopped_being_free(client, mode, monkeypatch):
    from app import db, llm

    mode("free")
    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenRouter', 'openai', 'https://openrouter.ai/api/v1', '', '', 1, ?)", db.now())
    try:
        ref = f"{pid}/vendor/model:free"
        db.set_setting(f"provider_prices:{pid}", json.dumps({"zero": ["vendor/model:free"], "at": 0}))  # old price list
        refreshed = []

        def relist(p_id, fresh=False):  # OpenRouter now lists a price for it
            refreshed.append(p_id)
            db.set_setting(f"provider_prices:{p_id}", json.dumps({"zero": [], "at": __import__("time").time()}))
            return []
        monkeypatch.setattr(llm, "list_models_for", relist)
        with pytest.raises(HTTPException) as e:
            asyncio.run(llm._chat_once(ref, ASK, None))
        assert refreshed == [pid] and "could not be confirmed as zero" in str(e.value.detail)
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_free_mode_never_sends_a_paid_connection_check(client, mode):
    mode("free")
    n = len(CALLS)
    r = client.post(f"/api/providers/{client.pid}/check?allow_paid=true").json()
    assert r["ok"] is False and len(CALLS) == n
    # the suite's model is a local server you have not confirmed, so ROOKS asks you to confirm it instead of sending
    assert "Free Cloud AI" in r["error"] and "Confirm it below" in r["error"]


def test_local_mode_refuses_cloud_models(client, mode):
    from app import db, llm

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('Cloud', 'openai', 'https://cloud.example.invalid/v1', '', 'm', 1, ?)", db.now())
    try:
        mode("local")
        with pytest.raises(HTTPException) as e:
            asyncio.run(llm.chat(f"{pid}/m", ASK))
        assert "Local AI" in str(e.value.detail)
        assert llm.mode_allows(f"{client.pid}/fake-model") == ""  # the local server is fine
        assert "Local AI" in llm.mode_allows("1/gpt-oss:120b-cloud") or not db.one("SELECT 1 FROM providers WHERE id = 1 AND kind = 'ollama'")
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_premium_needs_a_spending_limit(client, mode):
    assert client.put("/api/ai-mode", json={"mode": "premium"}).status_code == 400
    out = mode("premium", monthly_limit_cents=1000)
    assert out["mode"] == "premium" and out["monthly_limit_cents"] == 1000


def test_switching_modes_keeps_providers_and_your_default(client, mode):
    from app import db

    saved = db.get_setting("default_model")
    before = [p["id"] for p in db.all("SELECT id FROM providers")]
    for m in ("free", "local", "custom"):
        mode(m)
    assert db.get_setting("default_model") == saved
    assert [p["id"] for p in db.all("SELECT id FROM providers")] == before


def test_a_local_download_needs_consent(client, monkeypatch):
    from app import api, config

    monkeypatch.setattr(config, "OLLAMA_URL", "http://127.0.0.1:9")
    r = client.post("/api/ollama/pull", json={"model": "qwen3:4b"})
    assert r.status_code == 400 and "agreement" in r.json()["detail"]
    cat = client.get("/api/local/catalog").json()["models"]
    assert all(m["size_gb"] and m["memory_gb"] and m["licence"] for m in cat)
    assert any(m["model"] == "qwen3:4b" for m in api.LOCAL_CATALOG)


def test_capabilities_say_chat_is_not_ready_when_the_mode_allows_no_model(client, mode):
    mode("free")  # the only model is a local server you have not confirmed
    chat = next(c for c in client.get("/api/capabilities").json()["capabilities"] if c["id"] == "chat")
    assert chat["status"] == "needs_setup" and "Free Cloud AI" in chat["needs"]
    mode("custom")
    chat = next(c for c in client.get("/api/capabilities").json()["capabilities"] if c["id"] == "chat")
    assert chat["status"] == "ready"


def test_the_model_list_says_which_models_the_mode_allows(client, mode):
    mode("free")
    r = client.get("/api/models").json()
    m = next(x for x in r["models"] if x["ref"] == f"{client.pid}/fake-model")
    assert r["mode"] == "free" and m["allowed"] is False and "Free Cloud AI" in m["blocked"]
    mode("custom")
    r = client.get("/api/models").json()
    m = next(x for x in r["models"] if x["ref"] == f"{client.pid}/fake-model")
    assert r["mode"] == "custom" and "allowed" not in m  # nothing is restricted, so nothing is marked
