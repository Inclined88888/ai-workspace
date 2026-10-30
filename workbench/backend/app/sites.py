"""Work sites: any website the user works in (job trackers, rosters, project portals, CRMs, ticket queues, school or
research portals...). The user adds a URL and signs in once in Rooks Browser's own profile; the assistant then learns the
site by itself, with no per-site code:

1. discover: crawl the site's own navigation (same site only, never logout/delete links), and on each page find tables,
   repeated cards or list items, label/value detail panels, forms, and downloadable files;
2. understand: name each collection from the page ("Jobs", "Shifts", "Invoices"...) and give each column a role
   (id, title, status, start, due, end, person, amount, location) from its header words and its values;
3. track: store normalised records, compare with the last check and write a plain-English digest of what changed;
4. plan: records with dates become a timeline (program) and calendar events or an .ics file;
5. act: filling a form the site offers is proposed, shown to the user and only sent after they approve it, every time.
   Unattended checks are read-only.

Recipes (RECIPES) are optional accelerators, e.g. an official API with a key the user creates. They never replace the
generic path. Credentials are never stored by Workbench: sign-in lives in the browser profile; an API key goes to the OS
keychain (or encrypted at rest when no keychain exists).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx
from fastapi import HTTPException

from . import config, db

log = logging.getLogger("sites")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'auto',          -- auto | browser | http | api
    recipe TEXT NOT NULL DEFAULT '',
    secret_enc TEXT NOT NULL DEFAULT '',        -- API key for a recipe that uses one (keychain ref or encrypted)
    max_pages INTEGER NOT NULL DEFAULT 30,
    status TEXT NOT NULL DEFAULT 'new',         -- new | needs_sign_in | ok | error
    status_detail TEXT NOT NULL DEFAULT '',
    structure TEXT NOT NULL DEFAULT '{}',
    last_checked TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_records (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    collection TEXT NOT NULL,
    key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    start TEXT NOT NULL DEFAULT '',
    due TEXT NOT NULL DEFAULT '',
    finish TEXT NOT NULL DEFAULT '',
    person TEXT NOT NULL DEFAULT '',
    amount TEXT NOT NULL DEFAULT '',
    location TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    fields TEXT NOT NULL DEFAULT '{}',
    hash TEXT NOT NULL DEFAULT '',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    removed_at TEXT,
    UNIQUE(site_id, collection, key)
);
CREATE TABLE IF NOT EXISTS site_checks (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    at TEXT NOT NULL,
    status TEXT NOT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    records INTEGER NOT NULL DEFAULT 0,
    changes TEXT NOT NULL DEFAULT '{}',
    digest TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS site_watches (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    request TEXT NOT NULL,
    cron TEXT NOT NULL,
    rule TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run TEXT,
    last_run TEXT,
    last_result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_actions (
    id INTEGER PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
    page_url TEXT NOT NULL,
    form TEXT NOT NULL,
    vals TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',     -- pending | done | denied | failed
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT
);
"""
_ready: set[str] = set()


def ensure() -> None:
    if str(config.DB_PATH) not in _ready:
        db.conn().executescript(SCHEMA)
        _ready.add(str(config.DB_PATH))


DANGER = re.compile(r"log\s*-?out|sign\s*-?out|logoff|delete|remove|destroy|cancel|unsubscribe|archive|deactivate|reset|"
                    r"approve|reject|submit|pay\b|checkout|purchase", re.I)
FILE_EXT = re.compile(r"\.(pdf|docx?|xlsx?|xlsm|csv|pptx?|zip|dwg|dxf|png|jpe?g|txt|json|xml|ics|msg|eml|odt|ods|rtf|ipynb|parquet|h5|nc)(\?|$)", re.I)
DONE_WORDS = re.compile(r"\b(done|complete[d]?|closed|finished|resolved|paid|cancel+ed|invoiced|approved|delivered|archived|unsuccessful)\b", re.I)

ROLES = {
    "id": r"^(id|#|no\.?|num(ber)?|ref(erence)?|code|key|ticket|job\s*(no|#|id|number)|.*\b(id|no\.?|number|#)$)$",
    "status": r"status|state|stage|progress|phase|priority",
    "due": r"due|deadline|target|required by|expires?|expiry|\bby\b",
    "start": r"start|begin|scheduled|^date$|when|shift date|booked|appointment|from|opens?|day",
    "finish": r"\bend\b|finish|until|\bto\b|closes?|completion",
    "person": r"assign|owner|staff|employee|worker|technician|tech\b|manager|lead|who|person|teacher|supervisor|crew|team|analyst|reviewer|author|responsible|inspector|tutor|lawyer|solicitor|doctor",
    "client": r"client|customer|contact|company|account|patient|student|tenant|buyer|supplier|vendor",
    "amount": r"amount|total|price|cost|value|budget|balance|fee|rate|\$|hours?|qty|quantity",
    "location": r"address|location|site\b|suburb|city|room|venue|where|place",
    "title": r"title|name|subject|job|task|project|description|summary|item|activity|work|order|course|matter|case",
}
_ROLE_RE = {k: re.compile(v, re.I) for k, v in ROLES.items()}
DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{4}|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+\d{4})"
                     r"(?:[ T,]+\d{1,2}[:.]\d{2}(?:\s*[ap]m)?)?", re.I)
MONEY_RE = re.compile(r"^[-+]?\s*[$€£¥]\s?[\d,]+(\.\d+)?$|^[\d,]+(\.\d+)?\s?(AUD|USD|EUR|GBP|NZD)$")


# --- page analysis (pure: HTML in, structure out) -----------------------------------------------------------------------
def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def _sig(el) -> str:
    return f"{el.tag}.{' '.join(sorted((el.get('class') or '').split()))}"


def parse_date(s: str, dayfirst: bool = True) -> str:
    """ISO date (or datetime) from what a site shows; '' when it is not a date."""
    from dateutil import parser as dp

    s = _clean(s)
    m = DATE_RE.search(s)
    if not m:
        rel = s.lower()
        today = date.today()
        if rel in ("today",):
            return today.isoformat()
        if rel in ("tomorrow",):
            return (today + timedelta(days=1)).isoformat()
        if rel in ("yesterday",):
            return (today - timedelta(days=1)).isoformat()
        return ""
    try:
        d = dp.parse(m.group(0), dayfirst=dayfirst and not re.match(r"\d{4}-", m.group(0)))
    except (ValueError, OverflowError):
        return ""
    has_time = bool(re.search(r"\d[:.]\d{2}", m.group(0)))
    return d.isoformat(timespec="minutes") if has_time else d.date().isoformat()


def role_of(header: str, values: list[str]) -> str:
    h = _clean(header).lower()
    vals = [v for v in values if v][:30]
    datey = vals and sum(bool(parse_date(v)) for v in vals) >= 0.7 * len(vals)
    for role in ("id", "status", "due", "finish", "start", "person", "client", "amount", "location", "title"):
        if h and _ROLE_RE[role].search(h):
            if role in ("due", "start", "finish") and vals and not datey:
                continue
            if role == "amount" and vals and not all(re.search(r"\d", v) for v in vals):
                continue
            return role
    if datey:
        return "start"
    if len(vals) >= 2 and len(set(vals)) == len(vals) and all(re.match(r"^[A-Z]{1,6}[-#/]?\d{1,8}$", v.strip()) for v in vals):
        return "id"
    if vals and all(MONEY_RE.match(v.strip()) for v in vals):
        return "amount"
    if vals and len(set(v.lower() for v in vals)) <= max(2, len(vals) // 3) and all(len(v) < 25 for v in vals) and len(vals) >= 3:
        return "status"
    return ""


def analyse_page(url: str, html: str) -> dict:
    """Everything a person would notice on a page: what it is, where it links, its lists, details, forms and files."""
    import lxml.html

    try:
        doc = lxml.html.fromstring(html or "<html></html>")
    except Exception:  # noqa: BLE001
        doc = lxml.html.fromstring("<html></html>")
    for bad in doc.xpath("//script|//style|//noscript|//template"):
        bad.drop_tree()
    title = _clean(" ".join(doc.xpath("//title//text()"))) or _clean(" ".join(doc.xpath("(//h1)[1]//text()")))
    h1 = _clean(" ".join(doc.xpath("(//h1)[1]//text()")))
    links, files_ = [], []
    nav_nodes = set(doc.xpath("//nav//a|//header//a|//aside//a|//*[@role='navigation']//a|//*[contains(@class,'nav') or contains(@class,'menu') or contains(@class,'sidebar')]//a"))
    for a in doc.xpath("//a[@href]"):
        href = a.get("href", "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        absu = urldefrag(urljoin(url, href))[0]
        text = _clean(a.text_content()) or _clean(a.get("title", "")) or _clean(a.get("aria-label", ""))
        if FILE_EXT.search(urlsplit(absu).path) or a.get("download") is not None:
            files_.append({"url": absu, "name": text or urlsplit(absu).path.rsplit("/", 1)[-1]})
            continue
        links.append({"url": absu, "text": text[:80], "nav": a in nav_nodes})
    login = bool(doc.xpath("//input[@type='password']"))
    collections = _tables(doc, url) + _repeats(doc, url)
    details = _details(doc)
    forms = _forms(doc, url)
    heading = h1 or title
    for c in collections:
        c.setdefault("name", heading or "Items")
    return {"url": url, "title": title, "heading": h1, "login": login, "links": links, "files": files_, "collections": collections,
            "details": details, "forms": forms, "text": _clean(doc.text_content())[:4000]}


def _near_heading(el) -> str:
    cur = el
    for _ in range(6):
        prev = cur.getprevious()
        while prev is not None:
            if prev.tag in ("h1", "h2", "h3", "h4", "caption", "legend"):
                return _clean(prev.text_content())
            hs = prev.xpath(".//h1|.//h2|.//h3")
            if hs:
                return _clean(hs[-1].text_content())
            prev = prev.getprevious()
        cur = cur.getparent()
        if cur is None:
            break
    cap = el.xpath("./caption")
    return _clean(cap[0].text_content()) if cap else ""


def _tables(doc, url: str) -> list[dict]:
    out = []
    for t in doc.xpath("//table"):
        rows = t.xpath("./tr|./thead/tr|./tbody/tr|./tfoot/tr")
        if len(rows) < 2:
            continue
        head_cells = rows[0].xpath("./th|./td")
        headers = [_clean(c.text_content()) or f"Column {i + 1}" for i, c in enumerate(head_cells)]
        recs, links = [], []
        for r in rows[1:]:
            cells = r.xpath("./td|./th")
            if not cells or len(cells) < max(1, len(headers) // 2):
                continue
            vals = [_clean(c.text_content()) for c in cells]
            recs.append(dict(zip(headers, vals + [""] * (len(headers) - len(vals)))))
            a = r.xpath(".//a[@href]")
            links.append(urldefrag(urljoin(url, a[0].get("href")))[0] if a and not DANGER.search(_clean(a[0].text_content())) else "")
        if recs:
            out.append({"kind": "table", "name": _clean(" ".join(t.xpath("./caption//text()"))) or _near_heading(t) or None,
                        "columns": headers, "rows": recs, "row_links": links})
    return [{k: v for k, v in c.items() if v is not None} for c in out]


LABEL_VALUE = re.compile(r"^\s*([A-Za-z][\w /#&().-]{0,30}?)\s*[:：]\s*(.+?)\s*$")


def _item_fields(el) -> dict:
    """Label/value pairs inside one card or list item, found the ways sites usually show them."""
    f: dict[str, str] = {}
    for dt in el.xpath(".//dt"):
        dd = dt.getnext()
        if dd is not None and dd.tag == "dd":
            f[_clean(dt.text_content()).rstrip(":")] = _clean(dd.text_content())
    for lab in el.xpath(".//*[self::label or self::strong or self::b or self::th or contains(@class,'label') or contains(@class,'key')]"):
        t = _clean(lab.text_content())
        if t.endswith(":") and len(t) < 40:
            nxt = (lab.tail or "").strip() or (_clean(lab.getnext().text_content()) if lab.getnext() is not None else "")
            if nxt:
                f.setdefault(t.rstrip(":"), nxt)
    for node in el.xpath(".//*[@class or @data-field or @itemprop]"):
        if node.xpath(".//*[@class or @data-field or @itemprop]") and node.tag not in ("span", "time", "a"):
            continue
        name = node.get("data-field") or node.get("itemprop") or ""
        if not name:
            cls = [c for c in (node.get("class") or "").split() if not re.match(r"^(col|row|flex|text|font|p[xytblr]?-|m[xytblr]?-|w-|h-|badge$|chip$|pill$|tag$)", c)]
            name = cls[-1] if cls else ""
            if not name and re.search(r"badge|chip|pill|tag", node.get("class") or ""):
                name = "status"
        val = _clean(node.text_content())
        if name and val and len(val) < 300:
            key = re.sub(r"^(item|card|row|job|task|shift|project|record|entry)[-_]", "", name).replace("-", " ").replace("_", " ")
            if node.tag == "time" and node.get("datetime"):
                val = node.get("datetime")
            f.setdefault(key, val)
    for line in el.itertext():
        m = LABEL_VALUE.match(line)
        if m and len(m.group(2)) < 200:
            f.setdefault(m.group(1).strip(), m.group(2).strip())
    heads = el.xpath(".//h1|.//h2|.//h3|.//h4|.//h5|.//strong[not(contains(text(),':'))]|.//a[1]")
    if heads:
        t = _clean(heads[0].text_content())
        if t and not t.endswith(":"):
            f.setdefault("title", t)
    return f


def _repeats(doc, url: str) -> list[dict]:
    """Lists of cards or items: 3+ siblings with the same shape, each holding several facts."""
    out, used = [], set()
    for parent in doc.xpath("//*[count(*) >= 3]"):
        if parent.tag in ("table", "tbody", "thead", "tr", "select", "nav", "head", "html") or parent.xpath("ancestor::nav|ancestor::table|ancestor::header"):
            continue
        groups: dict[str, list] = {}
        for ch in parent:
            if isinstance(ch.tag, str):
                groups.setdefault(_sig(ch), []).append(ch)
        for sig, items in groups.items():
            if len(items) < 3 or any(i in used for i in items):
                continue
            recs, links = [], []
            for it in items:
                f = _item_fields(it)
                if len([v for v in f.values() if v]) < 2:
                    continue
                recs.append(f)
                a = it.xpath(".//a[@href]")
                links.append(urldefrag(urljoin(url, a[0].get("href")))[0] if a and not DANGER.search(_clean(a[0].text_content())) else "")
            if len(recs) >= 3 and len(recs) >= 0.6 * len(items):
                for it in items:
                    used.update(it.iter())
                cols = list(dict.fromkeys(k for r in recs for k in r))
                out.append({"kind": "list", "name": _near_heading(parent) or None, "columns": cols, "rows": recs, "row_links": links})
    return [{k: v for k, v in c.items() if v is not None} for c in out]


def _details(doc) -> dict:
    """A detail page: label/value pairs outside lists (dl, label: value, two-column tables)."""
    f: dict[str, str] = {}
    for dt in doc.xpath("//dl/dt"):
        dd = dt.getnext()
        if dd is not None and dd.tag == "dd":
            f[_clean(dt.text_content()).rstrip(":")] = _clean(dd.text_content())
    for tr in doc.xpath("//table/tr[count(*)=2]|//table/tbody/tr[count(*)=2]"):
        th, td = tr[0], tr[1]
        if th.tag == "th":
            f.setdefault(_clean(th.text_content()).rstrip(":"), _clean(td.text_content()))
    for lab in doc.xpath("//main//*[contains(@class,'label') or contains(@class,'field-name')]|//*[@id='details']//*[contains(@class,'label')]"):
        nxt = lab.getnext()
        if nxt is not None:
            f.setdefault(_clean(lab.text_content()).rstrip(":"), _clean(nxt.text_content()))
    return {k: v for k, v in f.items() if k and v and len(k) < 40}


def _forms(doc, url: str) -> list[dict]:
    out = []
    for i, form in enumerate(doc.xpath("//form")):
        fields = []
        for el in form.xpath(".//input|.//select|.//textarea"):
            name = el.get("name")
            typ = (el.get("type") or el.tag).lower()
            if not name or typ in ("submit", "button", "image", "reset"):
                continue
            label = ""
            if el.get("id"):
                lab = doc.xpath(f"//label[@for='{el.get('id')}']")
                label = _clean(lab[0].text_content()) if lab else ""
            if not label:
                par = el.getparent()
                label = _clean(par.text_content()) if par is not None and par.tag == "label" else ""
            opts = [{"value": o.get("value", _clean(o.text_content())), "label": _clean(o.text_content())} for o in el.xpath(".//option")]
            value = el.get("value", "") if el.tag != "textarea" else (el.text or "")
            if el.tag == "select":
                sel = el.xpath(".//option[@selected]")
                value = sel[0].get("value", _clean(sel[0].text_content())) if sel else (opts[0]["value"] if opts else "")
            fields.append({"name": name, "label": label or el.get("placeholder", "") or el.get("aria-label", "") or name, "type": typ,
                           "options": opts, "value": value, "required": el.get("required") is not None})
        if not fields:
            continue
        btn = form.xpath(".//button|.//input[@type='submit']")
        submit = _clean(btn[0].text_content() or btn[0].get("value", "")) if btn else "Submit"
        out.append({"index": i, "action": urljoin(url, form.get("action") or url), "method": (form.get("method") or "get").lower(),
                    "fields": fields, "submit": submit or "Submit", "name": _near_heading(form) or submit,
                    "login": any(f["type"] == "password" for f in fields), "search": len(fields) == 1 and fields[0]["type"] in ("search", "text") and form.get("method", "get").lower() == "get"})
    return out


# --- understanding: collections become typed records ------------------------------------------------------------------
def singular(name: str) -> str:
    n = _clean(re.sub(r"^(my|all|your|current|open|upcoming|active)\s+", "", name, flags=re.I))
    w = n.split(" ")[-1] if n else "item"
    if w.lower().endswith("ies"):
        w = w[:-3] + "y"
    elif w.lower().endswith(("ches", "shes", "sses", "xes")):
        w = w[:-2]
    elif w.lower().endswith("s") and not w.lower().endswith("ss"):
        w = w[:-1]
    return w.lower() or "item"


def schema_of(collection: dict) -> dict:
    roles: dict[str, str] = {}
    for c in collection["columns"]:
        r = role_of(c, [str(row.get(c, "")) for row in collection["rows"]])
        if r and r not in roles.values():
            roles[c] = r
        elif r == "start" and "due" not in roles.values() and _ROLE_RE["due"].search(c):
            roles[c] = "due"
    if "title" not in roles.values():
        for c in collection["columns"]:
            if c not in roles and any(len(str(r.get(c, ""))) > 3 for r in collection["rows"]):
                roles[c] = "title"
                break
    return roles


def normalise(collection: dict, page_url: str) -> list[dict]:
    roles = schema_of(collection)
    by_role = {v: k for k, v in roles.items()}
    recs, seen_keys = [], set()
    for i, row in enumerate(collection["rows"]):
        g = lambda role: _clean(str(row.get(by_role[role], ""))) if role in by_role else ""  # noqa: E731
        key = g("id") or " · ".join(x for x in (g("title"), g("start") or g("due")) if x) or hashlib.sha1(json.dumps(row, sort_keys=True).encode()).hexdigest()[:12]
        n = 2
        while key in seen_keys:  # same title twice (e.g. the same shift on two days): keep both
            key = f"{key.rsplit(' #', 1)[0]} #{n}"
            n += 1
        seen_keys.add(key)
        link = (collection.get("row_links") or [""] * len(collection["rows"]))[i] if i < len(collection.get("row_links") or []) else ""
        recs.append({"key": key[:200], "title": (g("title") or g("id") or key)[:300], "status": g("status")[:80],
                     "start": parse_date(g("start")) if g("start") else "", "due": parse_date(g("due")) if g("due") else "",
                     "finish": parse_date(g("finish")) if g("finish") else "", "person": (g("person") or g("client"))[:120], "amount": g("amount")[:40],
                     "location": g("location")[:200], "url": link or page_url, "fields": {k: str(v)[:500] for k, v in row.items()}})
    return recs


# --- fetching: Rooks Browser's persistent profile, or plain HTTP -----------------------------------------------------------
class HttpFetcher:
    """Plain HTTP with a cookie jar for the run: public sites, sites behind basic forms, and the offline test portals."""

    def __init__(self, site: dict):
        self.site = site
        self.client = httpx.AsyncClient(timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (Workbench site reader)"})

    async def page(self, url: str) -> tuple[str, int, str, str]:
        r = await self.client.get(url)
        ctype = r.headers.get("content-type", "")
        return str(r.url), r.status_code, r.text if "html" in ctype or not ctype else "", ctype

    async def submit(self, form: dict, values: dict) -> tuple[str, int, str]:
        data = {f["name"]: values.get(f["name"], f.get("value", "")) for f in form["fields"]}
        if form["method"] == "post":
            r = await self.client.post(form["action"], data=data)
        else:
            r = await self.client.get(form["action"], params=data)
        return str(r.url), r.status_code, r.text

    async def download(self, url: str) -> tuple[bytes, str]:
        r = await self.client.get(url)
        r.raise_for_status()
        cd = r.headers.get("content-disposition", "")
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
        return r.content, (m.group(1) if m else urlsplit(str(r.url)).path.rsplit("/", 1)[-1] or "download")

    async def close(self):
        await self.client.aclose()


class BrowserFetcher:
    """Rooks Browser with a persistent profile per site: the user signed in there once; Workbench never sees the password."""

    def __init__(self, site: dict):
        self.site = site
        self.profile = f"site-{site['id']}"
        self.h = {"Authorization": f"Bearer {config.BROWSER_API_KEY}"}

    async def _post(self, path: str, body: dict, timeout: float = 120) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(f"{config.BROWSER_URL}{path}", json={**body, "profile": self.profile}, headers=self.h)
        if r.status_code == 404:
            raise HTTPException(400, "This Rooks Browser is too old for work sites. Run the installer again to update it.")
        if r.status_code >= 400:
            raise HTTPException(r.status_code, r.json().get("detail", r.text))
        return r.json()

    async def page(self, url: str):
        d = await self._post("/api/site/page", {"url": url})
        return d["url"], d.get("status", 200), d.get("html", ""), d.get("content_type", "text/html")

    async def submit(self, form: dict, values: dict):
        d = await self._post("/api/site/submit", {"url": form.get("page_url") or form["action"], "form_index": form["index"],
                                                  "values": {f["name"]: values.get(f["name"], f.get("value", "")) for f in form["fields"]}})
        return d["url"], d.get("status", 200), d.get("html", "")

    async def download(self, url: str):
        import base64

        d = await self._post("/api/site/download", {"url": url}, 300)
        return base64.b64decode(d["data"]), d.get("name") or "download"

    async def close(self):
        pass


def fetcher(site: dict):
    mode = site["mode"]
    if mode == "browser" or (mode == "auto" and config.BROWSER_URL and config.BROWSER_API_KEY):
        if not (config.BROWSER_URL and config.BROWSER_API_KEY):
            raise HTTPException(400, "Rooks Browser is not running, so sites that need a sign-in cannot be read. Restart the app.")
        return BrowserFetcher(site)
    return HttpFetcher(site)


# --- recipes: optional accelerators ---------------------------------------------------------------------------------
RECIPES = {
    "servicem8": {"label": "ServiceM8 (official API)", "match": r"servicem8\.com", "kind": "api",
                  "base": "https://api.servicem8.com/api_1.0",
                  "collections": {"Jobs": "job.json", "Clients": "company.json", "Job activities": "jobactivity.json"},
                  "help": "In ServiceM8 open Settings → API Keys → Add API Key, then paste the key here. It is kept in your "
                          "computer's keychain. Without a key, the site is read through the browser like any other site."},
    "dayforce": {"label": "Dayforce", "match": r"dayforce(hcm)?\.com|ceridian", "kind": "browser",
                 "help": "Dayforce's API is only available to employers, so your schedule, timesheets and pay are read "
                         "through the browser after you sign in once."},
}


def recipe_for(url: str) -> str:
    for k, r in RECIPES.items():
        if re.search(r["match"], url, re.I):
            return k
    return ""


async def _api_collections(site: dict) -> list[dict]:
    from .security import decrypt

    r = RECIPES[site["recipe"]]
    key = decrypt(site["secret_enc"])
    if not key:
        raise HTTPException(400, "Add the API key for this site first.")
    base = (json.loads(site["structure"] or "{}").get("api_base") or r["base"]).rstrip("/")
    out = []
    async with httpx.AsyncClient(timeout=30) as c:
        for name, path in r["collections"].items():
            resp = await c.get(f"{base}/{path}", headers={"X-API-Key": key, "Accept": "application/json"})
            if resp.status_code == 401:
                raise HTTPException(400, "The site refused the API key. Check it was copied fully and is still active.")
            resp.raise_for_status()
            rows = [x for x in resp.json() if isinstance(x, dict) and str(x.get("active", 1)) != "0"]
            cols = list(dict.fromkeys(k for x in rows[:200] for k in x))
            out.append({"kind": "api", "name": name, "columns": cols, "rows": [{k: "" if v is None else str(v) for k, v in x.items()} for x in rows],
                        "row_links": [""] * len(rows), "page": f"{base}/{path}"})
    return out


# --- CRUD -------------------------------------------------------------------------------------------------------------
def create(name: str, url: str, mode: str = "auto", api_key: str = "", max_pages: int = 30) -> dict:
    ensure()
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).netloc:
        raise HTTPException(400, "That does not look like a web address. Paste the address from your browser's address bar.")
    recipe = recipe_for(url)
    sid = db.run("INSERT INTO sites(name, url, mode, recipe, max_pages, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (name or urlsplit(url).netloc).strip()[:120], url, mode if mode in ("auto", "browser", "http", "api") else "auto",
                 recipe, max(1, min(int(max_pages or 30), 200)), db.now())
    if api_key:
        set_key(sid, api_key)
    return get(sid)


def set_key(sid: int, api_key: str) -> dict:
    from . import keychain
    from .security import encrypt

    stored = keychain.put(f"site:{sid}", api_key) if keychain.enabled() else encrypt(api_key)
    db.run("UPDATE sites SET secret_enc = ?, mode = 'api' WHERE id = ?", stored, sid)
    return get(sid)


def get(sid: int) -> dict:
    ensure()
    s = db.one("SELECT * FROM sites WHERE id = ?", int(sid))
    if not s:
        raise HTTPException(404, f"No site #{sid}.")
    s["has_key"] = bool(s["secret_enc"])
    s["structure"] = json.loads(s["structure"] or "{}")
    s["recipe_info"] = {k: v for k, v in RECIPES.get(s["recipe"], {}).items() if k in ("label", "kind", "help")}
    return s


def public(s: dict) -> dict:
    return {k: v for k, v in s.items() if k != "secret_enc"}


def listing() -> list[dict]:
    ensure()
    out = []
    for s in db.all("SELECT id FROM sites ORDER BY id"):
        x = public(get(s["id"]))
        x["records"] = db.one("SELECT COUNT(*) n FROM site_records WHERE site_id = ? AND removed_at IS NULL", s["id"])["n"]
        last = db.one("SELECT digest, at FROM site_checks WHERE site_id = ? ORDER BY id DESC LIMIT 1", s["id"])
        x["last_digest"] = last["digest"] if last else ""
        x["watches"] = db.all("SELECT id, request, cron, enabled, next_run, last_run FROM site_watches WHERE site_id = ?", s["id"])
        out.append(x)
    return out


def delete(sid: int) -> dict:
    from . import keychain

    s = get(sid)
    if s["secret_enc"]:
        keychain.delete(s["secret_enc"])
    db.run("DELETE FROM sites WHERE id = ?", sid)
    return {"ok": True}


# --- check: crawl, extract, diff, digest ---------------------------------------------------------------------------
def _same_site(a: str, b: str) -> bool:
    ha, hb = urlsplit(a).hostname or "", urlsplit(b).hostname or ""
    return ha == hb or ha.endswith("." + hb) or hb.endswith("." + ha)


async def crawl(site: dict, f=None) -> dict:
    """Visit the site's own pages (navigation first), never following logout, delete or similar links."""
    own = f is None
    f = f or fetcher(site)
    start = site["url"]
    seen, queue, pages = set(), [(start, 0)], []
    try:
        while queue and len(pages) < site["max_pages"]:
            url, depth = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            try:
                final, status, html, ctype = await f.page(url)
            except HTTPException:
                raise
            except Exception as e:  # noqa: BLE001
                pages.append({"url": url, "error": str(e)[:200]})
                continue
            if status >= 400 or not html:
                pages.append({"url": url, "error": f"HTTP {status}" if status >= 400 else f"not a page ({ctype})"})
                continue
            p = analyse_page(final, html)
            p["depth"] = depth
            pages.append(p)
            if p["login"] and depth == 0 and not p["collections"]:
                break
            nxt = sorted((l for l in p["links"] if _same_site(l["url"], start) and not DANGER.search(l["text"] + " " + l["url"])),
                         key=lambda l: (not l["nav"], len(l["url"])))
            if depth < 2:
                queue += [(l["url"], depth + 1) for l in nxt if l["url"] not in seen]
            for c in p["collections"]:  # detail pages behind list rows
                for link in (c.get("row_links") or [])[:15]:
                    if link and _same_site(link, start) and link not in seen:
                        queue.append((link, depth + 1))
    finally:
        if own:
            await f.close()
    return {"pages": pages}


def _structure(pages: list[dict]) -> dict:
    detail_by_url = {p["url"]: p["details"] for p in pages if p.get("details")}
    cols, forms, files_, nav = [], [], [], []
    for p in pages:
        if p.get("error"):
            continue
        for c in p["collections"]:
            recs = normalise(c, p["url"])
            for r in recs:  # enrich from the record's own detail page
                det = detail_by_url.get(r["url"])
                if det and r["url"] != p["url"]:
                    r["fields"].update({f"{k}": v for k, v in det.items() if k not in r["fields"]})
                    roles = {role_of(k, [v]): v for k, v in det.items()}
                    for role in ("status", "due", "start", "finish", "person", "location", "amount"):
                        if not r[role] and roles.get(role):
                            r[role] = parse_date(roles[role]) if role in ("due", "start", "finish") else roles[role][:200]
            cols.append({"name": c["name"], "entity": singular(c["name"]), "page": p["url"], "kind": c["kind"],
                         "columns": c["columns"], "roles": schema_of(c), "records": recs})
        forms += [{**fm, "page_url": p["url"]} for fm in p["forms"] if not fm["login"] and not fm["search"]]
        files_ += p["files"]
        nav += [{"text": l["text"], "url": l["url"]} for l in p["links"] if l["nav"]]
    seen_nav, nav_u = set(), []
    for n in nav:
        if n["url"] not in seen_nav and n["text"]:
            seen_nav.add(n["url"])
            nav_u.append(n)
    return {"collections": cols, "forms": forms, "files": list({f["url"]: f for f in files_}.values()), "navigation": nav_u[:40]}


async def check(sid: int, f=None) -> dict:
    """One full read of the site: structure, records, what changed, and a digest in plain English."""
    s = get(sid)
    try:
        if s["mode"] == "api" and s["recipe"] and RECIPES.get(s["recipe"], {}).get("kind") == "api":
            api_cols = await _api_collections(s)
            pages = [{"url": c["page"], "collections": [c], "forms": [], "files": [], "links": [], "details": {}, "login": False}
                     for c in api_cols]
        else:
            pages = (await crawl(s, f))["pages"]
    except HTTPException as e:
        db.run("UPDATE sites SET status = 'error', status_detail = ?, last_checked = ? WHERE id = ?", str(e.detail)[:300], db.now(), sid)
        raise
    ok_pages = [p for p in pages if not p.get("error")]
    if ok_pages and ok_pages[0].get("login") and not any(p["collections"] for p in ok_pages):
        msg = "The site is asking you to sign in. Click “Sign in once”, sign in in the window that opens, then check again."
        db.run("UPDATE sites SET status = 'needs_sign_in', status_detail = ?, last_checked = ? WHERE id = ?", msg, db.now(), sid)
        cid = db.run("INSERT INTO site_checks(site_id, at, status, pages, digest) VALUES (?, ?, 'needs_sign_in', ?, ?)", sid, db.now(), len(pages), msg)
        return {"check_id": cid, "status": "needs_sign_in", "digest": msg, "changes": {}}
    st = _structure(ok_pages)
    changes = _store(sid, st["collections"])
    structure = {**s["structure"], "collections": [{k: v for k, v in c.items() if k != "records"} | {"count": len(c["records"])} for c in st["collections"]],
                 "forms": st["forms"][:40], "files": st["files"][:200], "navigation": st["navigation"],
                 "pages": [{"url": p["url"], "title": p.get("title", ""), "error": p.get("error", "")} for p in pages]}
    digest = write_digest(s, changes, structure)
    n = sum(len(c["records"]) for c in st["collections"])
    db.run("UPDATE sites SET status = 'ok', status_detail = '', structure = ?, last_checked = ? WHERE id = ?", json.dumps(structure), db.now(), sid)
    cid = db.run("INSERT INTO site_checks(site_id, at, status, pages, records, changes, digest) VALUES (?, ?, 'ok', ?, ?, ?, ?)",
                 sid, db.now(), len(pages), n, json.dumps(changes, default=str), digest)
    return {"check_id": cid, "status": "ok", "pages": len(pages), "records": n, "collections": [c["name"] for c in st["collections"]],
            "changes": changes, "digest": digest}


TRACKED = ("title", "status", "start", "due", "finish", "person", "amount", "location")


def _store(sid: int, collections: list[dict]) -> dict:
    now = db.now()
    first_check = not db.one("SELECT 1 FROM site_records WHERE site_id = ? LIMIT 1", sid)
    added, changed, seen = [], [], set()
    for c in collections:
        for r in c["records"]:
            h = hashlib.sha1(json.dumps({k: r[k] for k in TRACKED} | {"f": r["fields"]}, sort_keys=True).encode()).hexdigest()
            seen.add((c["name"], r["key"]))
            old = db.one("SELECT * FROM site_records WHERE site_id = ? AND collection = ? AND key = ?", sid, c["name"], r["key"])
            if not old:
                db.run("INSERT INTO site_records(site_id, collection, key, title, status, start, due, finish, person, amount, location, url, fields, hash, first_seen, last_seen) "
                       "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", sid, c["name"], r["key"], r["title"], r["status"], r["start"], r["due"],
                       r["finish"], r["person"], r["amount"], r["location"], r["url"], json.dumps(r["fields"]), h, now, now)
                if not first_check:
                    added.append({"collection": c["name"], "entity": c["entity"], **{k: r[k] for k in ("key", *TRACKED)}})
                continue
            diffs = {k: [old[k], r[k]] for k in TRACKED if (old[k] or "") != (r[k] or "")}
            fo = json.loads(old["fields"] or "{}")
            for k, v in r["fields"].items():
                if k not in [*TRACKED] and fo.get(k, "") != v and k in fo:
                    diffs.setdefault(k, [fo.get(k, ""), v])
            if diffs or old["removed_at"]:
                changed.append({"collection": c["name"], "entity": c["entity"], "key": r["key"], "title": r["title"], "changes": diffs,
                                "returned": bool(old["removed_at"])})
            db.run("UPDATE site_records SET title=?, status=?, start=?, due=?, finish=?, person=?, amount=?, location=?, url=?, fields=?, hash=?, last_seen=?, removed_at=NULL WHERE id=?",
                   r["title"], r["status"], r["start"], r["due"], r["finish"], r["person"], r["amount"], r["location"], r["url"], json.dumps(r["fields"]), h, now, old["id"])
    removed = []
    names = {c["name"] for c in collections}
    for old in db.all("SELECT * FROM site_records WHERE site_id = ? AND removed_at IS NULL", sid):
        if old["collection"] in names and (old["collection"], old["key"]) not in seen:
            db.run("UPDATE site_records SET removed_at = ? WHERE id = ?", now, old["id"])
            removed.append({"collection": old["collection"], "key": old["key"], "title": old["title"]})
    return {"first_check": first_check, "added": added, "changed": changed, "removed": removed}


def records(sid: int, collection: str = "", include_removed: bool = False) -> list[dict]:
    ensure()
    q = "SELECT * FROM site_records WHERE site_id = ?" + ("" if include_removed else " AND removed_at IS NULL")
    args: list = [sid]
    if collection:
        q += " AND collection = ?"
        args.append(collection)
    rows = db.all(q + " ORDER BY COALESCE(NULLIF(due,''), NULLIF(start,''), '9999'), id", *args)
    for r in rows:
        r["fields"] = json.loads(r["fields"] or "{}")
        r["done"] = bool(DONE_WORDS.search(r["status"] or ""))
        r["overdue"] = bool(r["due"] and r["due"][:10] < date.today().isoformat() and not r["done"])
    return rows


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def write_digest(site: dict, ch: dict, structure: dict) -> str:
    recs = records(site["id"])
    overdue = [r for r in recs if r["overdue"]]
    today = date.today().isoformat()
    soon = [r for r in recs if not r["done"] and (r["due"] or r["start"])[:10] >= today and
            (r["due"] or r["start"])[:10] <= (date.today() + timedelta(days=7)).isoformat()]
    lines = []
    if ch.get("first_check"):
        cols = structure.get("collections", [])
        lines.append(f"First look at {site['name']}: found " + ", ".join(f"{c['count']} {c['name'].lower()}" for c in cols) + "." if cols
                     else f"First look at {site['name']}: no lists or tables were found on the pages it could reach.")
    else:
        if not (ch["added"] or ch["changed"] or ch["removed"]):
            lines.append(f"Nothing changed on {site['name']} since the last check.")
        for a in ch["added"][:15]:
            when = f", {('due ' + a['due']) if a['due'] else ('on ' + a['start']) if a['start'] else ''}".rstrip(", ")
            lines.append(f"New {a['entity']}: {a['title']}{when}" + (f" ({a['status']})" if a["status"] else "") + ".")
        for c in ch["changed"][:20]:
            bits = [f"{k} {v[0] or '(blank)'} → {v[1] or '(blank)'}" for k, v in list(c["changes"].items())[:4]]
            lines.append(f"{c['entity'].capitalize()} {c['title']}: " + ("back on the site; " if c["returned"] else "") + "; ".join(bits) + ".")
        for r in ch["removed"][:10]:
            lines.append(f"No longer listed: {r['title']}.")
    if overdue:
        lines.append(f"Overdue: {_plural(len(overdue), 'item')} — " + "; ".join(f"{r['title']} (due {r['due']})" for r in overdue[:8]) + ".")
    if soon:
        lines.append(f"Coming up in the next 7 days: " + "; ".join(f"{r['title']} ({r['due'] or r['start']})" for r in soon[:8]) + ".")
    return "\n".join(lines)


# --- plan: timeline, calendar, ICS ------------------------------------------------------------------------------------
def timeline(sid: int) -> list[dict]:
    items = []
    for r in records(sid):
        start = r["start"] or r["due"]
        if not start:
            continue
        end = r["finish"] or r["due"] or start
        if end < start:
            end = start
        items.append({"id": r["id"], "collection": r["collection"], "title": r["title"], "start": start, "end": end, "status": r["status"],
                      "person": r["person"], "location": r["location"], "overdue": r["overdue"], "done": r["done"], "url": r["url"]})
    return sorted(items, key=lambda i: i["start"])


def ics(sid: int) -> str:
    from icalendar import Calendar, Event

    s = get(sid)
    cal = Calendar()
    cal.add("prodid", "-//AI Workspace//Sites//EN")
    cal.add("version", "2.0")
    for it in timeline(sid):
        e = Event()
        e.add("uid", f"site{sid}-rec{it['id']}@ai-workspace")
        e.add("summary", f"{it['title']}" + (f" ({it['status']})" if it["status"] else ""))
        st, en = _dt(it["start"]), _dt(it["end"])
        if isinstance(st, datetime):
            e.add("dtstart", st)
            e.add("dtend", en if isinstance(en, datetime) and en > st else st + timedelta(hours=1))
        else:
            e.add("dtstart", st)
            e.add("dtend", (en if not isinstance(en, datetime) else en.date()) + timedelta(days=1))
        if it["location"]:
            e.add("location", it["location"])
        e.add("description", f"From {s['name']}: {it['url']}" + (f"\nWho: {it['person']}" if it["person"] else ""))
        cal.add_component(e)
    return cal.to_ical().decode()


def _dt(v: str):
    return datetime.fromisoformat(v) if "T" in v else date.fromisoformat(v[:10])


async def to_calendar(sid: int, record_ids: list[int] | None = None) -> dict:
    """Put the site's dated items in the user's calendar (local, or a connected one). Re-running updates nothing twice."""
    from . import services

    made, skipped = [], 0
    for it in timeline(sid):
        if record_ids and it["id"] not in record_ids:
            continue
        if it["done"]:
            skipped += 1
            continue
        title = f"{it['title']}" + (f" ({it['status']})" if it["status"] else "")
        exists = db.one("SELECT id FROM events WHERE title = ? AND substr(start, 1, 10) = ?", title[:300], it["start"][:10])
        if exists:
            skipped += 1
            continue
        all_day = "T" not in it["start"]
        start = it["start"] if all_day else _with_tz(it["start"])
        end = None if all_day else _with_tz(it["end"]) if it["end"] != it["start"] else None
        ev = await asyncio.to_thread(services.create_event, services.EventIn(title=title[:300], start=start, end=end, all_day=all_day,
                                                                               location=it["location"][:300], description=f"From {get(sid)['name']}: {it['url']}"))
        made.append(ev.get("id") if isinstance(ev, dict) else ev)
    return {"created": len(made), "skipped": skipped}


def _with_tz(v: str) -> str:
    from zoneinfo import ZoneInfo

    from .assistant import timezone_name

    d = datetime.fromisoformat(v)
    return (d if d.tzinfo else d.replace(tzinfo=ZoneInfo(timezone_name()))).isoformat()


# --- watches: "check my portal every morning and tell me which jobs are overdue" ---------------------------------------
def parse_request(text: str) -> dict:
    """Turn a plain-English request into a schedule and a rule, without needing a model."""
    t = text.lower()
    hour, minute = 7, 0
    m = re.search(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b|\bat\s+(\d{1,2})(?::(\d{2}))?\b", t)
    if m:
        hour = int(m.group(1) or m.group(4))
        minute = int(m.group(2) or m.group(5) or 0)
        if m.group(3) == "pm" and hour < 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
    elif "evening" in t or "night" in t:
        hour = 18
    elif "afternoon" in t or "lunch" in t:
        hour = 13 if "afternoon" in t else 12
    days = "*"
    names = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
    picked = [str(i) for i, d in enumerate(names) if re.search(rf"\b{d}s?\b|\b{d[:3]}\b", t)]
    if "weekday" in t or "work day" in t or "workday" in t:
        days = "1-5"
    elif picked:
        days = ",".join(picked)
    hm = re.search(r"every\s+(\d+)\s+hours?", t)
    if hm:
        cron = f"{minute} */{max(1, int(hm.group(1)))} * * *"
    elif "hourly" in t or "every hour" in t:
        cron = f"{minute} * * * *"
    elif "week" in t and days == "*" and "weekday" not in t:
        cron = f"{minute} {hour} * * 1"
    else:
        cron = f"{minute} {hour} * * {days}"
    show = []
    if "overdue" in t or "late" in t or "behind" in t:
        show.append("overdue")
    if re.search(r"due (soon|this week|next week|today|tomorrow)|coming up|upcoming|this week|today", t):
        show.append("upcoming")
    if re.search(r"\bnew\b|added|assigned to me", t):
        show.append("new")
    if re.search(r"chang|update|moved|status", t):
        show.append("changed")
    if not show:
        show = ["new", "changed", "overdue", "upcoming"]
    words = re.findall(r"\b(jobs?|tasks?|shifts?|projects?|tickets?|invoices?|orders?|cases?|matters?|leads?|deals?|assignments?|"
                       r"bookings?|appointments?|quotes?|issues?|rfis?|defects?|inspections?|deliveries?|classes?|courses?|experiments?|samples?|"
                       r"rosters?|timesheets?)\b", t)
    return {"cron": cron, "show": show, "about": singular(words[0]) if words else "", "calendar": bool(re.search(r"calendar|schedule (it|them)|add (it|them) to", t)),
            "email": bool(re.search(r"\bemail me\b|send (me )?an email|by email", t))}


def _match_site(text: str) -> dict:
    sites = listing()
    if not sites:
        raise HTTPException(400, "Add the website first (Sites → Add a site), then ask again.")
    t = text.lower()
    for s in sites:
        if s["name"].lower() in t or (urlsplit(s["url"]).hostname or "").lower() in t:
            return s
    if len(sites) == 1:
        return sites[0]
    raise HTTPException(400, "Which site? Say its name: " + ", ".join(s["name"] for s in sites))


def watch_from_text(text: str, site_id: int | None = None) -> dict:
    from . import scheduler

    s = get(site_id) if site_id else _match_site(text)
    rule = parse_request(text)
    wid = db.run("INSERT INTO site_watches(site_id, request, cron, rule, next_run, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 s["id"], text.strip()[:500], rule["cron"], json.dumps(rule), scheduler.next_run(rule["cron"]), db.now())
    return {**db.one("SELECT * FROM site_watches WHERE id = ?", wid), "rule": rule, "site": s["name"], "in_words": describe_cron(rule["cron"])}


def describe_cron(cron: str) -> str:
    mi, h, _, _, dow = cron.split()
    if h.startswith("*/"):
        return f"every {h[2:]} hours"
    if h == "*":
        return "every hour"
    hh, mm = int(h), int(mi)
    t = f"{(hh % 12) or 12}:{mm:02d} {'AM' if hh < 12 else 'PM'}"
    names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    when = {"*": "every day", "1-5": "every weekday"}.get(dow) or "every " + ", ".join(names[int(d) % 7] for d in dow.split(","))
    return f"{when} at {t}"


def answer(site_id: int, rule: dict, changes: dict) -> str:
    """The watch's message: only what the user asked to hear about."""
    s = get(site_id)
    recs = records(site_id)
    about = rule.get("about") or ""
    if about:
        filt = [r for r in recs if singular(r["collection"]) == about] or recs
    else:
        filt = recs
    lines = []
    keys = {r["key"] for r in filt}
    if "overdue" in rule["show"]:
        od = [r for r in filt if r["overdue"]]
        lines.append(f"Overdue ({len(od)}): " + ("; ".join(f"{r['title']} — due {r['due']}" + (f", {r['status']}" if r["status"] else "") for r in od[:15]) if od else "none."))
    if "upcoming" in rule["show"]:
        lim = (date.today() + timedelta(days=7)).isoformat()
        up = [r for r in filt if not r["done"] and date.today().isoformat() <= (r["due"] or r["start"])[:10] <= lim]
        lines.append(f"Next 7 days ({len(up)}): " + ("; ".join(f"{r['title']} — {r['due'] or r['start']}" for r in up[:15]) if up else "nothing scheduled."))
    if "new" in rule["show"] and not changes.get("first_check"):
        new = [a for a in changes.get("added", []) if a["key"] in keys]
        lines.append(f"New ({len(new)}): " + ("; ".join(a["title"] for a in new[:15]) if new else "none."))
    if "changed" in rule["show"] and not changes.get("first_check"):
        ch = [c for c in changes.get("changed", []) if c["key"] in keys]
        lines.append(f"Changed ({len(ch)}): " + ("; ".join(f"{c['title']} ({', '.join(f'{k} → {v[1]}' for k, v in list(c['changes'].items())[:3])})" for c in ch[:15]) if ch else "none."))
    return f"{s['name']}\n" + "\n".join(lines)


async def run_watch(w: dict) -> str:
    from . import scheduler

    db.run("UPDATE site_watches SET next_run = ? WHERE id = ?", scheduler.next_run(w["cron"]), w["id"])
    rule = json.loads(w["rule"] or "{}")
    try:
        res = await check(w["site_id"])
        text = res["digest"] if res["status"] != "ok" else answer(w["site_id"], rule, res["changes"])
        if res["status"] == "ok" and rule.get("calendar"):
            c = await to_calendar(w["site_id"])
            text += f"\nCalendar: {c['created']} new events added."
        if rule.get("email"):
            from . import tools

            try:
                tools.save_draft(tools.DraftIn(to="", subject=f"{get(w['site_id'])['name']}: daily summary", body=text))
                text += "\nAn email draft is waiting in Mail for you to review and send."
            except Exception:  # noqa: BLE001 - mail is optional
                pass
    except HTTPException as e:
        text = f"Could not check the site: {e.detail}"
    db.run("UPDATE site_watches SET last_run = ?, last_result = ? WHERE id = ?", db.now(), text[:4000], w["id"])
    scheduler.deliver(f"Site watch: {get(w['site_id'])['name']}", text, "inbox", f"site_watch:{w['id']}")
    return text


async def loop() -> None:
    """Scheduled re-checks. Read only: a watch never fills in a form or clicks anything that changes the site."""
    while True:
        await asyncio.sleep(30)
        try:
            ensure()
            for w in db.all("SELECT * FROM site_watches WHERE enabled = 1 AND next_run <= ? ORDER BY next_run", db.now()):
                await run_watch(w)
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            log.warning("site watch error: %s", e)


# --- act: proposals the user approves -----------------------------------------------------------------------------------
def propose(sid: int, form_ref, values: dict, page_url: str = "") -> dict:
    """Prepare a change on the site (fill a form it offers). Nothing is sent until the user approves this exact change."""
    s = get(sid)
    forms = s["structure"].get("forms", [])
    form = None
    if isinstance(form_ref, int) or str(form_ref).isdigit():
        i = int(form_ref)
        form = forms[i] if 0 <= i < len(forms) else None
    else:
        ref = str(form_ref).lower()
        form = next((f for f in forms if ref in (f["name"] + " " + f["submit"]).lower() and (not page_url or f["page_url"] == page_url)), None)
    if not form:
        raise HTTPException(404, "That form was not found on the site. Check the site again or pick one from the list: "
                                 + "; ".join(f"{i}: {f['name']} ({f['submit']})" for i, f in enumerate(forms[:20])))
    names = {f["name"]: f for f in form["fields"]}
    labels = {f["label"].lower(): f["name"] for f in form["fields"]}
    clean = {}
    for k, v in (values or {}).items():
        name = k if k in names else labels.get(str(k).lower())
        if not name:
            raise HTTPException(400, f"The form has no field '{k}'. Fields: " + ", ".join(f"{f['label']} ({f['name']})" for f in form["fields"]))
        opts = names[name]["options"]
        if opts and str(v) not in [o["value"] for o in opts]:
            match = next((o["value"] for o in opts if o["label"].lower() == str(v).lower()), None)
            if match is None:
                raise HTTPException(400, f"'{v}' is not a choice for {names[name]['label']}: " + ", ".join(o["label"] for o in opts))
            v = match
        clean[name] = str(v)
    summary = f"On {s['name']}, “{form['name']}”: " + ", ".join(f"{names[k]['label']} = {v}" for k, v in clean.items()) + f", then press “{form['submit']}”."
    aid = db.run("INSERT INTO site_actions(site_id, page_url, form, vals, summary, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 sid, form["page_url"], json.dumps(form), json.dumps(clean), summary, db.now())
    return action(aid)


def action(aid: int) -> dict:
    ensure()
    a = db.one("SELECT * FROM site_actions WHERE id = ?", aid)
    if not a:
        raise HTTPException(404, "No such action.")
    a["form"] = json.loads(a["form"])
    a["vals"] = json.loads(a["vals"])
    return a


def actions(sid: int | None = None) -> list[dict]:
    ensure()
    q = "SELECT id FROM site_actions" + (" WHERE site_id = ?" if sid else "") + " ORDER BY id DESC LIMIT 100"
    return [action(r["id"]) for r in db.all(q, *([sid] if sid else []))]


async def decide(aid: int, approve: bool, f=None) -> dict:
    a = action(aid)
    if a["status"] != "pending":
        raise HTTPException(409, "This change was already answered.")
    if not approve:
        db.run("UPDATE site_actions SET status = 'denied', decided_at = ? WHERE id = ?", db.now(), aid)
        return action(aid)
    own = f is None
    f = f or fetcher(get(a["site_id"]))
    try:
        url, status, html = await f.submit(a["form"], a["vals"])
        ok = status < 400
        p = analyse_page(url, html) if html else {"title": "", "text": ""}
        result = f"HTTP {status} at {url}. Page now says: {p['title']} — {p['text'][:300]}"
        db.run("UPDATE site_actions SET status = ?, result = ?, decided_at = ? WHERE id = ?", "done" if ok else "failed", result[:2000], db.now(), aid)
    finally:
        if own:
            await f.close()
    return action(aid)


async def fetch_file(sid: int, url: str, f=None) -> dict:
    """Download a file the site links to into the Files library, ready to analyse."""
    from . import analysis

    s = get(sid)
    if not _same_site(url, s["url"]):
        raise HTTPException(400, "Only files on the site itself are downloaded.")
    own = f is None
    f = f or fetcher(s)
    try:
        data, name = await f.download(url)
    finally:
        if own:
            await f.close()
    return analysis.add(name, data=data, source=f"site:{sid}")


async def sign_in(sid: int) -> dict:
    """Open the site in a visible Rooks Browser window with this site's own profile; the user signs in there."""
    s = get(sid)
    if not (config.BROWSER_URL and config.BROWSER_API_KEY):
        raise HTTPException(400, "Rooks Browser is not running. Restart the app, then try again.")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{config.BROWSER_URL}/api/site/login", json={"url": s["url"], "profile": f"site-{sid}"},
                         headers={"Authorization": f"Bearer {config.BROWSER_API_KEY}"})
    if r.status_code >= 400:
        raise HTTPException(r.status_code, r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text)
    db.run("UPDATE sites SET mode = CASE WHEN mode = 'http' THEN 'browser' ELSE mode END WHERE id = ?", sid)
    return {"ok": True, "message": "A browser window opened. Sign in there as you normally do, then close the window and press “Check now”."}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
