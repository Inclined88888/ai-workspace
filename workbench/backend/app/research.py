"""Deep research: plan queries, search SearXNG, read pages, write a cited report."""

import asyncio
import ipaddress
import json
import socket
from urllib.parse import urlparse

import httpx
import trafilatura
from fastapi import HTTPException

from . import db, llm, search

MAX_SOURCES = 8
PAGE_CHARS = 6000
UA = "Mozilla/5.0 (X11; Linux x86_64) AIWorkspace/1.0 (+self-hosted research)"


async def web_search(query: str, count: int = 6) -> list[dict]:
    return await search.web_search(query, count)


def _public_host(url: str) -> bool:
    """Refuse to fetch internal addresses (other containers, metadata endpoints, LAN)."""
    host = urlparse(url).hostname
    if not host:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    if not await asyncio.to_thread(_public_host, url):
        return ""
    try:
        r = await client.get(url, follow_redirects=False)
        hops = 0
        while r.is_redirect and hops < 4:
            nxt = str(r.next_request.url) if r.next_request else ""
            if not nxt or not await asyncio.to_thread(_public_host, nxt):
                return ""
            r = await client.get(nxt, follow_redirects=False)
            hops += 1
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return ""
        html = r.text[:2_000_000]
    except httpx.HTTPError:
        return ""
    text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False, include_tables=True)
    return (text or "")[:PAGE_CHARS]


def _update(rid: int, **fields) -> None:
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.run(f"UPDATE research SET {cols} WHERE id = ?", *fields.values(), rid)


async def run(rid: int, question: str, model: str, depth: int) -> None:
    try:
        _update(rid, progress="Planning search queries")
        plan = await llm.complete(model, [
            {"role": "system", "content": "You plan web research. Reply with only a JSON array of strings."},
            {"role": "user", "content": f"Write {depth + 1} distinct, specific web search queries that together answer:\n{question}"},
        ])
        queries = [q for q in (llm.parse_json(plan) or []) if isinstance(q, str)][: depth + 1] or [question]

        seen: dict[str, dict] = {}
        for i, q in enumerate(queries, 1):
            _update(rid, progress=f"Searching ({i}/{len(queries)}): {q}")
            try:
                for hit in await web_search(q):
                    seen.setdefault(hit["url"], hit)
            except (httpx.HTTPError, HTTPException) as e:
                _update(rid, progress=f"Search failed for '{q}': {getattr(e, 'detail', e)}")
        candidates = list(seen.values())[: MAX_SOURCES * 2]
        if not candidates:
            raise RuntimeError("Web search returned no results. Check the search setup in Settings.")

        _update(rid, progress=f"Reading {len(candidates)} pages")
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
            texts = await asyncio.gather(*(fetch_text(client, c["url"]) for c in candidates))
        sources = []
        for c, text in zip(candidates, texts):
            body = text or c["snippet"]
            if body.strip():
                sources.append({**c, "text": body})
            if len(sources) >= MAX_SOURCES:
                break
        if not sources:
            raise RuntimeError("None of the result pages could be read.")

        _update(rid, progress="Writing the report", sources=json.dumps([{k: s[k] for k in ("title", "url", "snippet")} for s in sources]))
        context = "\n\n".join(f"[{i}] {s['title']}\nURL: {s['url']}\n{s['text']}" for i, s in enumerate(sources, 1))
        report = await llm.complete(model, [
            {"role": "system", "content": (
                "You are a careful research analyst. Answer only from the numbered sources provided. "
                "Cite every factual claim inline with [n] using the source numbers. "
                "If sources disagree, say so. If the sources do not answer something, say that plainly instead of guessing. "
                "Write in Markdown: a short direct answer first, then sections with detail, then 'Open questions'. "
                "Do not add a reference list; it is appended automatically."
            )},
            {"role": "user", "content": f"Question: {question}\n\nSources:\n{context}"},
        ])
        refs = "\n".join(f"{i}. [{s['title'] or s['url']}]({s['url']})" for i, s in enumerate(sources, 1))
        _update(rid, status="done", progress="Done", report=f"{report}\n\n## Sources\n\n{refs}")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        detail = getattr(e, "detail", None) or str(e) or e.__class__.__name__
        _update(rid, status="error", error=str(detail), progress="Failed")
