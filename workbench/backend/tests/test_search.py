"""Web search: clear guidance when it is off, fallback across providers, and a real test from Settings."""

import asyncio

import httpx
import pytest


def _status_error(code: int, text: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://example.test")
    return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, text=text, request=req))


def test_not_set_up_points_to_settings(client, monkeypatch):
    from fastapi import HTTPException

    from app import search

    monkeypatch.setattr(search, "providers", lambda: [])
    with pytest.raises(HTTPException) as e:
        asyncio.run(search.web_search("anything"))
    assert "Settings, under Web search" in e.value.detail and "Tavily" in e.value.detail
    r = client.post("/api/search/test", json={"query": "x"}).json()
    assert r["ok"] is False and "not set up" in r["error"]


def test_used_up_provider_falls_back_to_the_next(client, monkeypatch):
    from app import search

    async def fake(c, p, q, n):
        if p == "brave":
            raise _status_error(429, "rate limited")
        return [{"title": "Evoro", "url": "https://evoro.example", "snippet": "waste management"}]

    monkeypatch.setattr(search, "providers", lambda: ["brave", "tavily"])
    monkeypatch.setattr(search, "_search_one", fake)
    items = asyncio.run(search.web_search("Evoro waste management Australia"))
    assert items[0]["url"] == "https://evoro.example"
    r = client.post("/api/search/test", json={"query": "Evoro"}).json()
    assert r["ok"] is True and r["results"][0]["title"] == "Evoro"


def test_rejected_keys_are_explained(client, monkeypatch):
    from fastapi import HTTPException

    from app import search

    async def fake(c, p, q, n):
        raise _status_error(401 if p == "tavily" else 432, "nope")

    monkeypatch.setattr(search, "providers", lambda: ["tavily", "brave"])
    monkeypatch.setattr(search, "_search_one", fake)
    with pytest.raises(HTTPException) as e:
        asyncio.run(search.web_search("x"))
    assert "Tavily rejected the key" in e.value.detail and "no searches left" in e.value.detail


def test_assistant_knows_when_search_is_off(client, monkeypatch):
    from app import assistant, search

    monkeypatch.setattr(search, "providers", lambda: [])
    prompt = assistant.system_prompt(assistant.Ctx(channel="web", conversation_id=0), "find a company")
    assert "Web search is not set up" in prompt and "read_page" in prompt
    monkeypatch.setattr(search, "providers", lambda: ["tavily"])
    assert "Web search is not set up" not in assistant.system_prompt(assistant.Ctx(channel="web", conversation_id=0), "x")


def test_real_invalid_key_responses(client):
    from app import search

    # captured from the live services with an invalid key
    assert "Brave Search rejected the key" in search._explain("brave", 422, '{"error":{"code":"SUBSCRIPTION_TOKEN_INVALID","detail":"The provided subscription token is invalid."}}')
    assert "Tavily rejected the key" in search._explain("tavily", 401, '{"detail":{"error":"Unauthorized: missing or invalid API key."}}')
