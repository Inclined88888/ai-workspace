"""Work sites for Workbench's Sites page: read, download and (only when approved) submit on sites the person signs in to.

Each site gets its own named profile. Signing in happens once, in a visible window the person drives themselves
(Workbench never sees the password); the session (cookies, including session-only ones, and local storage) is kept in
that profile's folder, readable only by this user, and reused by every later read, download and submit.

  POST /api/site/login    {"url", "profile"}                       a visible window opens on the site; sign in there
  POST /api/site/close    {"profile"}                              save the session and close that window
  POST /api/site/page     {"url", "profile"}                       -> {"url", "status", "html", "content_type"}
  POST /api/site/submit   {"url", "profile", "form_index", "values", "approved": true}
                                                                   fill and send form N on that page -> {"url", "status", "html"}
                                                                   refused unless "approved" is true (Workbench asks first)
  POST /api/site/download {"url", "profile"}                       -> {"name", "content_type", "data" (base64), "size"}
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
from pathlib import Path

from . import runner
from .browser import ENGINES

MAX_DOWNLOAD = 100 * 1024 * 1024
_PROFILE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class SiteError(ValueError):
    pass


def profile_dir(profile: str) -> Path:
    if not _PROFILE.match(profile or "") or profile.startswith("."):
        raise SiteError("profile must be 1-64 letters, digits, dot, dash or underscore")
    d = runner.home() / "site-profiles" / profile
    d.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


def _url(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        raise SiteError("url is required")
    if "://" not in url:
        url = "https://" + url
    if not url.lower().startswith(("http://", "https://")):
        raise SiteError("only http and https sites")
    return url


class Sites:
    """Runs on the server's event loop. One playwright, one browser per mode, one context per signed-in window."""

    def __init__(self, loop: asyncio.AbstractEventLoop, engine: str = "auto"):
        self.loop, self.engine = loop, engine
        self._pw = None
        self._browsers: dict[bool, object] = {}
        self.windows: dict[str, dict] = {}  # profile -> {"context", "page", "saver"}

    def run(self, coro, timeout: float = 120):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    async def _browser(self, headless: bool):
        if self._browsers.get(headless):
            return self._browsers[headless]
        from playwright.async_api import async_playwright

        if not self._pw:
            self._pw = await async_playwright().start()
        order = [self.engine] if self.engine != "auto" else list(ENGINES)
        errors = []
        for eng in order:
            try:
                kw = {"headless": headless, "args": ["--no-first-run", "--no-default-browser-check"]}
                if eng != "chromium":
                    kw["channel"] = eng
                self._browsers[headless] = await self._pw.chromium.launch(**kw)
                return self._browsers[headless]
            except Exception as e:  # noqa: BLE001
                errors.append(f"{eng}: {str(e).splitlines()[0][:120]}")
        raise RuntimeError("No browser found: " + "; ".join(errors))

    @staticmethod
    def _state_file(profile: str) -> Path:
        return profile_dir(profile) / "session.json"

    async def _save(self, profile: str, context) -> None:
        try:
            state = await context.storage_state()
        except Exception:  # noqa: BLE001 - window already gone
            return
        p = self._state_file(profile)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state))
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(p)

    async def _context(self, profile: str, headless: bool = True):
        st = self._state_file(profile)
        b = await self._browser(headless)
        kw = {"storage_state": str(st)} if st.exists() else {}
        if not headless:
            kw["no_viewport"] = True
        return await b.new_context(accept_downloads=True, **kw)

    # sign in ----------------------------------------------------------------------------------------------------------
    async def login(self, url: str, profile: str) -> dict:
        url = _url(url)
        profile_dir(profile)
        if profile in self.windows:
            await self.close(profile)
        headless = os.getenv("ROOKS_BROWSER_SITE_HEADLESS") == "1"  # tests only; people always get a real window
        ctx = await self._context(profile, headless=headless)
        page = await ctx.new_page()

        async def saver():  # keep the session on disk while the person signs in, so closing the window loses nothing
            while True:
                await asyncio.sleep(2)
                await self._save(profile, ctx)

        win = {"context": ctx, "page": page, "saver": asyncio.ensure_future(saver())}
        self.windows[profile] = win

        def gone(_=None):
            if self.windows.get(profile) is win:
                asyncio.ensure_future(self.close(profile))

        page.on("close", gone)
        await page.goto(url, wait_until="domcontentloaded")
        return {"ok": True, "profile": profile, "status": "open", "url": page.url,
                "message": "Sign in in the window that opened, then close it."}

    async def close(self, profile: str) -> dict:
        win = self.windows.get(profile)
        if not win:
            return {"ok": True, "profile": profile, "status": "closed", "signed_in_before": self._state_file(profile).exists()}
        if win.get("closing"):  # already saving: wait for it, then report
            await win["closing"]
            return {"ok": True, "profile": profile, "status": "closed", "signed_in_before": True}
        win["closing"] = asyncio.get_running_loop().create_future()
        win["saver"].cancel()
        try:
            await self._save(profile, win["context"])  # stays listed until the session is on disk
            try:
                await win["context"].close()
            except Exception:  # noqa: BLE001
                pass
        finally:
            if self.windows.get(profile) is win:
                self.windows.pop(profile, None)
            win["closing"].set_result(True)
        return {"ok": True, "profile": profile, "status": "closed", "signed_in_before": True}

    # read, submit, download ------------------------------------------------------------------------------------------
    async def _with_page(self, profile: str, fn):
        win = self.windows.get(profile)
        if win and win.get("closing"):
            await win["closing"]
            win = None
        if win:  # the sign-in window is still open: work in a tab of the same session
            page = await win["context"].new_page()
            try:
                return await fn(page)
            finally:
                await page.close()
                await self._save(profile, win["context"])
        ctx = await self._context(profile)
        try:
            page = await ctx.new_page()
            out = await fn(page)
            await self._save(profile, ctx)  # sites refresh their cookies; keep the newest
            return out
        finally:
            await ctx.close()

    async def page(self, url: str, profile: str) -> dict:
        url = _url(url)
        profile_dir(profile)

        async def go(page):
            try:
                resp = await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception as e:  # noqa: BLE001 - a file, not a page
                if "Download is starting" in str(e):
                    r = await page.context.request.get(url)
                    return {"url": r.url, "status": r.status, "html": "", "content_type": r.headers.get("content-type", "")}
                raise
            ctype = (resp.headers.get("content-type", "") if resp else "") or "text/html"
            html = await page.content() if "html" in ctype or "xml" in ctype else ""
            return {"url": page.url, "status": resp.status if resp else 200, "html": html, "content_type": ctype}

        return await self._with_page(profile, go)

    async def submit(self, url: str, profile: str, form_index: int, values: dict, approved: bool) -> dict:
        if approved is not True:
            raise PermissionError("Sending a form changes something on the site, so it needs the person's OK first.")
        url = _url(url)
        profile_dir(profile)
        values = {str(k): "" if v is None else str(v) for k, v in (values or {}).items()}

        async def send(page):
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            n = await page.evaluate("() => document.forms.length")
            if not 0 <= int(form_index) < n:
                raise SiteError(f"This page has {n} form(s); form {form_index} is not there any more. Check the site again.")
            async with page.expect_navigation(wait_until="domcontentloaded", timeout=60000) as nav:
                await page.evaluate("""([i, vals]) => {
                  const f = document.forms[i];
                  for (const [name, v] of Object.entries(vals)) {
                    for (const el of f.querySelectorAll(`[name="${CSS.escape(name)}"]`)) {
                      if (el.type === 'checkbox' || el.type === 'radio') el.checked = (el.value === v || v === 'on' || v === 'true');
                      else el.value = v;
                      el.dispatchEvent(new Event('input', {bubbles: true})); el.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                  }
                  f.requestSubmit ? f.requestSubmit() : f.submit();
                }""", [int(form_index), values])
            resp = await nav.value
            return {"url": page.url, "status": resp.status if resp else 200, "html": await page.content()}

        return await self._with_page(profile, send)

    async def download(self, url: str, profile: str) -> dict:
        url = _url(url)
        profile_dir(profile)

        async def get(page):
            r = await page.context.request.get(url, timeout=300000)
            if r.status >= 400:
                raise SiteError(f"The site answered {r.status} for that file.")
            body = await r.body()
            if len(body) > MAX_DOWNLOAD:
                raise SiteError("That file is larger than 100 MB.")
            cd = r.headers.get("content-disposition", "")
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd)
            name = (m.group(1) if m else url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]) or "download"
            return {"name": name, "content_type": r.headers.get("content-type", ""), "size": len(body),
                    "data": base64.b64encode(body).decode()}

        return await self._with_page(profile, get)

    async def shutdown(self) -> None:
        for p in list(self.windows):
            await self.close(p)
        for b in self._browsers.values():
            try:
                await b.close()
            except Exception:  # noqa: BLE001
                pass
        if self._pw:
            await self._pw.stop()


def handle(jobs, action: str, body: dict) -> tuple[int, dict]:
    """Called from the HTTP handler thread."""
    if not hasattr(jobs, "sites"):
        jobs.sites = Sites(jobs.loop, engine=jobs.engine)
    s: Sites = jobs.sites
    profile = str(body.get("profile") or "")
    try:
        if action == "login":
            return 200, s.run(s.login(body.get("url"), profile), 90)
        if action == "close":
            profile_dir(profile)
            return 200, s.run(s.close(profile), 30)
        if action == "page":
            return 200, s.run(s.page(body.get("url"), profile), 120)
        if action == "submit":
            return 200, s.run(s.submit(body.get("url"), profile, int(body.get("form_index") or 0), body.get("values") or {},
                                       body.get("approved")), 120)
        if action == "download":
            return 200, s.run(s.download(body.get("url"), profile), 320)
    except PermissionError as e:
        return 403, {"detail": str(e)}
    except SiteError as e:
        return 400, {"detail": str(e)}
    return 404, {"detail": "Not found"}
