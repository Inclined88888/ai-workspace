"""The smart router: fallback across fake providers that return 429s, daily limits, 5xx; quota headers; tiers."""

import json
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from conftest import SCRIPT, _port, events

prov = FastAPI()
MODE: dict[str, dict] = {}      # provider name -> {"status": 200 | 429 | 500, "body": str, "headers": {...}}
HITS: list[tuple[str, str]] = []  # (provider, model) per chat call
MODELS = {"groq": ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "whisper-large-v3"],
          "gemini": ["gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-embedding-001"],
          "paid": ["big-model", "big-model-mini"]}


@prov.get("/{name}/{host}/v1/models")
def p_models(name: str, host: str):
    return {"data": [{"id": m} for m in MODELS.get(name, ["m"])]}


@prov.post("/{name}/{host}/v1/chat/completions")
async def p_chat(name: str, host: str, req: Request):
    body = await req.json()
    HITS.append((name, body["model"]))
    m = MODE.get(f"{name}:{body['model']}") or MODE.get(name, {})  # per model first: a retired model 404s alone
    status = m.get("status", 200)
    if status != 200:
        return JSONResponse({"error": {"message": m.get("body", "error")}}, status_code=status, headers=m.get("headers", {}))
    if body.get("stream") and body.get("tools"):  # a tool-using turn read as a stream: the scripted reply, chunked
        reply = SCRIPT.pop(0) if SCRIPT else {"role": "assistant", "content": f"answer from {name}"}

        def gen_reply():
            if reply.get("content"):
                yield f"data: {json.dumps({'choices': [{'delta': {'content': reply['content']}}]})}\n\n"
            for i, tc in enumerate(reply.get("tool_calls") or []):
                yield f"data: {json.dumps({'choices': [{'delta': {'tool_calls': [{**tc, 'index': i}]}}]})}\n\n"
            yield f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 4}})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen_reply(), media_type="text/event-stream", headers=m.get("headers", {}))
    if body.get("stream"):
        def gen():
            yield f"data: {json.dumps({'choices': [{'delta': {'content': f'hi from {name}'}}]})}\n\n"
            yield f"data: {json.dumps({'choices': [], 'usage': {'prompt_tokens': 5, 'completion_tokens': 3}})}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream", headers=m.get("headers", {}))
    reply = SCRIPT.pop(0) if SCRIPT else {"role": "assistant", "content": f"answer from {name}"}
    return JSONResponse({"choices": [{"message": reply, "finish_reason": "stop"}], "usage": {"prompt_tokens": 10, "completion_tokens": 4}},
                        headers=m.get("headers", {}))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
def prov_url():
    port = _port()
    server = uvicorn.Server(uvicorn.Config(prov, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    import socket
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture
def routed(client, prov_url):
    """Three cloud connections only: a Groq-shaped and a Gemini-shaped free tier, then a paid key. Everything else is
    left out of the router for the test and put back afterwards."""
    from app import db, llm

    saved = {k: db.get_setting(k) for k in ("router", "default_model")}
    others = [c["id"] for c in db.all("SELECT id FROM connections")]
    ids = {}
    # The host part makes the router see each one as that provider (it matches on the base URL)
    for name, host in (("groq", "api.groq.com"), ("gemini", "generativelanguage.googleapis.com"), ("paid", "api.example.com")):
        r = client.post("/api/providers", json={"name": name.title(), "auth_kind": "api_key", "protocol": "chat_completions",
                                                "base_url": f"{prov_url}/{name}/{host}/v1", "api_key": f"key-{name}-12345"})
        ids[name] = r.json()["id"]
    client.put("/api/router", json={"order": [ids["groq"], ids["gemini"], ids["paid"]], "disabled": others, "models": {}})
    db.run("DELETE FROM router_state")
    MODE.clear()
    HITS.clear()
    llm._model_cache.clear()
    yield ids
    for cid in ids.values():
        client.delete(f"/api/providers/{cid}")
    for k, v in saved.items():
        db.set_setting(k, v or ("{}" if k == "router" else ""))
    db.run("DELETE FROM router_state")
    MODE.clear()


async def _chat(messages=None, tools=None, ref="auto", channel=""):
    from app import llm

    return await llm.chat(ref, messages or [{"role": "user", "content": "hi"}], tools, trace={"channel": channel})


@pytest.mark.anyio
async def test_429_falls_back_to_the_next_provider_and_parks_the_first(routed):
    MODE["groq"] = {"status": 429, "body": "Rate limit reached for requests per minute", "headers": {"retry-after": "7"}}
    res = await _chat()
    assert res["message"]["content"] == "answer from gemini"
    r = res["router"]
    assert r["provider"] == "Gemini" and r["model"] == "gemini-2.5-flash-lite" and r["tier"] == "fast"
    assert r["fallbacks"] == [{"provider": "Groq", "model": "openai/gpt-oss-20b", "reason": "busy (rate limited)"}]
    from app import db
    row = db.one("SELECT * FROM router_state WHERE connection_id = ?", routed["groq"])
    assert 5 < row["cooldown_until"] - time.time() <= 7.5  # the provider's own Retry-After, not a guess
    HITS.clear()
    MODE.pop("groq")
    res = await _chat()  # still parked: not even tried
    assert [h[0] for h in HITS] == ["gemini"] and res["router"]["fallbacks"] == []


@pytest.mark.anyio
async def test_daily_limit_parks_until_the_providers_reset(routed):
    from app import router
    from app import db

    MODE["groq"] = {"status": 429, "body": "Rate limit reached for model `openai/gpt-oss-20b` on requests per day (RPD): Limit 1000, Used 1000"}
    MODE["gemini"] = {"status": 429, "body": '{"status": "RESOURCE_EXHAUSTED", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}'}
    res = await _chat()
    assert res["message"]["content"] == "answer from paid"
    assert [f["reason"] for f in res["router"]["fallbacks"]] == ["daily limit reached", "daily limit reached"]
    gem = db.one("SELECT * FROM connections WHERE id = ?", routed["gemini"])
    row = db.one("SELECT * FROM router_state WHERE connection_id = ?", routed["gemini"])
    assert abs(row["cooldown_until"] - router.next_reset(gem)) < 2  # midnight Pacific for Gemini
    q = router.quota(gem)
    assert q["cooling_reason"] == "daily limit reached" and q["resets_at"]


@pytest.mark.anyio
async def test_5xx_falls_back_and_every_failure_is_explained(routed):
    from fastapi import HTTPException

    MODE["groq"] = {"status": 503, "body": "over capacity"}
    res = await _chat()
    assert res["router"]["provider"] == "Gemini" and res["router"]["fallbacks"][0]["reason"] == "not responding"
    for name in ("gemini", "paid"):
        MODE[name] = {"status": 429, "body": "Too many requests"}
    with pytest.raises(HTTPException) as e:
        await _chat()
    assert e.value.status_code == 429
    assert "Gemini" in e.value.detail and "Paid" in e.value.detail and "busy" in e.value.detail


@pytest.mark.anyio
async def test_quota_headers_and_tiers(routed):
    from app import db, router

    MODE["groq"] = {"headers": {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "812",
                                "x-ratelimit-limit-tokens": "8000", "x-ratelimit-remaining-tokens": "7400"}}
    fast = await _chat()
    assert fast["router"]["model"] == "openai/gpt-oss-20b"
    q = fast["router"]["quota"]
    assert (q["limit"], q["remaining"], q["window"], q["free"]) == (1000, 812, "day", True)  # Groq counts requests per day
    tools = [{"type": "function", "function": {"name": "x", "description": "x", "parameters": {"type": "object", "properties": {}}}}]
    strong = await _chat(tools=tools)
    assert strong["router"]["model"] == "openai/gpt-oss-120b" and strong["router"]["tier"] == "strong"
    assert (await _chat(channel="rooks-code"))["router"]["tier"] == "strong"
    assert (await _chat(ref="auto:strong"))["router"]["tier"] == "strong"
    assert db.one("SELECT requests FROM router_state WHERE connection_id = ?", routed["groq"])["requests"] == 4
    # A user's per-provider model choice wins
    router.save_settings({"models": {str(routed["groq"]): {"fast": "openai/gpt-oss-120b"}}})
    assert (await _chat())["router"]["model"] == "openai/gpt-oss-120b"
    # Zero left for the day: parked until the reset without waiting for a 429
    MODE["groq"]["headers"]["x-ratelimit-remaining-requests"] = "0"
    await _chat()
    assert (await _chat())["router"]["provider"] == "Gemini"


@pytest.mark.anyio
async def test_stream_falls_back_before_the_first_token(routed):
    from app import llm

    MODE["groq"] = {"status": 429, "body": "slow down"}
    out = [e async for e in llm.stream("auto", [{"role": "user", "content": "hi"}], trace={"channel": "research"})]
    text = "".join(e.get("delta", "") for e in out)
    assert text == "hi from gemini"
    assert out[0]["router"]["provider"] == "Gemini" and out[0]["router"]["fallbacks"][0]["provider"] == "Groq"
    assert out[-1]["done"] and out[-1]["router"]["quota"]["used_today"] == 1


def test_chat_shows_who_answered(client, routed):
    MODE["groq"] = {"status": 429, "body": "rate limit"}
    SCRIPT[:] = [{"role": "assistant", "content": "Hello there."}]
    ev = events(client.post("/api/assistant/chat", json={"message": "hello", "model": "auto"}))
    via = next(e for e in ev if e["type"] == "router")
    assert via["provider"] == "Gemini" and via["tier"] == "strong" and via["fallbacks"][0]["reason"] == "busy (rate limited)"
    assert next(e for e in ev if e["type"] == "message")["content"] == "Hello there."


def test_router_api_and_default(client, routed):
    s = client.get("/api/router").json()
    rows = {p["id"]: p for p in s["providers"]}
    assert rows[routed["groq"]]["fast"] == "openai/gpt-oss-20b" and rows[routed["groq"]]["free"]
    assert rows[routed["gemini"]]["strong"] == "gemini-2.5-flash" and rows[routed["paid"]]["fast"] == "big-model-mini"
    assert client.put("/api/settings", json={"default_model": "auto"}).status_code == 200
    models = client.get("/api/models").json()
    assert models["default"] == "auto" and models["models"][0]["ref"] == "auto"
    client.put("/api/router", json={"order": [routed["paid"], routed["groq"], routed["gemini"]]})
    assert client.get("/api/router").json()["tiers"]["fast"] == "Paid"


def test_rooks_clients_get_auto_through_the_bridge(client, routed, monkeypatch):
    from app import config

    monkeypatch.setattr(config, "BROWSER_API_KEY", "b" * 32)
    h = {"Authorization": "Bearer " + "b" * 32}
    ids = [m["id"] for m in client.get("/v1/models", headers=h).json()["data"]]
    assert ids[:3] == ["auto", "auto:fast", "auto:strong"]
    MODE["groq"] = {"status": 500, "body": "boom"}
    r = client.post("/v1/chat/completions", headers={**h, "X-Workbench-Channel": "rooks-code"},
                    json={"model": "auto", "messages": [{"role": "user", "content": "write code"}]}).json()
    assert r["choices"][0]["message"]["content"] == "answer from gemini"
    assert r["router"]["tier"] == "strong" and r["router"]["fallbacks"][0]["reason"] == "not responding"


def test_local_is_last_and_only_when_allowed(client, routed):
    from app import router

    oll = client.post("/api/providers", json={"name": "Ollama", "auth_kind": "local_ollama", "protocol": "ollama_native",
                                              "base_url": "http://127.0.0.1:9"}).json()["id"]
    try:
        names = [c["name"] for c in router._routable(router.ordered_connections())]
        assert "Ollama" not in names
        router.save_settings({"local_fallback": True})
        assert [c["name"] for c in router._routable(router.ordered_connections())][-1] == "Ollama"
    finally:
        router.save_settings({"local_fallback": False})
        client.delete(f"/api/providers/{oll}")


def test_local_default_uses_an_installed_model(monkeypatch):
    """Ollama without the configured DEFAULT_MODEL: the first chat must not 404 on a model that is not installed."""
    from app import config, llm

    monkeypatch.setattr(config, "DEFAULT_MODEL", "qwen3:4b")
    monkeypatch.setattr(llm, "list_models_for", lambda cid, fresh=False: [{"model": "nomic-embed-text"}, {"model": "qwen2.5:7b"}])
    assert llm._local_default(1) == "qwen2.5:7b"
    monkeypatch.setattr(llm, "list_models_for", lambda cid, fresh=False: [{"model": "qwen3:4b"}, {"model": "qwen2.5:7b"}])
    assert llm._local_default(1) == "qwen3:4b"

    def down(cid, fresh=False):
        raise RuntimeError("refused")
    monkeypatch.setattr(llm, "list_models_for", down)
    assert llm._local_default(1) == "qwen3:4b"


# Vision: screenshots from Rooks Desk / Rooks Browser go to a model that can see; otherwise they are taken out.
PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
SHOT = [{"role": "user", "content": [{"type": "text", "text": "What is on screen?"}, {"type": "image_url", "image_url": {"url": PIXEL}}]}]


def test_which_models_can_see():
    from app.router import sees

    for m in ("gemini-2.5-flash", "meta-llama/llama-4-scout-17b-16e-instruct", "qwen/qwen3.8-27b", "gpt-4o-mini", "claude-sonnet-4",
              "google/gemma-3-27b-it:free", "qwen2.5vl:7b", "llava:13b"):
        assert sees(m), m
    # o3-mini is text only; the name rules said it could see, the catalog knows it cannot
    for m in ("o3-mini", "openai/gpt-oss-120b", "qwen2.5:7b", "llama-3.3-70b-versatile", "qwen/qwen3-coder:free", "deepseek-chat", "llama-3.1-8b-instant"):
        assert not sees(m), m


@pytest.mark.anyio
async def test_screenshots_go_to_a_model_that_can_see(routed, monkeypatch):
    monkeypatch.setitem(MODELS, "groq", MODELS["groq"] + ["meta-llama/llama-4-scout-17b-16e-instruct"])
    from app import llm
    llm._model_cache.clear()
    res = await _chat(SHOT, channel="rooks-desk")
    assert HITS == [("groq", "meta-llama/llama-4-scout-17b-16e-instruct")]  # Groq first, but its vision model, not gpt-oss
    assert res["router"]["vision"] is True and res["router"]["tier"] == "strong"
    HITS.clear()
    MODE["groq"] = {"status": 429, "body": "Too many requests"}
    res = await _chat(SHOT, channel="rooks-desk")
    assert HITS[-1] == ("gemini", "gemini-2.5-flash") and res["router"]["vision"] is True


@pytest.mark.anyio
async def test_when_nothing_can_see_the_images_are_left_out(routed, monkeypatch):
    monkeypatch.setitem(MODELS, "gemini", ["gemma-2-9b-text"])
    monkeypatch.setitem(MODELS, "paid", ["big-model"])
    from app import llm
    llm._model_cache.clear()
    seen = []
    real = llm.chat_one

    async def spy(ref, messages, *a, **k):
        seen.append(messages)
        return await real(ref, messages, *a, **k)

    monkeypatch.setattr(llm, "chat_one", spy)
    res = await _chat(SHOT, channel="rooks-desk")
    assert HITS == [("groq", "openai/gpt-oss-120b")] and res["router"]["vision"] is False
    parts = seen[0][0]["content"]
    assert not any(p.get("type") == "image_url" for p in parts)
    assert "cannot see images" in parts[-1]["text"] and parts[0]["text"] == "What is on screen?"


# Model catalog (models.dev): tiers, vision, context and price come from facts, not from model names.
def _conn(host: str, cid: int = 990) -> dict:
    return {"id": cid, "name": host, "base_url": f"https://{host}/v1", "auth_kind": "api_key"}


def test_strong_never_resolves_to_a_small_sibling(client):
    from app import router

    openai = _conn("api.openai.com")
    assert router.choose_model(openai, ["gpt-6-mini", "gpt-6-nano", "gpt-5"], "strong") == "gpt-5"
    assert router.choose_model(openai, ["gpt-6-mini", "gpt-6-nano", "gpt-5"], "fast") == "gpt-6-mini"
    xai = _conn("api.x.ai")
    assert router.choose_model(xai, ["grok-4-fast-reasoning", "grok-4-0709", "grok-3-mini"], "strong") == "grok-4-0709"


def test_newest_family_member_wins_by_release_date(client):
    from app import router

    anthropic = _conn("api.anthropic.com")
    listed = ["claude-sonnet-4-20250514", "claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]
    # plain string order would pick claude-sonnet-4-20250514 (\"2\" > \"5\" fails, but 20250514 > 5 by version number)
    assert router.choose_model(anthropic, listed, "strong") == "claude-sonnet-4-5-20250929"


def test_vision_and_context_come_from_the_catalog():
    from app import catalog, router
    from app.adapters import CHARS_PER_TOKEN, CLOUD_CONTEXT_CHARS, Adapter

    assert router.sees("llama-3.2-11b-vision-instruct")  # the name rules called this blind
    assert router.sees("gpt-5", _conn("api.openai.com"))
    groq = {**_conn("api.groq.com"), "protocol": "chat_completions", "secret_enc": ""}
    assert catalog.context_tokens(groq["base_url"], "openai/gpt-oss-120b") == 131072
    assert Adapter(groq).context_chars("openai/gpt-oss-120b") == int(131072 * CHARS_PER_TOKEN * 0.75)
    assert Adapter(groq).context_chars("never-heard-of-it") == CLOUD_CONTEXT_CHARS


def test_cost_from_the_catalog_unless_settings_say_otherwise(client):
    from app import catalog, db, usage

    cid = client.post("/api/providers", json={"name": "OpenAI cat", "auth_kind": "api_key", "protocol": "chat_completions",
                                              "base_url": "https://api.openai.com/v1", "api_key": "sk-test-123456"}).json()["id"]
    try:
        p_in, p_out = catalog.price("https://api.openai.com/v1", "gpt-5")
        assert usage.price_usd(f"{cid}/gpt-5", 1_000_000, 1_000_000) == p_in + p_out
        assert usage.price_usd(f"{cid}/never-heard-of-it", 10, 10) is None
        db.set_setting("model_prices", json.dumps({f"{cid}/gpt-5": {"in": 1, "out": 1}}))
        assert usage.price_usd(f"{cid}/gpt-5", 1_000_000, 0) == 1
    finally:
        db.set_setting("model_prices", "{}")
        client.delete(f"/api/providers/{cid}")
    assert catalog.price("https://openrouter.ai/api/v1", "openai/gpt-oss-120b:free") == (0.0, 0.0)
    assert catalog.price("http://127.0.0.1:1234/v1", "gpt-5") is None  # another provider's price is not this server's


@pytest.mark.anyio
async def test_a_retired_model_falls_back_to_the_same_providers_next_model(routed):
    from app import router

    router._dead.clear()
    MODE["groq:openai/gpt-oss-20b"] = {"status": 404, "body": "The model `openai/gpt-oss-20b` does not exist"}
    try:
        res = await _chat()
        assert res["message"]["content"] == "answer from groq" and res["router"]["model"] == "openai/gpt-oss-120b"
        assert res["router"]["fallbacks"] == [{"provider": "Groq", "model": "openai/gpt-oss-20b", "reason": "model not available"}]
        HITS.clear()
        await _chat()  # remembered: the retired model is not tried again
        assert HITS == [("groq", "openai/gpt-oss-120b")]
    finally:
        router._dead.clear()


def test_new_presets_and_key_detection():
    from app import llm

    assert llm.detect_key("nvapi-abc123") == "nvidia" and llm.detect_key("fw_abc123") == "fireworks"
    assert llm.detect_key("sk-ant-abc") == "anthropic" and llm.detect_key("sk-abc") == "openai"
    assert llm.PRESETS["nvidia"]["base_url"] == "https://integrate.api.nvidia.com/v1"


def test_a_key_that_lists_models_but_cannot_chat_is_not_green(client, routed):
    from app import db
    from app.adapters import Adapter

    groq = db.one("SELECT * FROM connections WHERE id = ?", routed["groq"])
    assert Adapter(groq).probe()[0] == "ok"  # the model list alone looks fine
    status, detail = Adapter(groq).probe(deep=True)
    assert status == "ok" and "answered" in detail
    MODE["groq"] = {"status": 429, "body": '{"error": {"code": "insufficient_quota"}}'}
    status, detail = Adapter(groq).probe(deep=True)
    assert status == "degraded" and "out of credit" in detail
    MODE["groq"] = {"status": 403, "body": "no access"}
    assert Adapter(groq).probe(deep=True)[0] == "down"


def test_health_shows_a_resting_provider_as_degraded(client, routed):
    from app import db, router

    groq = db.one("SELECT * FROM connections WHERE id = ?", routed["groq"])
    router.state(groq)  # the row _cool updates, as note_fail creates it first
    router._cool(groq, time.time() + 600, "daily limit reached")
    rows = {r["ref"]: r for r in client.get("/api/health", params={"fresh": "true"}).json()["checks"] if r.get("ref")}
    assert rows[routed["groq"]]["status"] == "degraded" and "daily limit reached" in rows[routed["groq"]]["detail"]


def test_a_rejected_key_says_so_instead_of_no_models(client, routed, monkeypatch):
    from app import db, llm

    monkeypatch.setitem(MODELS, "groq", [])
    real = llm.Adapter.list_models

    def rejected(self):
        self.list_error = "the key was rejected (HTTP 401)"
        return []
    monkeypatch.setattr(llm.Adapter, "list_models", rejected)
    llm._model_cache.clear()
    assert llm.list_models_for(routed["groq"]) == []
    assert db.one("SELECT last_error FROM connections WHERE id = ?", routed["groq"])["last_error"] == "the key was rejected (HTTP 401)"
    monkeypatch.setattr(llm.Adapter, "list_models", real)


def test_anthropic_requests_mark_cache_breakpoints_and_count_cached_tokens():
    from app.adapters import AnthropicMessages, _anthropic_usage

    tools = [{"type": "function", "function": {"name": n, "description": n, "parameters": {"type": "object", "properties": {}}}}
             for n in ("a", "b")]
    body = AnthropicMessages()._body("claude-x", [{"role": "system", "content": "You are helpful."},
                                                  {"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"},
                                                  {"role": "user", "content": "again"}], False, tools)
    assert body["system"] == [{"type": "text", "text": "You are helpful.", "cache_control": {"type": "ephemeral"}}]
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in body["tools"][0]
    assert body["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in body["messages"][0]["content"][-1]
    assert sum(json.dumps(body).count("cache_control") for _ in [0]) == 3  # Anthropic allows at most 4
    u = _anthropic_usage({"input_tokens": 10, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 5000, "output_tokens": 7})
    assert u == {"prompt_tokens": 7010, "completion_tokens": 7, "cache_write_tokens": 2000, "cache_read_tokens": 5000}


def test_cached_tokens_are_priced_at_the_cache_rate(client):
    from app import catalog, usage

    cid = client.post("/api/providers", json={"name": "Claude cat", "auth_kind": "api_key", "protocol": "anthropic_messages",
                                              "base_url": "https://api.anthropic.com/v1", "api_key": "sk-ant-test-123456"}).json()["id"]
    try:
        model = "claude-sonnet-4-5-20250929"
        p_in, _ = catalog.price("https://api.anthropic.com/v1", model)
        read_p, _ = catalog.cache_prices("https://api.anthropic.com/v1", model)
        full = usage.price_usd(f"{cid}/{model}", 1_000_000, 0)
        cached = usage.price_usd(f"{cid}/{model}", 1_000_000, 0, cache_read=1_000_000)
        assert full == p_in and abs(cached - read_p) < 1e-9 and cached < full
    finally:
        client.delete(f"/api/providers/{cid}")


def test_a_key_changed_in_env_is_applied_on_the_next_start_but_never_over_an_app_edit(client, monkeypatch):
    from app import config, db, llm
    from app.security import decrypt, store_connection_secret

    url = llm.PRESETS["nvidia"]["base_url"]
    monkeypatch.setitem(config.SEED_KEYS, "nvidia", "nvapi-first-1234")
    llm.seed_connections()
    row = db.one("SELECT * FROM connections WHERE base_url = ?", url)
    assert decrypt(row["secret_enc"]) == "nvapi-first-1234"
    monkeypatch.setitem(config.SEED_KEYS, "nvidia", "nvapi-second-5678")  # edited in .env, then restarted
    llm.seed_connections()
    assert decrypt(db.one("SELECT secret_enc FROM connections WHERE id = ?", row["id"])["secret_enc"]) == "nvapi-second-5678"
    assert len(db.all("SELECT id FROM connections WHERE base_url = ?", url)) == 1
    store_connection_secret(row["id"], "nvapi-typed-in-app")  # the user changes it in the app; .env unchanged
    llm.seed_connections()
    assert decrypt(db.one("SELECT secret_enc FROM connections WHERE id = ?", row["id"])["secret_enc"]) == "nvapi-typed-in-app"
    db.run("DELETE FROM connections WHERE id = ?", row["id"])


# Plans through the user's own CLI: a fake `claude` and `codex` stand in for the real binaries.
FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys
prompt = sys.stdin.read()
open(os.environ["FAKE_CLI_LOG"], "a").write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "prompt": prompt,
    "has_key": "ANTHROPIC_API_KEY" in os.environ}) + "\n")
if "LIMIT" in prompt:
    print(json.dumps({"type": "result", "is_error": True, "result": "Claude usage limit reached. Resets at 5pm"})); sys.exit(1)
if "read_file" in " ".join(sys.argv) and "TOOL RESULT" not in prompt:
    print(json.dumps({"type": "result", "is_error": False, "result": '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}</tool_call>',
                      "usage": {"input_tokens": 7, "output_tokens": 3}})); sys.exit(0)
print(json.dumps({"type": "result", "is_error": False, "result": "plan answer", "usage": {"input_tokens": 12, "cache_read_input_tokens": 5, "output_tokens": 2}}))
'''
FAKE_CODEX = r'''#!/usr/bin/env python3
import json, sys
sys.stdin.read()
for ev in ({"type": "thread.started", "thread_id": "t1"}, {"type": "item.completed", "item": {"type": "agent_message", "text": "codex answer"}},
           {"type": "turn.completed", "usage": {"input_tokens": 9, "output_tokens": 4}}):
    print(json.dumps(ev))
'''


@pytest.fixture
def fake_clis(tmp_path, monkeypatch):
    from app import cli_agents

    paths = {}
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        p = tmp_path / name
        p.write_text(body)
        p.chmod(0o755)
        paths[name] = p
    monkeypatch.setenv("WORKBENCH_CLAUDE_BIN", str(paths["claude"]))
    monkeypatch.setenv("WORKBENCH_CODEX_BIN", str(paths["codex"]))
    monkeypatch.setenv("FAKE_CLI_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-must-not-reach-the-cli")
    cli_agents._DETECT_CACHE.clear()
    yield tmp_path / "calls.jsonl"
    cli_agents._DETECT_CACHE.clear()


def _plan_conn(client, which):
    r = client.post("/api/providers", json={"name": f"{which} plan", "auth_kind": "cli_subscription", "protocol": "cli_plan",
                                            "base_url": f"cli://{which}"})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.anyio
async def test_claude_plan_answers_through_the_users_own_cli(client, fake_clis):
    from app import llm

    cid = _plan_conn(client, "claude")
    try:
        assert [m["model"] for m in llm.list_models_for(cid, fresh=True)] == ["default", "haiku", "opus", "sonnet"]
        res = await llm.chat(f"{cid}/sonnet", [{"role": "system", "content": "Be brief."}, {"role": "user", "content": "hi"}])
        assert res["message"]["content"] == "plan answer" and res["usage"]["prompt_tokens"] == 17
        call = json.loads(fake_clis.read_text().splitlines()[-1])
        assert call["argv"][:6] == ["-p", "--output-format", "json", "--tools", "", "--no-session-persistence"]
        assert ["--system-prompt", "Be brief."] == call["argv"][6:8] and call["argv"][-2:] == ["--model", "sonnet"]
        assert not call["has_key"] and "aiw-cli-" in call["cwd"]  # the user's sign-in, never the app's key; an empty folder
        tools = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {"type": "object"}}}]
        res = await llm.chat(f"{cid}/default", [{"role": "user", "content": "read a.py"}], tools)
        assert res["message"]["tool_calls"][0]["function"]["name"] == "read_file"  # text tool call recovered
    finally:
        client.delete(f"/api/providers/{cid}")


@pytest.mark.anyio
async def test_a_plan_limit_is_a_quota_the_router_falls_back_from(client, fake_clis):
    from fastapi import HTTPException

    from app import llm, router

    cid = _plan_conn(client, "claude")
    try:
        with pytest.raises(HTTPException) as e:
            await llm.chat(f"{cid}/default", [{"role": "user", "content": "LIMIT please"}])
        assert router.classify(e.value)[0] in ("daily limit reached", "busy (rate limited)")
    finally:
        client.delete(f"/api/providers/{cid}")


@pytest.mark.anyio
async def test_codex_plan_answers_through_codex_exec(client, fake_clis):
    from app import llm

    cid = _plan_conn(client, "codex")
    try:
        res = await llm.chat(f"{cid}/default", [{"role": "user", "content": "hi"}])
        assert res["message"]["content"] == "codex answer" and res["usage"] == {"prompt_tokens": 9, "completion_tokens": 4}
    finally:
        client.delete(f"/api/providers/{cid}")


def test_plan_cli_urls_only_for_plan_connections(client):
    bad = client.post("/api/providers", json={"name": "x", "auth_kind": "api_key", "protocol": "chat_completions", "base_url": "cli://claude"})
    assert bad.status_code == 400
    bad = client.post("/api/providers", json={"name": "x", "auth_kind": "cli_subscription", "protocol": "cli_plan", "base_url": "https://x.example"})
    assert bad.status_code == 400


def test_model_pickers_leave_out_models_that_cannot_chat():
    from app.router import chat_models

    got = chat_models(["meta/llama-4", "meta/llama-guard-4-12b", "nvidia/nv-embedqa-mistral-7b-v2", "whisper-1",
                       "meta/llama-3.2-90b-vision-instruct", "gpt-5.1"])
    assert got == ["meta/llama-4", "meta/llama-3.2-90b-vision-instruct", "gpt-5.1"]
    assert chat_models(["text-embedding-3-small"]) == ["text-embedding-3-small"]  # never an empty picker
