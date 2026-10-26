"""Web search without extra containers: Brave Search API, Tavily API, or a SearXNG instance if you run one.

Keys are set in Settings (stored encrypted) or by environment variable. The first configured one is used.
"""

import os

import httpx
from fastapi import HTTPException

from . import config, db
from .security import decrypt


def _key(name: str) -> str:
    return decrypt(db.get_setting(f"secret:{name}", "") or "") or os.getenv(name, "")


def provider() -> str:
    if config.SEARXNG_URL:
        return "searxng"
    if _key("BRAVE_API_KEY"):
        return "brave"
    if _key("TAVILY_API_KEY"):
        return "tavily"
    return ""


async def web_search(query: str, count: int = 6) -> list[dict]:
    p = provider()
    async with httpx.AsyncClient(timeout=20) as c:
        if p == "searxng":
            r = await c.get(f"{config.SEARXNG_URL}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                     for i in r.json().get("results", [])]
        elif p == "brave":
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(count, 20)},
                            headers={"X-Subscription-Token": _key("BRAVE_API_KEY"), "Accept": "application/json"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")}
                     for i in (r.json().get("web") or {}).get("results", [])]
        elif p == "tavily":
            r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": min(count, 20)},
                             headers={"Authorization": f"Bearer {_key('TAVILY_API_KEY')}"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                     for i in r.json().get("results", [])]
        else:
            raise HTTPException(400, "Web search is not set up. Add a Brave Search or Tavily API key in Settings, "
                                     "or run the optional SearXNG add-on.")
    return [i for i in items if i["url"].startswith(("http://", "https://"))][:count]
