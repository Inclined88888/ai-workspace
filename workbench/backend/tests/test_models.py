"""Model setup that works on the first try: connect checks the key and picks a model that answered,
defaults survive removed providers, automatic defaults fall back, explicit picks fail visibly with a clear reason."""

import asyncio
import json
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

prov = FastAPI()
MODELS = [  # shaped like OpenRouter's /models
    {"id": "paid-model", "context_length": 200000, "supported_parameters": ["tools"]},
    {"id": "free-model:free", "context_length": 128000, "supported_parameters": ["tools", "temperature"]},
    {"id": "notools:free", "context_length": 900000, "supported_parameters": ["temperature"]},
    {"id": "small:free", "context_length": 8000, "supported_parameters": ["tools"]},
]


def _auth(req: Request) -> bool:
    return req.headers.get("authorization") == "Bearer good"


@prov.get("/v1/models")
def models():
    return {"data": MODELS}


@prov.get("/v1/key")
def key(req: Request):
    if not _auth(req):
        return JSONResponse({"error": {"message": "No auth credentials found", "code": 401}}, 401)
    return {"data": {"is_free_tier": True, "limit_remaining": None}}


PINGS: list[dict] = []
MODE = {"hang": False, "reject_cap": False}


@prov.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    if body.get("tools") and body["tools"][0]["function"]["name"] == "noop":
        PINGS.append(body)
        if MODE["hang"]:
            await asyncio.sleep(5)
        if MODE["reject_cap"] and "max_tokens" in body:
            return JSONResponse({"error": {"message": "Unsupported parameter: max_tokens", "code": 400}}, 400)
    if not _auth(req):
        return JSONResponse({"error": {"message": "No auth credentials found", "code": 401}}, 401)
    m = body["model"]
    if m == "paid-model":
        return JSONResponse({"error": {"message": "This request would exceed your available credits", "code": 402}}, 402)
    if m == "notools:free" and body.get("tools"):
        return JSONResponse({"error": {"message": "No endpoints found that support tool use", "code": 404}}, 404)
    return {"choices": [{"message": {"role": "assistant", "content": f"OK from {m}"}}], "usage": {"prompt_tokens": 5, "completion_tokens": 1}}


@pytest.fixture(scope="module")
def prov_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(prov, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


@pytest.fixture
def router_like(client, prov_url, monkeypatch):
    """Treat the fake provider as OpenRouter and start each test with no saved default or backups."""
    from app import db, llm

    monkeypatch.setattr(llm, "_is_openrouter", lambda p: p["base_url"].startswith(prov_url))
    saved = db.get_setting("default_model")
    db.set_setting("default_model", "")
    db.set_setting("fallback_models", "")
    # the suite's own fake provider is verified too; switch it off so these tests see only the router
    others = [p["id"] for p in db.all("SELECT id FROM providers WHERE enabled = 1")]
    db.run(f"UPDATE providers SET enabled = 0 WHERE id IN ({','.join('?' * len(others))})", *others) if others else None
    yield prov_url
    if others:
        db.run(f"UPDATE providers SET enabled = 1 WHERE id IN ({','.join('?' * len(others))})", *others)
    db.set_setting("default_model", saved or "")
    db.set_setting("fallback_models", "")
    for p in db.all("SELECT id FROM providers WHERE base_url = ?", prov_url):
        client.delete(f"/api/providers/{p['id']}")


def connect(client, url, key):
    return client.post("/api/providers/connect", json={"name": "Router", "base_url": url, "api_key": key}).json()


def test_bad_key_is_explained_and_never_becomes_default(client, router_like):
    r = connect(client, router_like, "wrong")
    assert r["ok"] is False and "rejected the API key" in r["error"]
    assert r["status"]["default"]["ref"] != f"{r['provider_id']}/free-model:free"


def test_good_key_picks_a_free_model_that_answered_with_tools(client, router_like):
    bad = connect(client, router_like, "wrong")
    good = connect(client, router_like, "good")
    assert good["provider_id"] == bad["provider_id"]  # pasting again updates the key, no duplicate
    assert good["ok"] is True and good["free_tier"] is True
    # notools:free has the largest context but cannot use tools; paid-model needs credits
    assert good["recommended"] == "free-model:free"
    assert good["status"]["default"] == {"ref": f"{good['provider_id']}/free-model:free", "source": "auto", "error": ""}


def test_saved_default_for_a_removed_provider_is_ignored(client, router_like):
    from app import db, llm

    good = connect(client, router_like, "good")
    db.set_setting("default_model", "9999/gone-model")
    ref, source = llm.effective_default()
    assert source == "auto" and ref == f"{good['provider_id']}/free-model:free"
    assert client.get("/api/models/status").json()["saved_default_broken"] is True


def test_default_falls_back_and_explicit_pick_fails_visibly(client, router_like):
    from app import db, llm
    from conftest import events

    good = connect(client, router_like, "good")
    pid = good["provider_id"]
    db.set_setting("default_model", f"{pid}/paid-model")
    db.set_setting("fallback_models", json.dumps([f"{pid}/free-model:free"]))
    res = asyncio.run(llm.chat(None, [{"role": "user", "content": "hi"}]))
    assert res["ref"] == f"{pid}/free-model:free" and res["message"]["content"] == "OK from free-model:free"
    assert "no credits" in res["fallback_from"][0]["error"]
    # the assistant tells the user which model answered and why
    ev = events(client.post("/api/assistant/chat", json={"message": "hello"}))
    notice = [e for e in ev if e["type"] == "notice"]
    assert notice and "free-model:free" in notice[0]["text"] and "no credits" in notice[0]["text"]
    # a model the user picked is strict: no silent switch
    with pytest.raises(llm.ModelError) as e:
        asyncio.run(llm.chat(f"{pid}/paid-model", [{"role": "user", "content": "hi"}]))
    assert "no credits" in str(e.value.detail)


def test_model_without_tool_support_is_explained(client, router_like):
    from app import llm

    pid = connect(client, router_like, "good")["provider_id"]
    with pytest.raises(llm.ModelError) as e:
        asyncio.run(llm.chat(f"{pid}/notools:free", [{"role": "user", "content": "hi"}], [llm.PING_TOOL]))
    assert "can't use tools" in str(e.value.detail)


def test_no_provider_message_points_to_settings(client):
    from app import db, llm

    rows = db.all("SELECT id, enabled FROM providers")
    db.run("UPDATE providers SET enabled = 0")
    try:
        with pytest.raises(Exception) as e:
            llm.effective_default()
        assert "Settings" in str(getattr(e.value, "detail", e.value))
    finally:
        for r in rows:
            db.run("UPDATE providers SET enabled = ? WHERE id = ?", r["enabled"], r["id"])


def test_telegram_model_command(client, router_like):
    from app import telegram

    pid = connect(client, router_like, "good")["provider_id"]
    out = telegram.model_command(4242, "/model free-model:free")
    assert "now uses free-model:free" in out
    assert "free-model:free" in telegram.model_command(4242, "/status")
    assert "Back to the default" in telegram.model_command(4242, "/model auto")
    assert "Backups" in telegram.model_command(4242, "/status")
    assert pid
    from app import db

    db.run("DELETE FROM conversations WHERE channel = 'telegram' AND external_id = '4242'")


def test_provider_specific_key_errors_are_recognised(client):
    from app import llm

    gem = {"name": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"}
    # real responses captured from each service with an invalid key
    assert "rejected the API key" in llm.explain(gem, 400, '{"error": {"code": 400, "message": "Please pass a valid API key", "status": "INVALID_ARGUMENT"}}')
    oai = {"name": "OpenAI", "base_url": "https://api.openai.com/v1"}
    assert "rejected the API key" in llm.explain(oai, 401, '{"error": {"code": "invalid_api_key"}}')
    assert "rejected the API key" not in llm.explain(oai, 400, '{"error": {"message": "messages: field required"}}')


def test_providers_saved_before_checks_get_checked_at_startup(client, router_like):
    from app import db, llm, security

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, created_at) VALUES ('Old', 'openai', ?, ?, ?)",
                 router_like, security.encrypt("good"), db.now())
    assert llm.check_info(pid) == {}
    asyncio.run(llm.check_unchecked())
    info = llm.check_info(pid)
    assert info["ok"] is True and info["recommended"] == "free-model:free"


# Model setup regression checklist
def test_a_broken_provider_does_not_break_the_others(client, router_like):
    from app import db, llm

    bad_id = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                    "('Broken', 'openai', 'http://127.0.0.1:9/v1', '', '', 1, ?)", db.now())
    try:
        asyncio.run(llm.check_provider(bad_id))
        good = connect(client, router_like, "good")
        ref, source = llm.effective_default()
        assert ref == f"{good['provider_id']}/free-model:free" and source == "auto"
        assert asyncio.run(llm.chat(None, [{"role": "user", "content": "hi"}]))["message"]["content"] == "OK from free-model:free"
        st = client.get("/api/models/status").json()
        broken = next(p for p in st["providers"] if p["id"] == bad_id)
        assert broken["checked"] and not broken["ok"] and broken["error"]
    finally:
        client.delete(f"/api/providers/{bad_id}")


def test_unverified_providers_are_never_used_automatically(client, router_like):
    """Free models are not assumed to work: a provider nobody checked is skipped until a real request succeeds."""
    from app import db, llm

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('Unchecked', 'openai', ?, '', 'free-model:free', 1, ?)", router_like, db.now())
    try:
        assert llm.recommended_ref(db.one("SELECT * FROM providers WHERE id = ?", pid)) is None
        with pytest.raises(Exception, match="connected|working"):
            llm.effective_default()
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_connection_checks_are_capped_and_time_limited(client, router_like, monkeypatch):
    from app import llm

    PINGS.clear()
    good = connect(client, router_like, "good")
    assert good["ok"] and PINGS and all(p.get("max_tokens") == 32 for p in PINGS)
    # A provider rejecting the cap must NEVER trigger an uncapped paid retry.
    PINGS.clear()
    MODE["reject_cap"] = True
    try:
        assert asyncio.run(llm.check_provider(good["provider_id"], max_tries=1))["ok"] is False
        assert len(PINGS) == 1 and PINGS[0]["max_tokens"] == 32
    finally:
        MODE["reject_cap"] = False
    # a provider that never answers is given up on, with a clear reason
    monkeypatch.setattr(llm, "PING_TIMEOUT", 0.5)
    MODE["hang"] = True
    try:
        started = time.monotonic()
        r = asyncio.run(llm.check_provider(good["provider_id"], max_tries=2))
        assert r["ok"] is False and "did not answer within" in r["error"] and time.monotonic() - started < 4
    finally:
        MODE["hang"] = False


def test_free_default_never_falls_back_to_paid_unless_allowed(client, router_like):
    from app import db, llm

    good = connect(client, router_like, "good")
    pid = good["provider_id"]
    other = connect(client, router_like.replace("127.0.0.1", "localhost"), "good")  # second provider, same fake
    try:
        db.set_setting(f"provider_check:{other['provider_id']}", json.dumps({**other, "recommended": "paid-model", "ok": True}))
        primary = f"{pid}/free-model:free"
        assert f"{other['provider_id']}/paid-model" not in llm.fallback_refs(primary)
        assert client.put("/api/usage/limits", json={"fallback_allow_paid": True}).json()["fallback_allow_paid"] is True
        assert f"{other['provider_id']}/paid-model" in llm.fallback_refs(primary)
    finally:
        client.put("/api/usage/limits", json={"fallback_allow_paid": False})
        client.delete(f"/api/providers/{other['provider_id']}")


def test_spending_limit_stops_calls_and_fallback_cannot_bypass_it(client, router_like):
    from app import db, llm

    connect(client, router_like, "good")
    db.run("INSERT INTO usage(ts, model, channel, prompt_tokens, completion_tokens, cost_cents, priced) VALUES (?, 'x', 'web', 1, 1, 500, 1)", db.now())
    try:
        client.put("/api/usage/limits", json={"monthly_limit_cents": 100})
        with pytest.raises(llm.ModelError) as e:
            asyncio.run(llm.chat(None, [{"role": "user", "content": "hi"}]))
        assert "spending limit" in str(e.value.detail) and not e.value.retryable
        assert client.get("/api/usage").json()["limits"]["monthly_limit_cents"] == 100
    finally:
        client.put("/api/usage/limits", json={"monthly_limit_cents": 0})
        db.run("DELETE FROM usage WHERE model = 'x'")


def test_keys_never_reach_the_browser(client, router_like):
    connect(client, router_like, "good")
    client.put("/api/assistant/settings", json={"tavily_api_key": "tvly-0123456789abcdefghij"})
    try:
        for path in ("/api/providers", "/api/models/status", "/api/assistant/settings", "/api/connections", "/api/settings"):
            text = client.get(path).text
            assert '"good"' not in text and "tvly-0123456789" not in text, path
    finally:
        client.put("/api/assistant/settings", json={"tavily_api_key": ""})
