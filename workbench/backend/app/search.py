"""Web search without extra containers: Brave Search API, Tavily API, or a SearXNG instance if you run one.

Keys are set in Settings (stored encrypted) or by environment variable. The first configured one is used.
With none of them, DuckDuckGo's keyless HTML results page is used, so search works on a fresh install with no keys.
That page is meant for people, not programs: it can rate limit or change its markup at any time. SearXNG (the
search add-on) is the dependable keyless option.
"""

import os
from urllib.parse import parse_qs, urlparse

import httpx
import lxml.html
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
    if db.get_setting("search_fallback", "1") == "1":
        return "duckduckgo"
    return ""


DDG_URL = "https://html.duckduckgo.com/html/"
DDG_UA = "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"


def parse_duckduckgo(html: str) -> list[dict]:
    """Results from DuckDuckGo's HTML page. Links are wrapped as //duckduckgo.com/l/?uddg=<target>."""
    if not html.strip():
        return []
    doc = lxml.html.fromstring(html)
    items = []
    for res in doc.xpath('//div[contains(concat(" ", normalize-space(@class), " "), " result ")]'):
        link = res.xpath('.//a[contains(@class, "result__a")]')
        if not link:
            continue
        href = link[0].get("href", "")
        if "uddg=" in href:
            href = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        if "duckduckgo.com/y.js" in href or "ad_provider" in href:  # ads
            continue
        snippet = res.xpath('.//*[contains(@class, "result__snippet")]')
        items.append({"title": link[0].text_content().strip(), "url": href,
                      "snippet": snippet[0].text_content().strip() if snippet else ""})
    return items


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
        elif p == "duckduckgo":
            r = await c.post(DDG_URL, data={"q": query}, headers={"User-Agent": DDG_UA})
            if r.status_code != 200:
                raise HTTPException(502, f"DuckDuckGo refused the search (HTTP {r.status_code}, often a rate limit). "
                                         "For dependable keyless search turn on the search add-on (SearXNG), or add a Brave or Tavily key.")
            items = parse_duckduckgo(r.text)
            if not items and "result" not in r.text:
                raise HTTPException(502, "DuckDuckGo returned no readable results (blocked or its page changed). "
                                         "Turn on the search add-on (SearXNG), or add a Brave or Tavily key.")
        else:
            raise HTTPException(400, "Web search is not set up. Add a Brave Search or Tavily API key in Settings, "
                                     "or run the optional SearXNG add-on.")
    return [i for i in items if i["url"].startswith(("http://", "https://"))][:count]
