"""Outbound audit log (item 75, R8): every connection Workbench opens to another host is a row in the run log.

Rows are `actions` of kind `egress` (R2: no second ledger): host in `provider`, the Workbench module that made the call
in `tool`, `local` or `external` in `effect`, "METHOD scheme://host/path" in `args` (no query string; redacted like every
text column, so a token in a URL path is masked), the outcome and the latency. Local-only mode is verifiable from these
rows alone: no `external` row in the period means nothing left the machine or its private network.

Coverage, enforced by tests/test_egress.py:
* httpx (model adapters, search, research, Telegram, health, browser proxy, ICS calendars): both transports are wrapped
  once at startup, so every request is recorded whoever made it.
* requests (used inside caldav): HTTPAdapter.send is wrapped the same way.
* IMAP and SMTP (mail.py): recorded where the connection is opened, with track().
* No other network library may be imported by the app (source scan in the test). DNS lookups are not recorded.
A failure to write a row is logged and never breaks the request (R4).
"""

import ipaddress
import logging
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("egress")
_installed = False


def is_local(host: str) -> bool:
    """Loopback, private and link-local addresses, *.local / *.internal / *.localhost names, and single-label names
    (Docker service names such as `browser` or `ollama`). Anything else counts as external."""
    h = (host or "").lower().strip("[]")
    if h == "localhost" or h.endswith((".local", ".internal", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return "." not in h


def _component() -> str:
    """The Workbench module that made the call: the nearest app.* frame on the stack, other than this one."""
    f = sys._getframe(1)
    while f is not None:
        name = f.f_globals.get("__name__", "")
        if name.startswith("app.") and name != __name__:
            return name[4:]
        f = f.f_back
    return "unknown"


def record(method: str, url: str, outcome: str, ms: int, error: str = "", component: str | None = None) -> None:
    from . import actions

    try:
        u = urlsplit(url)
        host = u.hostname or ""
        target = f"{u.scheme}://{host}{f':{u.port}' if u.port else ''}{u.path}"
        actions.add("egress", outcome, trace={"channel": "egress"}, provider=host, tool=component or _component(),
                    effect="local" if is_local(host) else "external", args=f"{method} {target}", latency_ms=ms,
                    failure_reason=error)
    except Exception as e:  # noqa: BLE001 - auditing must never break the call it audits
        log.warning("could not record outbound call: %s", e.__class__.__name__)


def _outcome(status: int) -> str:
    return "ok" if status < 400 else f"http_{status}"


@contextmanager
def track(method: str, url: str, component: str | None = None):
    """Record a connection made by a library that is not wrapped globally (imaplib, smtplib)."""
    started, error = time.monotonic(), ""
    comp = component or _component()
    try:
        yield
    except Exception as e:
        error = e.__class__.__name__
        raise
    finally:
        record(method, url, "error" if error else "ok", int((time.monotonic() - started) * 1000), error, comp)


def install() -> None:
    """Wrap the HTTP clients once. Called by main before anything can make a request."""
    global _installed
    if _installed:
        return
    _installed = True

    sync_send = httpx.HTTPTransport.handle_request
    async_send = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(self, request):
        started, comp = time.monotonic(), _component()
        try:
            response = sync_send(self, request)
        except Exception as e:
            record(request.method, str(request.url), "error", int((time.monotonic() - started) * 1000), e.__class__.__name__, comp)
            raise
        record(request.method, str(request.url), _outcome(response.status_code), int((time.monotonic() - started) * 1000), "", comp)
        return response

    async def handle_async_request(self, request):
        started, comp = time.monotonic(), _component()
        try:
            response = await async_send(self, request)
        except Exception as e:
            record(request.method, str(request.url), "error", int((time.monotonic() - started) * 1000), e.__class__.__name__, comp)
            raise
        record(request.method, str(request.url), _outcome(response.status_code), int((time.monotonic() - started) * 1000), "", comp)
        return response

    httpx.HTTPTransport.handle_request = handle_request
    httpx.AsyncHTTPTransport.handle_async_request = handle_async_request

    try:
        import requests.adapters
    except ImportError:  # only caldav brings requests in
        return
    requests_send = requests.adapters.HTTPAdapter.send

    def send(self, request, *args, **kwargs):
        started, comp = time.monotonic(), _component()
        try:
            response = requests_send(self, request, *args, **kwargs)
        except Exception as e:
            record(request.method, request.url, "error", int((time.monotonic() - started) * 1000), e.__class__.__name__, comp)
            raise
        record(request.method, request.url, _outcome(response.status_code), int((time.monotonic() - started) * 1000), "", comp)
        return response

    requests.adapters.HTTPAdapter.send = send


def summary(hours: int = 24) -> dict:
    """What left Workbench in the last `hours`: per host, plus whether everything stayed local."""
    from . import db

    since = db.one("SELECT strftime('%Y-%m-%dT%H:%M:%S+00:00', 'now', ?) AS t", f"-{int(hours)} hours")["t"]
    hosts = db.all("SELECT provider AS host, effect, GROUP_CONCAT(DISTINCT tool) AS components, COUNT(*) AS requests, "
                   "SUM(outcome != 'ok') AS failures, MAX(ts) AS last FROM actions WHERE kind = 'egress' AND ts >= ? "
                   "GROUP BY provider, effect ORDER BY effect DESC, requests DESC", since)
    external = sum(h["requests"] for h in hosts if h["effect"] == "external")
    recent = db.all("SELECT id, ts, provider AS host, tool AS component, effect, args, outcome, failure_reason, latency_ms "
                    "FROM actions WHERE kind = 'egress' ORDER BY id DESC LIMIT 50")
    return {"since": since, "hours": hours, "local_only": external == 0, "external_requests": external,
            "hosts": hosts, "recent": recent}
