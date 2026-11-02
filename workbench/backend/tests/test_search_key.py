"""Web search setup: a key is tested with one real search and saved only when that works."""

import httpx

H = {"X-Workbench": "1"}


def test_search_key_is_tested_before_it_is_saved(client, monkeypatch):
    from app import db, search

    calls = []

    async def fake(query, count=6, use=None):
        calls.append(use)
        if use and use[1] == "tvly-good-key-123":
            return [{"title": "Weather", "url": "https://example.com", "snippet": ""}]
        req = httpx.Request("POST", "https://api.tavily.com/search")
        raise httpx.HTTPStatusError("no", request=req, response=httpx.Response(401, request=req))

    monkeypatch.setattr(search, "web_search", fake)
    db.set_setting("secret:TAVILY_API_KEY", "")
    assert client.get("/api/assistant/search").json()["provider"] in ("", "searxng", "brave")

    bad = client.post("/api/assistant/search-key", headers=H, json={"provider": "tavily", "key": "tvly-wrong-key-1"})
    assert bad.status_code == 400 and "refused" in bad.json()["detail"]
    assert not search._key("TAVILY_API_KEY")  # nothing saved

    ok = client.post("/api/assistant/search-key", headers=H, json={"provider": "tavily", "key": "tvly-good-key-123"})
    assert ok.status_code == 200 and ok.json()["name"] == "Tavily"
    assert search._key("TAVILY_API_KEY") == "tvly-good-key-123"
    assert calls[-1] == ("tavily", "tvly-good-key-123")
    db.set_setting("secret:TAVILY_API_KEY", "")


def test_search_off_says_how_to_turn_it_on(client, monkeypatch):
    import asyncio

    import pytest
    from fastapi import HTTPException

    from app import search

    monkeypatch.setattr(search, "provider", lambda: "")
    with pytest.raises(HTTPException) as e:
        asyncio.run(search.web_search("x"))
    assert "Settings > Web search" in e.value.detail and "no card" in e.value.detail
