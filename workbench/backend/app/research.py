"""Deep research: plan sub-questions, search every configured provider, read pages that allow it, score sources,
extract quoted claims, and write a structured report whose citations are checked by code."""

import asyncio
import json
import ipaddress
import re
import socket
import time
import urllib.robotparser
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
import trafilatura
from fastapi import HTTPException

from . import db, llm, search, usage

MAX_SOURCES = 8
MAX_READ = 14
PAGE_CHARS = 6000
# Honest identification: ROOKS does not pretend to be a browser. Sites that refuse it are skipped, not worked around.
UA = "ROOKS/1.0 (self-hosted research assistant; respects robots.txt)"
ROBOTS_AGENT = "ROOKS"


async def web_search(query: str, count: int = 6) -> list[dict]:
    return await search.web_search(query, count)


MAX_PAGE_BYTES = 2_000_000


def _blocked_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped  # ::ffff:127.0.0.1 is still loopback
    # is_global excludes private, loopback, link-local (cloud metadata 169.254.169.254), shared CGNAT and reserved ranges
    return not ip.is_global or ip.is_multicast


def _public_host(url: str) -> bool:
    """Refuse to fetch internal addresses (other containers, metadata endpoints, LAN)."""
    u = urlparse(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        return False
    try:
        infos = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "https" else 80))
    except (socket.gaierror, UnicodeError, ValueError):
        return False
    return bool(infos) and not any(_blocked_ip(info[4][0]) for info in infos)


def _peer_ok(r: httpx.Response) -> bool:
    """Check the address the connection actually reached, so DNS that changes between the check and the connect
    (rebinding) cannot point a fetch at an internal service. Through an outbound proxy the peer is the proxy, and the
    name check above is what applies."""
    stream = r.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream else None
    if not peer:
        return True
    if _proxied(r.request.url.scheme):
        return True
    return not _blocked_ip(str(peer[0]))


def _proxied(scheme: str) -> bool:
    import urllib.request

    found = urllib.request.getproxies()
    return bool(found.get(scheme) or found.get("all"))


async def _fetch(client: httpx.AsyncClient, url: str, kinds: tuple[str, ...] = ("html",)) -> tuple[str, str]:
    """(body, final url) of a public page whose content type matches, or ('', '') when it is not public, not that
    type, too slow or not reachable. Every redirect is checked again, and so is the address actually connected to."""
    try:
        for _ in range(5):  # the first request plus up to 4 redirects
            if not await asyncio.to_thread(_public_host, url):
                return "", ""
            async with client.stream("GET", url, follow_redirects=False) as r:
                if not _peer_ok(r):
                    return "", ""
                if r.is_redirect:
                    url = str(r.next_request.url) if r.next_request else ""
                    if not url:
                        return "", ""
                    continue
                if r.status_code != 200 or not any(k in r.headers.get("content-type", "") for k in kinds):
                    return "", ""
                if int(r.headers.get("content-length") or 0) > MAX_PAGE_BYTES * 4:
                    return "", ""
                body = bytearray()
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_PAGE_BYTES:
                        break
                return bytes(body[:MAX_PAGE_BYTES]).decode(r.encoding or "utf-8", errors="replace"), url
        return "", ""
    except (httpx.HTTPError, LookupError):
        return "", ""


async def fetch_text(client: httpx.AsyncClient, url: str) -> str:
    """Text of a public HTML page, or '' when it is not public, not HTML, too slow or not reachable."""
    html, _ = await _fetch(client, url)
    if not html:
        return ""
    text = await asyncio.to_thread(trafilatura.extract, html, include_comments=False, include_tables=True)
    return (text or "")[:PAGE_CHARS]


async def fetch_page(client: httpx.AsyncClient, url: str) -> dict:
    """Text plus what the page says about itself (title, site, author, date)."""
    html, final = await _fetch(client, url)
    if not html:
        return {}
    doc = await asyncio.to_thread(trafilatura.bare_extraction, html, include_comments=False, include_tables=True, with_metadata=True)
    if doc is None:
        return {}
    get = (lambda k: doc.get(k)) if isinstance(doc, dict) else (lambda k: getattr(doc, k, None))
    text = (get("text") or "").strip()
    if not text:
        return {}
    return {"text": text[:PAGE_CHARS], "title": get("title") or "", "site": get("sitename") or "", "author": get("author") or "",
            "date": (get("date") or "")[:10], "final_url": final}


class Robots:
    """robots.txt per site, fetched through the same address checks. A site whose robots.txt cannot be read is
    treated as allowing access, as the robots standard says; one that disallows ROOKS is skipped."""

    def __init__(self, client: httpx.AsyncClient):
        self.client, self.cache = client, {}

    async def allowed(self, url: str) -> bool:
        u = urlparse(url)
        site = f"{u.scheme}://{u.netloc}"
        if site not in self.cache:
            body, _ = await _fetch(self.client, site + "/robots.txt", ("text/plain", "text"))
            rp = urllib.robotparser.RobotFileParser()
            rp.parse(body.splitlines() if body else [])
            self.cache[site] = rp
        return self.cache[site].can_fetch(ROBOTS_AGENT, url)


# Source assessment. Heuristics, shown to the user as such; they rank sources, they do not prove anything.
HIGHER = ("gov", "edu", "mil", "int")
HIGHER_SUFFIX = (".gov.", ".edu.", ".ac.", ".govt.")
HIGHER_SITES = {"who.int", "un.org", "europa.eu", "oecd.org", "worldbank.org", "imf.org", "nature.com", "science.org",
                "thelancet.com", "nejm.org", "bmj.com", "abs.gov.au", "ato.gov.au", "ieee.org", "acm.org", "w3.org", "ietf.org"}
LOWER_SITES = {"reddit.com", "quora.com", "medium.com", "pinterest.com", "facebook.com", "x.com", "twitter.com", "tiktok.com",
               "instagram.com", "youtube.com", "linkedin.com"}
LOWER_SUFFIX = (".blogspot.com", ".wordpress.com", ".substack.com", ".tumblr.com")
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_src|igshid|si$)")


def canonical(url: str) -> str:
    u = urlparse(url.strip())
    host = (u.hostname or "").lower().removeprefix("www.")
    query = urlencode([(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not TRACKING.match(k)])
    path = u.path.rstrip("/") or "/"
    return urlunparse((u.scheme.lower(), host + (f":{u.port}" if u.port else ""), path, "", query, ""))


def domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def credibility(url: str) -> tuple[str, str]:
    d = domain(url)
    tld = d.rsplit(".", 1)[-1]
    if d in HIGHER_SITES or any(d.endswith("." + s) for s in HIGHER_SITES) or tld in HIGHER or any(x in "." + d + "." for x in HIGHER_SUFFIX):
        return "higher", "government, education or a recognised institution"
    if d in LOWER_SITES or any(d.endswith("." + s) for s in LOWER_SITES) or d.endswith(LOWER_SUFFIX):
        return "lower", "social media, forum or self-published platform"
    if d.endswith("wikipedia.org"):
        return "medium", "encyclopedia; check its own sources"
    return "medium", "no strong signal either way"


def freshness(date: str, now: datetime | None = None) -> tuple[str, int | None]:
    try:
        d = datetime.strptime(date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return "unknown", None
    days = ((now or datetime.now(timezone.utc)) - d).days
    return ("recent" if days <= 365 else "within 3 years" if days <= 3 * 365 else "older"), days


def _stem(w: str) -> str:
    """Light suffix stripping so 'domains', 'examples' and 'used' match 'domain', 'example' and 'use'."""
    for suf in ("ing", "es", "ed", "s", "e"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def _words(text: str) -> set[str]:
    from .memory import tokens

    return {_stem(t) for t in tokens(text)}


def relevance(question: str, text: str) -> float:
    q = _words(question)
    if not q:
        return 0.0
    t = _words(text[:4000])
    return round(len(q & t) / len(q), 3)


def _shingles(text: str, n: int = 5) -> set[str]:
    w = re.findall(r"\w+", text.lower())[:1500]
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def near_duplicate(a: str, b: str) -> bool:
    sa, sb = _shingles(a), _shingles(b)
    return bool(sa and sb) and len(sa & sb) / min(len(sa), len(sb)) > 0.6


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s%.$-]", "", text.lower())).strip()


def check_citations(report: str, n_sources: int) -> tuple[str, dict]:
    """Remove citations to sources that do not exist and count what was cited. Returns (report, stats)."""
    cited: set[int] = set()
    invalid = 0

    def fix(m: re.Match) -> str:
        nonlocal invalid
        keep = []
        for part in re.split(r"\s*,\s*", m.group(1)):
            if part.isdigit() and 1 <= int(part) <= n_sources:
                keep.append(part)
                cited.add(int(part))
            else:
                invalid += 1
        return f"[{', '.join(keep)}]" if keep else ""

    report = re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", fix, report)
    findings = re.search(r"## Key findings\n(.*?)(?=\n## |\Z)", report, re.S)
    unsourced = 0
    if findings:
        lines = findings.group(1).split("\n")
        for i, line in enumerate(lines):
            if line.lstrip().startswith(("-", "*")) and not re.search(r"\[\d", line):
                lines[i] = line + " *(no source given)*"
                unsourced += 1
        report = report.replace(findings.group(1), "\n".join(lines), 1)
    return report, {"cited": sorted(cited), "invalid_removed": invalid, "unsourced_findings": unsourced}


PLAN_PROMPT = (
    "You plan web research. Split the question into {n} focused sub-questions that together answer it, each with 1 or "
    "2 specific web search queries. Reply with only JSON: "
    '{{"sub_questions": [{{"q": "...", "queries": ["...", "..."]}}]}}'
)
CLAIMS_PROMPT = (
    "Extract up to 6 facts from this source that help answer the question. Each needs an exact quote copied from the "
    "source text (a short phrase is enough) that states it. Do not include anything the text does not say. Reply with "
    'only JSON: [{"claim": "...", "quote": "...", "kind": "fact|figure|opinion|forecast"}]'
)
REPORT_PROMPT = (
    "You are a careful research analyst. Use only the numbered sources and the claims extracted from them. Cite every "
    "factual statement with [n]. Write Markdown with exactly these sections:\n"
    "## Answer\nTwo to four sentences that directly answer the question, with citations.\n"
    "## Key findings\nBullet points, each with citations.\n"
    "## Where sources disagree\nConflicting figures or claims with both citations, or 'No conflicts found in these sources.'\n"
    "## Inferences\nConclusions you draw that no source states directly, each labelled as an inference. Or 'None.'\n"
    "## Open questions\nWhat the sources do not answer.\n"
    "Prefer recent and higher credibility sources when they conflict, and say so. Do not add a source list."
)


def _update(rid: int, **fields) -> None:
    for k in ("sources", "plan", "claims", "stats"):
        if k in fields and not isinstance(fields[k], str):
            fields[k] = json.dumps(fields[k])
    fields["updated_at"] = db.now()
    cols = ", ".join(f"{k} = ?" for k in fields)
    db.run(f"UPDATE research SET {cols} WHERE id = ?", *fields.values(), rid)


class Cancelled(Exception):
    pass


def _check_cancel(rid: int) -> None:
    r = db.one("SELECT cancel FROM research WHERE id = ?", rid)
    if not r or r["cancel"]:
        raise Cancelled()


async def _llm(model: str, rid: int, system: str, user: str) -> str:
    res = await llm.chat(model, [{"role": "system", "content": system}, {"role": "user", "content": user}])
    usage.record(res, channel="research")
    return res["message"].get("content") or ""


async def run(rid: int, question: str, model: str, depth: int) -> None:
    started = time.monotonic()
    try:
        # 1. Plan
        _update(rid, progress="Planning the research")
        n = max(2, min(depth + 1, 6))
        plan_raw = llm.parse_json(await _llm(model, rid, PLAN_PROMPT.format(n=n), question))
        subs = []
        for s in (plan_raw.get("sub_questions") if isinstance(plan_raw, dict) else None) or []:
            if isinstance(s, dict) and isinstance(s.get("q"), str):
                qs = [q for q in s.get("queries") or [] if isinstance(q, str) and q.strip()][:2] or [s["q"]]
                subs.append({"q": s["q"][:300], "queries": [q[:200] for q in qs]})
        subs = subs[:n] or [{"q": question, "queries": [question]}]
        _update(rid, plan=subs)
        _check_cancel(rid)

        # 2. Search every configured provider, de-duplicating by canonical URL
        found: dict[str, dict] = {}
        queries = [(i, q) for i, s in enumerate(subs) for q in s["queries"]]
        for k, (si, q) in enumerate(queries, 1):
            _update(rid, progress=f"Searching ({k}/{len(queries)}): {q}")
            try:
                hits = await search.web_search_all(q, 8)
            except HTTPException as e:
                _update(rid, progress=f"Search failed for '{q}': {e.detail}")
                continue
            for h in hits:
                c = canonical(h["url"])
                entry = found.setdefault(c, {**h, "url": h["url"], "found_by": [], "subs": set()})
                entry["found_by"].append(f"{h.get('provider', '?')}: {q}")
                entry["subs"].add(si)
            _check_cancel(rid)
        if not found:
            raise RuntimeError("Web search returned no results. Check the search setup in Settings.")

        # 3. Choose what to read: relevant first, at most 3 pages per site
        ranked = sorted(found.values(), key=lambda h: (-(relevance(question + " " + " ".join(subs[i]["q"] for i in h["subs"]),
                                                                     h["title"] + " " + h["snippet"])), len(h["found_by"]) * -1))
        per_site: dict[str, int] = {}
        to_read = []
        for h in ranked:
            d = domain(h["url"])
            if per_site.get(d, 0) < 3:
                per_site[d] = per_site.get(d, 0) + 1
                to_read.append(h)
            if len(to_read) >= MAX_READ:
                break

        # 4. Read, honouring robots.txt
        skipped: list[dict] = []
        pages: list[dict] = []
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA}) as client:
            robots = Robots(client)
            sem = asyncio.Semaphore(4)

            async def read(h: dict) -> None:
                async with sem:
                    if not await robots.allowed(h["url"]):
                        skipped.append({"url": h["url"], "title": h["title"], "why": "the site's robots.txt does not allow it"})
                        return
                    page = await fetch_page(client, h["url"])
                    if page:
                        pages.append({**h, **page, "title": page["title"] or h["title"]})
                    else:
                        skipped.append({"url": h["url"], "title": h["title"], "why": "could not be read (blocked, not a web page, or private address)"})

            _update(rid, progress=f"Reading {len(to_read)} pages")
            await asyncio.gather(*(read(h) for h in to_read))
        _check_cancel(rid)

        # 5. Score, fold duplicate content, keep the best few with at most 2 per site
        now = datetime.now(timezone.utc)
        for p in pages:
            p["relevance"] = relevance(question + " " + " ".join(subs[i]["q"] for i in p["subs"]), p["text"])
            p["credibility"], p["credibility_why"] = credibility(p["url"])
            p["freshness"], p["age_days"] = freshness(p["date"], now)
            p["score"] = round(p["relevance"] * 0.6 + {"higher": 0.3, "medium": 0.15, "lower": 0.0}[p["credibility"]]
                               + {"recent": 0.1, "within 3 years": 0.06, "older": 0.0, "unknown": 0.03}[p["freshness"]], 3)
        pages.sort(key=lambda p: -p["score"])
        sources: list[dict] = []
        per_site = {}
        for p in pages:
            dup = next((s for s in sources if near_duplicate(p["text"], s["text"])), None)
            if dup:
                dup.setdefault("duplicates", []).append(p["url"])
                continue
            d = domain(p["url"])
            if per_site.get(d, 0) >= 2 or p["relevance"] == 0:
                continue
            per_site[d] = per_site.get(d, 0) + 1
            sources.append(p)
            if len(sources) >= MAX_SOURCES:
                break
        if not sources:
            raise RuntimeError("None of the result pages could be read" + (f" ({len(skipped)} skipped; see the list)." if skipped else "."))

        def public(s: dict, i: int) -> dict:
            return {"n": i, "title": s["title"], "url": s["url"], "site": s.get("site") or domain(s["url"]), "date": s.get("date") or "",
                    "author": s.get("author") or "", "snippet": s.get("snippet", "")[:300], "credibility": s["credibility"],
                    "credibility_why": s["credibility_why"], "freshness": s["freshness"], "relevance": s["relevance"], "score": s["score"],
                    "found_by": s["found_by"][:4], "duplicates": s.get("duplicates", [])[:4], "claims": s.get("claims", [])}

        _update(rid, sources=[public(s, i) for i, s in enumerate(sources, 1)], skipped=json.dumps(skipped))

        # 6. Claims with quotes, kept only when the quote really is in the page
        _update(rid, progress=f"Extracting claims from {len(sources)} sources")
        sem = asyncio.Semaphore(3)

        async def claims_for(i: int, s: dict) -> None:
            async with sem:
                try:
                    raw = llm.parse_json(await _llm(model, rid, CLAIMS_PROMPT, f"Question: {question}\n\nSource [{i}] {s['title']}\n{s['text']}"))
                except Exception:  # noqa: BLE001 - one source failing must not sink the report
                    raw = []
                text = _norm(s["text"])
                kept = []
                for c in raw if isinstance(raw, list) else []:
                    if isinstance(c, dict) and isinstance(c.get("claim"), str) and isinstance(c.get("quote"), str):
                        q = _norm(c["quote"])
                        if len(q) >= 8 and q in text:
                            kept.append({"claim": c["claim"][:400], "quote": c["quote"][:300], "kind": str(c.get("kind", "fact"))[:20]})
                s["claims"] = kept[:6]
                s["claims_dropped"] = len(raw if isinstance(raw, list) else []) - len(kept)

        await asyncio.gather(*(claims_for(i, s) for i, s in enumerate(sources, 1)))
        _check_cancel(rid)
        dropped = sum(s.get("claims_dropped", 0) for s in sources)
        _update(rid, sources=[public(s, i) for i, s in enumerate(sources, 1)])

        # 7. Report, then check every citation
        _update(rid, progress="Writing the report")
        ctx = []
        for i, s in enumerate(sources, 1):
            claims = "\n".join(f"  - ({c['kind']}) {c['claim']} [quote: \"{c['quote']}\"]" for c in s["claims"]) or "  (no quoted claims; use the text)"
            ctx.append(f"[{i}] {s['title']} | {s.get('site') or domain(s['url'])} | date: {s.get('date') or 'unknown'} | "
                       f"credibility: {s['credibility']}\nClaims:\n{claims}\nText:\n{s['text'][:2500]}")
        sub_list = "\n".join(f"- {s['q']}" for s in subs)
        report = await _llm(model, rid, REPORT_PROMPT, f"Question: {question}\n\nSub-questions:\n{sub_list}\n\nSources:\n\n" + "\n\n".join(ctx))
        report, stats = check_citations(report, len(sources))
        uncited = [i for i in range(1, len(sources) + 1) if i not in stats["cited"]]
        stats.update(sources_read=len(pages), sources_used=len(sources), skipped=len(skipped), claims=sum(len(s["claims"]) for s in sources),
                     claims_dropped_unquoted=dropped, uncited_sources=uncited, seconds=round(time.monotonic() - started))
        refs = "\n".join(f"{i}. [{s['title'] or s['url']}]({s['url']}) ({s.get('site') or domain(s['url'])}"
                         f"{', ' + s['date'] if s.get('date') else ''}; credibility {s['credibility']}; {s['freshness']})"
                         for i, s in enumerate(sources, 1))
        notes = []
        if stats["invalid_removed"]:
            notes.append(f"{stats['invalid_removed']} citation(s) to sources that do not exist were removed.")
        if stats["unsourced_findings"]:
            notes.append(f"{stats['unsourced_findings']} finding(s) have no source and are marked.")
        if dropped:
            notes.append(f"{dropped} extracted claim(s) were discarded because their quote was not found in the source.")
        checks = ("\n\n> Checks: " + " ".join(notes)) if notes else ""
        _update(rid, status="done", progress="Done", stats=stats, report=f"{report.strip()}{checks}\n\n## Sources\n\n{refs}")
    except Cancelled:
        _update(rid, status="cancelled", progress="Cancelled", error="Cancelled by you.")
    except Exception as e:  # noqa: BLE001 - surface any failure to the UI
        detail = getattr(e, "detail", None) or str(e) or e.__class__.__name__
        _update(rid, status="error", error=str(detail), progress="Failed")
