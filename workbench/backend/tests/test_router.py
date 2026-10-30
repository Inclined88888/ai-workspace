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
    m = MODE.get(name, {})
    status = m.get("status", 200)
    if status != 200:
        return JSONResponse({"error": {"message": m.get("body", "error")}}, status_code=status, headers=m.get("headers", {}))
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
              "google/gemma-3-27b-it:free", "qwen2.5vl:7b", "llava:13b", "o3-mini"):
        assert sees(m), m
    for m in ("openai/gpt-oss-120b", "qwen2.5:7b", "llama-3.3-70b-versatile", "qwen/qwen3-coder:free", "deepseek-chat", "llama-3.1-8b-instant"):
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
