"""Integration health checks (item 25): one status per moving part, for the status page and the CLI.

Checks run only when someone asks (opening the page, `?fresh=true`), never in the background: a probe of a cloud
provider is egress, and R8 allows none the user did not cause. Results are cached for a minute. Each check has its own
timeout and its failure is a status, never an error (R4). Paid search APIs are not probed, because a probe costs a query.

Status: ok | degraded (works, with a problem) | down | off (not configured).
"""

import asyncio
import shutil
import time

import httpx
from fastapi import HTTPException

from . import config, db, search, telegram
from .adapters import Adapter
from .redact import redact

TIMEOUT_S = 8
CACHE_S = 60
DISK_WARN_MB, DISK_DOWN_MB = 500, 50
LOOPS: dict[str, asyncio.Task] = {}  # background loops, registered by main.lifespan
_cache: tuple[float, dict] | None = None
_RANK = {"ok": 0, "off": 0, "degraded": 1, "down": 2}


def _result(name: str, kind: str, status: str, detail: str = "", ms: int = 0) -> dict:
    return {"name": name, "kind": kind, "status": status, "detail": redact(detail)[:300], "latency_ms": ms}


def database() -> dict:
    started = time.monotonic()
    try:
        version = db.one("PRAGMA user_version")["user_version"]
    except Exception as e:  # noqa: BLE001 - reported as down, not raised
        return _result("Database", "core", "down", f"SQLite is not readable: {e}")
    ms = int((time.monotonic() - started) * 1000)
    free_mb = shutil.disk_usage(config.DATA_DIR).free // 2**20
    if version != db.SCHEMA_VERSION:
        return _result("Database", "core", "down", f"schema version {version}, expected {db.SCHEMA_VERSION}", ms)
    if free_mb < DISK_DOWN_MB:
        return _result("Database", "core", "down", f"{free_mb} MB free on the data volume; writes will fail", ms)
    if free_mb < DISK_WARN_MB:
        return _result("Database", "core", "degraded", f"only {free_mb} MB free on the data volume", ms)
    return _result("Database", "core", "ok", f"schema v{version}, {free_mb:,} MB free", ms)


def loops() -> list[dict]:
    out = []
    for name, task in LOOPS.items():
        if not task.done():
            out.append(_result(f"{name} loop", "core", "ok", "running"))
        else:
            exc = None if task.cancelled() else task.exception()
            out.append(_result(f"{name} loop", "core", "down", f"stopped: {exc!r}" if exc else "stopped"))
    return out


async def _timed(name: str, kind: str, fn) -> dict:
    """Run a blocking probe in a thread with a timeout. fn returns (status, detail)."""
    started = time.monotonic()
    try:
        status, detail = await asyncio.wait_for(asyncio.to_thread(fn), TIMEOUT_S)
    except asyncio.TimeoutError:
        status, detail = "down", f"no answer within {TIMEOUT_S} s"
    except HTTPException as e:
        status, detail = "down", str(e.detail)
    except Exception as e:  # noqa: BLE001
        status, detail = "down", str(e) or e.__class__.__name__
    return _result(name, kind, status, detail, int((time.monotonic() - started) * 1000))


def _connection(c: dict):
    return lambda: Adapter(c).probe()


async def connections() -> list[dict]:
    conns = db.all("SELECT * FROM connections WHERE enabled = 1 ORDER BY id")
    results = await asyncio.gather(*(_timed(c["name"], "model connection", _connection(c)) for c in conns))
    for c, r in zip(conns, results):
        if r["status"] == "ok":
            db.run("UPDATE connections SET last_ok_at = ?, last_error = '' WHERE id = ?", db.now(), c["id"])
        else:
            db.run("UPDATE connections SET last_error = ? WHERE id = ?", r["detail"], c["id"])
        r["ref"] = c["id"]
    if not conns:
        return [_result("Model connections", "model connection", "down", "none configured: add one in Settings")]
    return list(results)


async def _http(name: str, kind: str, url: str, headers: dict | None = None) -> dict:
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_S) as c:
            r = await c.get(url, headers=headers or {})
        status = "ok" if r.status_code < 400 else "down"
        detail = "reachable" if status == "ok" else f"HTTP {r.status_code}"
    except httpx.HTTPError as e:
        status, detail = "down", f"unreachable ({e.__class__.__name__})"
    return _result(name, kind, status, detail, int((time.monotonic() - started) * 1000))


async def addons() -> list[dict]:
    out = []
    if config.BROWSER_URL and config.BROWSER_API_KEY:
        out.append(await _http("Browser agent", "add-on", f"{config.BROWSER_URL}/healthz"))
    else:
        out.append(_result("Browser agent", "add-on", "off", "add-on not enabled"))
    p = search.provider()
    if p == "searxng":
        out.append(await _http("Web search (SearXNG)", "add-on", f"{config.SEARXNG_URL}/healthz"))
    elif p:
        out.append(_result(f"Web search ({p})", "integration", "ok",
                           "key configured; not probed, because a test search would spend a paid query"))
    else:
        out.append(_result("Web search", "integration", "off", "no search provider configured"))
    return out


def integrations() -> list[dict]:
    """From state the app already keeps; no probe."""
    out = []
    tg = telegram.status()
    if not tg["configured"]:
        out.append(_result("Telegram", "integration", "off", "no bot token"))
    elif tg.get("running"):
        out.append(_result("Telegram", "integration", "ok", f"connected as @{tg.get('bot', '')}"))
    else:
        out.append(_result("Telegram", "integration", "down", tg.get("error") or "not connected yet"))
    for c in db.all("SELECT name, url, last_synced, last_error FROM calendars WHERE url != '' ORDER BY id"):
        if c["last_error"]:
            out.append(_result(f"Calendar: {c['name']}", "integration", "down", c["last_error"]))
        elif c["last_synced"]:
            out.append(_result(f"Calendar: {c['name']}", "integration", "ok", f"last synced {c['last_synced']}"))
        else:
            out.append(_result(f"Calendar: {c['name']}", "integration", "degraded", "not synced yet"))
    return out


async def mail_accounts() -> list[dict]:
    from . import mail

    accts = db.all("SELECT * FROM mail_accounts ORDER BY id")

    def probe(a):
        def run():
            mail.test(a)
            return "ok", "signed in to IMAP"
        return run
    return list(await asyncio.gather(*(_timed(f"Mail: {a['email']}", "integration", probe(a)) for a in accts)))


def overall(checks: list[dict]) -> str:
    worst = max((_RANK[c["status"]] for c in checks), default=0)
    return {0: "ok", 1: "degraded", 2: "down"}[worst]


async def report(fresh: bool = False) -> dict:
    global _cache
    if _cache and not fresh and _cache[0] > time.monotonic() - CACHE_S:
        return _cache[1]
    core = [database(), *loops()]
    conns, adds, mails = await asyncio.gather(connections(), addons(), mail_accounts())
    checks = [*core, *conns, *adds, *integrations(), *mails]
    out = {"status": overall(checks), "checked_at": db.now(), "checks": checks}
    _cache = (time.monotonic(), out)
    return out
