"""Web search without extra containers: Brave Search API, Tavily API, or a SearXNG instance if you run one.

Keys are set in Settings (stored encrypted) or by environment variable. Every configured provider is tried in order,
so a used-up free quota on one does not stop search while another works.
"""

import asyncio
import os

import httpx
from fastapi import HTTPException

from . import config, db
from .security import decrypt

NOT_SET_UP = ("Web search is not set up yet. Add a free Tavily key in Settings, under Web search (1,000 searches a month, "
              "no card needed), or turn on the search add-on with ./setup.sh --with search.")
NAMES = {"searxng": "SearXNG", "brave": "Brave Search", "tavily": "Tavily"}


def _key(name: str) -> str:
    return decrypt(db.get_setting(f"secret:{name}", "") or "") or os.getenv(name, "")


def providers() -> list[str]:
    out = []
    if config.SEARXNG_URL:
        out.append("searxng")
    if _key("BRAVE_API_KEY"):
        out.append("brave")
    if _key("TAVILY_API_KEY"):
        out.append("tavily")
    return out


def provider() -> str:
    p = providers()
    return p[0] if p else ""


def _explain(p: str, status: int, text: str) -> str:
    name, low = NAMES[p], (text or "").lower()
    # Brave answers a bad token with 422 SUBSCRIPTION_TOKEN_INVALID rather than 401
    if status in (401, 403) or (status == 422 and "token_invalid" in low) or ("invalid" in low and ("api key" in low or "token" in low)):
        return f"{name} rejected the key. Paste it again in Settings, under Web search."
    if status in (402, 429, 432, 433):  # Tavily uses 432/433 for plan and usage limits
        return f"{name} has no searches left right now (free monthly allowance used up, or rate limited)."
    if status == 0:
        return f"Can't reach {name}" + (" (is the search add-on running?)" if p == "searxng" else "") + f": {text[:120]}"
    return f"{name} search failed ({status}): {text[:160]}"


async def _search_one(c: httpx.AsyncClient, p: str, query: str, count: int) -> list[dict]:
    if p == "searxng":
        r = await c.get(f"{config.SEARXNG_URL}/search", params={"q": query, "format": "json"})
        r.raise_for_status()
        return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
                for i in r.json().get("results", [])]
    if p == "brave":
        r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": min(count, 20)},
                        headers={"X-Subscription-Token": _key("BRAVE_API_KEY"), "Accept": "application/json"})
        r.raise_for_status()
        return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("description", "")}
                for i in (r.json().get("web") or {}).get("results", [])]
    r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": min(count, 20)},
                     headers={"Authorization": f"Bearer {_key('TAVILY_API_KEY')}"})
    r.raise_for_status()
    return [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": i.get("content", "")}
            for i in r.json().get("results", [])]


async def web_search_all(query: str, count: int = 8) -> list[dict]:
    """Ask every configured provider at once and merge the results (each tagged with its provider), so research is
    not limited to one index. Fails only when every provider fails."""
    order = providers()
    if not order:
        raise HTTPException(400, NOT_SET_UP)
    async with httpx.AsyncClient(timeout=20) as c:
        results = await asyncio.gather(*(_search_one(c, p, query, count) for p in order), return_exceptions=True)
    merged, problems = [], []
    for p, r in zip(order, results):
        if isinstance(r, httpx.HTTPStatusError):
            problems.append(_explain(p, r.response.status_code, r.response.text))
        elif isinstance(r, (httpx.HTTPError, ValueError)):
            problems.append(_explain(p, 0, str(r) or r.__class__.__name__))
        elif isinstance(r, BaseException):
            problems.append(f"{NAMES[p]} search failed: {r}")
        else:
            merged.extend({**i, "provider": p} for i in r[:count] if i["url"].startswith(("http://", "https://")))
    if not merged and problems:
        raise HTTPException(502, " ".join(problems))
    return merged


async def web_search(query: str, count: int = 6, only: str = "") -> list[dict]:
    order = [only] if only else providers()
    if not order:
        raise HTTPException(400, NOT_SET_UP)
    problems = []
    async with httpx.AsyncClient(timeout=20) as c:
        for p in order:
            try:
                items = await _search_one(c, p, query, count)
            except httpx.HTTPStatusError as e:
                problems.append(_explain(p, e.response.status_code, e.response.text))
                continue
            except (httpx.HTTPError, ValueError) as e:
                problems.append(_explain(p, 0, str(e) or e.__class__.__name__))
                continue
            return [i for i in items if i["url"].startswith(("http://", "https://"))][:count]
    raise HTTPException(502, " ".join(problems))
