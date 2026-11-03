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


SIGNUP = {"tavily": "https://app.tavily.com/home", "brave": "https://api-dashboard.search.brave.com/app/keys"}
NAMES = {"searxng": "SearXNG", "brave": "Brave Search", "tavily": "Tavily"}


async def test_key(name: str, key: str) -> str:
    """'' when one real search with this key returns results, else what went wrong, in plain words."""
    try:
        items = await web_search("weather", 3, use=(name, key.strip()))
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        return ("That key was refused. Copy it again from the key page." if code in (401, 403)
                else f"{NAMES[name]} answered {code}. Try again in a minute.")
    except httpx.HTTPError as e:
        return f"Could not reach {NAMES[name]}: {e.__class__.__name__}. Check the internet connection."
    return "" if items else f"{NAMES[name]} answered, but with no results. Try again in a minute."


async def web_search(query: str, count: int = 6, use: tuple[str, str] | None = None) -> list[dict]:
    """Results from the configured search service. `use` = (provider, key) checks a key before it is saved."""
    p = use[0] if use else provider()
    key = (lambda name: use[1]) if use else _key
    async with httpx.AsyncClient(timeout=20) as c:
        if p == "searxng":
            r = await c.get(f"{config.SEARXNG_URL}/search", params={"q": query, "format": "json"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                     for i in r.json().get("results", [])]
        elif p == "brave":
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(count, 20)},
                            headers={"X-Subscription-Token": key("BRAVE_API_KEY"), "Accept": "application/json"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")}
                     for i in (r.json().get("web") or {}).get("results", [])]
        elif p == "tavily":
            r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": min(count, 20)},
                             headers={"Authorization": f"Bearer {key('TAVILY_API_KEY')}"})
            r.raise_for_status()
            items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                     for i in r.json().get("results", [])]
        else:
            raise HTTPException(400, "Web search is off. Turn it on in Settings > Web search: a free Tavily key "
                                     "(1,000 searches a month, no card) takes about a minute.")
    return [i for i in items if i["url"].startswith(("http://", "https://"))][:count]
