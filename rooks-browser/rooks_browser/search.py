"""Web search: your SearXNG if you have one (AI Workspace's `search` add-on, or any instance), else a search page in the
browser itself (DuckDuckGo's HTML page by default), so search needs no API key and no extra service.

SearXNG is found from SEARXNG_URL, then http://localhost:8888 and http://localhost:8080 (a quick probe). Results are
title, url and snippet. Both paths are in the receipt: SearXNG as an egress row (tool browser.search), the browser
fallback as a normal page visit.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request

from .receipts import RunLog

FALLBACK = "https://html.duckduckgo.com/html/?q={q}"
_PROBES = ("http://localhost:8888", "http://localhost:8080")


def searxng_url(probe: bool = True) -> str:
    u = (os.getenv("SEARXNG_URL") or "").rstrip("/")
    if u or not probe or os.getenv("ROOKS_BROWSER_NO_PROBE"):
        return u
    for cand in _PROBES:
        try:
            with urllib.request.urlopen(f"{cand}/search?q=test&format=json", timeout=1.0) as r:
                if "results" in json.loads(r.read(200000).decode("utf-8", "replace")):
                    return cand
        except (OSError, ValueError):
            continue
    return ""


def searxng(base: str, query: str, log: RunLog | None = None, parent: int | None = None, count: int = 8) -> list[dict]:
    url = f"{base.rstrip('/')}/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "rooks-browser/0.1"}), timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        if log:
            log.egress("GET", url, "error", int((time.monotonic() - started) * 1000), parent, tool="search", error=e.__class__.__name__)
        raise RuntimeError(f"SearXNG at {base} failed: {e}") from e
    if log:
        log.egress("GET", url, "ok", int((time.monotonic() - started) * 1000), parent, tool="search")
    items = [{"title": i.get("title", ""), "url": i.get("url", ""), "snippet": (i.get("content") or "")[:240]}
             for i in data.get("results", [])]
    return [i for i in items if i["url"].startswith(("http://", "https://"))][:count]


# Read result links out of the fallback search page (DuckDuckGo HTML, Bing, Brave, or a generic page).
RESULTS_JS = r"""
(count) => {
  const clean = s => (s || '').replace(/\s+/g, ' ').trim();
  const out = [];
  const seen = new Set();
  const push = (a, snip) => {
    let href = a.href;
    try { const u = new URL(href); const real = u.searchParams.get('uddg'); if (real) href = real; } catch (e) {}
    if (!/^https?:/.test(href) || seen.has(href)) return;
    if (/duckduckgo\.com|bing\.com\/(?:search|ck)|\/\/search\.brave\.com/.test(href) && !/uddg=/.test(a.href)) return;
    seen.add(href); out.push({ title: clean(a.innerText).slice(0, 160), url: href, snippet: clean(snip).slice(0, 240) });
  };
  const blocks = document.querySelectorAll('.result, li.b_algo, #b_results > li, [data-type=web], .snippet');
  for (const b of blocks) {
    const a = b.querySelector('a.result__a, h2 a, a[href]');
    if (a) push(a, (b.querySelector('.result__snippet, .b_caption p, .snippet-description') || {}).innerText || '');
    if (out.length >= count) break;
  }
  if (!out.length) for (const a of document.querySelectorAll('main a[href], a[href]')) {
    if (clean(a.innerText).length > 12) push(a, '');
    if (out.length >= count) break;
  }
  return out;
}
"""


def fallback_url(query: str) -> str:
    tmpl = os.getenv("ROOKS_BROWSER_SEARCH_URL") or FALLBACK
    return tmpl.replace("{q}", urllib.parse.quote_plus(query))


def format_results(results: list[dict], source: str) -> str:
    if not results:
        return f"no results ({source})"
    lines = [f"{len(results)} results ({source}); open one with the open action:"]
    for n, r in enumerate(results, 1):
        lines.append(f"{n}. {r['title']} — {r['url']}" + (f"\n   {r['snippet']}" if r.get("snippet") else ""))
    return "\n".join(lines)
