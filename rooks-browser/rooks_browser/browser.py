"""The browser: Playwright driving your installed Chrome or Edge (or Playwright's Chromium), or attached over CDP to a
Chrome you started yourself. No image, no bundled browser needed.

Every page load goes through the site policy (main frame and iframes; a redirect or new tab cannot leave the allowed
sites) and into the receipt. Each run gets a fresh, throwaway browser profile unless you attach over CDP, so nothing the
agent sees (cookies, form data, passwords) is kept after the run.
"""

from __future__ import annotations

import asyncio
import base64
import os
import platform
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from . import page_js
from .policy import SitePolicy
from .receipts import RunLog

ENGINES = ("chrome", "msedge", "chromium")


def installed_browsers() -> dict[str, str]:
    """Chrome and Edge executables on this computer (what Playwright's `channel` launches), by OS."""
    system = platform.system()
    found: dict[str, str] = {}
    cands: dict[str, list[str]] = {"chrome": [], "msedge": []}
    if system == "Darwin":
        cands["chrome"] = ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
        cands["msedge"] = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"]
    elif system == "Windows":
        roots = [os.getenv("PROGRAMFILES", r"C:\Program Files"), os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 os.getenv("LOCALAPPDATA", "")]
        cands["chrome"] = [str(Path(r) / "Google/Chrome/Application/chrome.exe") for r in roots if r]
        cands["msedge"] = [str(Path(r) / "Microsoft/Edge/Application/msedge.exe") for r in roots if r]
    else:
        cands["chrome"] = ["/opt/google/chrome/chrome", shutil.which("google-chrome") or "", shutil.which("google-chrome-stable") or ""]
        cands["msedge"] = ["/opt/microsoft/msedge/msedge", shutil.which("microsoft-edge") or ""]
    for name, paths in cands.items():
        for p in paths:
            if p and Path(p).exists():
                found[name] = p
                break
    return found


@dataclass
class Snapshot:
    url: str
    title: str
    elements: list[dict]
    text: str
    scroll_y: int = 0
    page_height: int = 0
    viewport_h: int = 0
    tabs: int = 1

    def element(self, eid) -> dict | None:
        try:
            i = int(str(eid).strip("[] "))
        except (TypeError, ValueError):
            return None
        return next((e for e in self.elements if e["id"] == i), None)

    def describe(self, text_chars: int = 2500) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}"]
        below = max(0, self.page_height - self.scroll_y - self.viewport_h)
        lines.append(f"Scroll: {self.scroll_y}px from top, {below}px more below" + (f", {self.tabs} tabs open" if self.tabs > 1 else ""))
        lines.append("Interactive elements ([id] role \"name\" details; * = currently on screen):")
        for e in self.elements:
            bits = []
            if e.get("type") and e["role"] in ("textbox", "button", "searchbox"):
                bits.append(e["type"])
            if e.get("value"):
                bits.append(f"value={e['value']!r}")
            if "checked" in e:
                bits.append("checked" if e["checked"] else "unchecked")
            if e.get("options"):
                bits.append("options=" + "|".join(e["options"]))
            if e.get("href") and e["role"] == "link":
                bits.append(_short(e["href"]))
            if e.get("is_submit"):
                bits.append("submits form")
            if e.get("disabled"):
                bits.append("disabled")
            lines.append(f"{'*' if e.get('in_view') else ' '}[{e['id']}] {e['role']} \"{e['name']}\"" + (f" ({', '.join(bits)})" if bits else ""))
        if not self.elements:
            lines.append("  (none)")
        text = self.text.strip()
        lines.append(f"Page text (first {min(len(text), text_chars)} of {len(text)} characters; use extract for more):")
        lines.append(text[:text_chars] or "(empty)")
        return "\n".join(lines)


def _short(url: str) -> str:
    u = urlsplit(url)
    s = f"{u.path or '/'}{'?' + u.query if u.query else ''}"
    return ("→ " + (s if len(s) < 70 else s[:67] + "...")) if u.scheme in ("http", "https", "file") else ""


@dataclass
class _Sub:
    count: int = 0
    failed: int = 0


class PolicyBlocked(Exception):
    pass


class Browser:
    def __init__(self, *, policy: SitePolicy, log: RunLog, headless: bool = True, engine: str = "auto",
                 cdp_url: str | None = None, downloads: Path | None = None, viewport=(1280, 860), slow_mo: int = 0,
                 overlay: bool | None = None):
        self.policy, self.log, self.headless, self.engine, self.cdp_url = policy, log, headless, engine, cdp_url
        self.downloads = Path(downloads or Path.cwd() / "downloads")
        self.viewport, self.slow_mo = viewport, slow_mo
        self._shown: tuple | None = None
        self.overlay = (not headless) if overlay is None else overlay
        self.engine_used = ""
        self.parent: int | None = None  # receipt row the next egress rows hang under (the current step)
        self.blocked: list[tuple[str, str]] = []
        self._pw = self._browser = self.context = self.page = None
        self._subs: dict[str, _Sub] = {}
        self._started: dict[int, float] = {}
        self.saved: list[dict] = []

    # lifecycle -----------------------------------------------------------------------------------------------------
    async def start(self) -> "Browser":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        if self.cdp_url:
            self._browser = await self._pw.chromium.connect_over_cdp(self.cdp_url)
            self.engine_used = f"cdp:{self.cdp_url}"
            self.context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
        else:
            order = [self.engine] if self.engine != "auto" else list(ENGINES)
            errors = []
            for eng in order:
                try:
                    kw = {"headless": self.headless, "slow_mo": self.slow_mo,
                          "args": ["--disable-features=PasswordManagerOnboarding", "--no-first-run", "--no-default-browser-check"]}
                    if not self.headless:  # a real window: size it, and let the page follow the window
                        kw["args"].append(f"--window-size={self.viewport[0]},{self.viewport[1] + 90}")
                    if eng != "chromium":
                        kw["channel"] = eng
                    self._browser = await self._pw.chromium.launch(**kw)
                    self.engine_used = eng
                    break
                except Exception as e:  # noqa: BLE001 - try the next engine, report all if none works
                    errors.append(f"{eng}: {str(e).splitlines()[0][:160]}")
            if not self._browser:
                await self._pw.stop()
                raise RuntimeError("No browser found. Install Google Chrome or Microsoft Edge, or run "
                                   "`python -m playwright install chromium`.\n  " + "\n  ".join(errors))
            size = {"no_viewport": True} if not self.headless else {"viewport": {"width": self.viewport[0], "height": self.viewport[1]}}
            self.context = await self._browser.new_context(**size, accept_downloads=True)
        await self.context.route("**/*", self._route)
        if self.overlay:
            await self.context.add_init_script(page_js.OVERLAY)
        self.context.on("request", self._on_request)
        self.context.on("requestfinished", self._on_finished)
        self.context.on("requestfailed", self._on_failed)
        self.context.on("page", self._on_page)
        self.page = await self.context.new_page()  # _on_page sets self.page and the download listener
        return self

    async def close(self) -> None:
        self.flush()
        try:
            if self.cdp_url:
                if self.page:
                    await self.page.close()  # leave the user's own browser running
            elif self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, *exc):
        await self.close()

    # policy and receipts -------------------------------------------------------------------------------------------
    async def _route(self, route, request):
        if request.resource_type == "document" or request.is_navigation_request():
            ok, why = self.policy.check(request.url)
            if not ok:
                self.blocked.append((request.url, why))
                self.log.egress(request.method, request.url, "blocked", 0, self.parent, tool="page", error=why)
                await route.abort("blockedbyclient")
                return
        elif any(_deny_hit(request.url, p) for p in self.policy.deny):
            # resources from a denied host are dropped too (trackers, embeds). With an allow list only, page resources
            # (CDNs, fonts) still load: they are not places the agent goes, and they are still in the receipt.
            await route.abort("blockedbyclient")
            return
        await route.continue_()

    def _on_request(self, request):
        self._started[id(request)] = time.monotonic()

    def _on_page(self, page):
        self.page = page  # follow new tabs (target=_blank, window.open)
        page.on("download", self._on_download)

    async def _on_download(self, dl):
        """Every download, whatever started it, lands in the downloads folder (never opened) and in the receipt."""
        self.downloads.mkdir(parents=True, exist_ok=True)
        path = _unique(self.downloads / _safe(dl.suggested_filename or "download"))
        try:
            await dl.save_as(str(path))
            self.log.egress("GET", dl.url, "ok", 0, self.parent, tool="download", detail=f"saved {path.name}")
            self.saved.append({"path": str(path), "bytes": path.stat().st_size})
        except Exception as e:  # noqa: BLE001
            self.log.egress("GET", dl.url, "error", 0, self.parent, tool="download", error=str(e)[:120])
            self.saved.append({"path": "", "bytes": 0, "error": str(e)[:200]})

    async def _on_finished(self, request):
        ms = int((time.monotonic() - self._started.pop(id(request), time.monotonic())) * 1000)
        if request.url.startswith(("data:", "blob:")):
            return
        if request.resource_type == "document":
            status = 0
            try:
                resp = await request.response()
                status = resp.status if resp else 0
            except Exception:  # noqa: BLE001
                pass
            self.log.egress(request.method, request.url, "ok" if status < 400 else f"http_{status}", ms, self.parent, tool="page")
        else:
            self._sub(request.url).count += 1

    def _on_failed(self, request):
        ms = int((time.monotonic() - self._started.pop(id(request), time.monotonic())) * 1000)
        if request.url.startswith(("data:", "blob:")):
            return
        err = (request.failure or "error")[:80]
        if request.resource_type == "document":
            if "BLOCKED_BY_CLIENT" in err.upper():
                return  # already recorded as blocked
            self.log.egress(request.method, request.url, "error", ms, self.parent, tool="page", error=err)
        else:
            s = self._sub(request.url)
            s.count += 1
            s.failed += 1

    def _sub(self, url: str) -> _Sub:
        u = urlsplit(url)
        key = f"{u.scheme}://{u.hostname}{f':{u.port}' if u.port else ''}/"
        return self._subs.setdefault(key, _Sub())

    def flush(self) -> None:
        """One egress row per host for the page resources loaded since the last flush."""
        for key, s in sorted(self._subs.items()):
            self.log.egress("GET", key, "ok" if not s.failed else "error", 0, self.parent, tool="resources",
                            detail=f"{s.count} page resource request(s), {s.failed} failed")
        self._subs.clear()

    # reading the page -------------------------------------------------------------------------------------------------
    async def snapshot(self, max_elements: int = 120) -> Snapshot:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        for _ in range(3):
            try:
                d = await self.page.evaluate(page_js.SNAPSHOT, max_elements)
                return Snapshot(**d, tabs=len(self.context.pages))
            except Exception as e:  # noqa: BLE001 - navigation in flight: wait and retry
                last = e
                await asyncio.sleep(0.7)
        raise RuntimeError(f"could not read the page: {str(last).splitlines()[0]}")

    async def screenshot(self, path: Path | None = None, full_page: bool = False, for_model: bool = False) -> bytes:
        if for_model and self.overlay:
            await self._overlay_call("hide")
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
        kw = {"type": "jpeg", "quality": 60} if for_model else {"type": "png"}
        try:
            return await self.page.screenshot(path=str(path) if path else None, full_page=full_page, timeout=15000, **kw)
        finally:
            if for_model and self.overlay and self._shown:
                await self._overlay_call("show", *self._shown)  # the model sees the page; the user keeps the banner

    async def screenshot_b64(self) -> str:
        return base64.b64encode(await self.screenshot(for_model=True)).decode()

    async def show(self, text: str, eid=None) -> None:
        self._shown = (text, eid)
        if self.overlay:
            await self._overlay_call("show", text, eid)

    async def _overlay_call(self, fn: str, *args) -> None:
        try:
            await self.page.evaluate(f"(a) => window.__rooks && window.__rooks.{fn}(...a)", list(args))
        except Exception:  # noqa: BLE001 - the overlay is only a courtesy
            pass

    # actions -------------------------------------------------------------------------------------------------------
    async def open(self, url: str) -> str:
        url = normalise_url(url)
        ok, why = self.policy.check(url)
        if not ok:
            self.blocked.append((url, why))
            self.log.egress("GET", url, "blocked", 0, self.parent, tool="page", error=why)
            raise PolicyBlocked(why)
        try:
            resp = await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:  # noqa: BLE001
            msg = str(e).splitlines()[0]
            if "ERR_BLOCKED_BY_CLIENT" in msg and self.blocked:
                raise PolicyBlocked(self.blocked[-1][1]) from e
            if "Download is starting" in msg:
                return "that URL is a file download; use the download action"
            raise
        await self._settle()
        return f"opened {self.page.url}" + (f" (HTTP {resp.status})" if resp is not None and resp.status >= 400 else "")

    def _loc(self, eid):
        return self.page.locator(f'[data-rb-id="{int(str(eid).strip("[] "))}"]').first

    async def click(self, eid) -> str:
        before, n = self.page.url, len(self.blocked)
        loc = self._loc(eid)
        await loc.scroll_into_view_if_needed(timeout=5000)
        await loc.click(timeout=8000)
        await self._settle()
        if len(self.blocked) > n:
            await self._leave_error_page(before)
            return f"clicked, but the page it leads to was blocked: {self.blocked[-1][1]}. Still at {self.page.url}"
        return "clicked" + (f", now at {self.page.url}" if self.page.url != before else "")

    async def _leave_error_page(self, before: str) -> None:
        if self.page.url.startswith("chrome-error:") or self.page.url != before:
            try:
                await self.page.go_back(wait_until="domcontentloaded", timeout=8000)
            except Exception:  # noqa: BLE001
                pass
            if self.page.url.startswith("chrome-error:") or self.page.url in ("", "about:blank"):
                try:
                    await self.page.goto(before, wait_until="domcontentloaded", timeout=15000)
                except Exception:  # noqa: BLE001
                    pass

    async def type(self, eid, text: str, submit: bool = False, clear: bool = True) -> str:
        loc = self._loc(eid)
        await loc.scroll_into_view_if_needed(timeout=5000)
        if clear:
            await loc.fill("", timeout=8000)
        await loc.fill(str(text), timeout=8000)
        if submit:
            before, n = self.page.url, len(self.blocked)
            await loc.press("Enter")
            await self._settle()
            if len(self.blocked) > n:
                await self._leave_error_page(before)
                return f"typed, but submitting was blocked: {self.blocked[-1][1]}"
            return f"typed and pressed Enter, now at {self.page.url}"
        return "typed"

    async def select(self, eid, value: str) -> str:
        loc = self._loc(eid)
        try:
            await loc.select_option(label=str(value), timeout=5000)
        except Exception:  # noqa: BLE001
            await loc.select_option(str(value), timeout=5000)
        await self._settle()
        return f"selected {value!r}"

    async def scroll(self, direction: str = "down") -> str:
        d = (direction or "down").lower()
        js = {"up": "window.scrollBy(0, -window.innerHeight * 0.8)", "top": "window.scrollTo(0, 0)",
              "bottom": "window.scrollTo(0, document.documentElement.scrollHeight)"}.get(d, "window.scrollBy(0, window.innerHeight * 0.8)")
        await self.page.evaluate(js)
        await asyncio.sleep(0.3)
        return f"scrolled {d}"

    async def back(self) -> str:
        await self.page.go_back(wait_until="domcontentloaded", timeout=15000)
        await self._settle()
        return f"went back to {self.page.url}"

    async def extract(self, max_chars: int = 8000) -> str:
        snap = await self.snapshot()
        return snap.text.strip()[:max_chars]

    async def download(self, eid=None, url: str | None = None) -> dict:
        self.downloads.mkdir(parents=True, exist_ok=True)
        if url:
            url = normalise_url(url, base=self.page.url)
            ok, why = self.policy.check(url)
            if not ok:
                self.log.egress("GET", url, "blocked", 0, self.parent, tool="download", error=why)
                raise PolicyBlocked(why)
            started = time.monotonic()
            r = await self.context.request.get(url, timeout=60000)
            self.log.egress("GET", url, "ok" if r.ok else f"http_{r.status}", int((time.monotonic() - started) * 1000),
                            self.parent, tool="download")
            if not r.ok:
                raise RuntimeError(f"download failed: HTTP {r.status}")
            name = _filename(r.headers.get("content-disposition", ""), url)
            path = _unique(self.downloads / name)
            path.write_bytes(await r.body())
            self.saved.append({"path": str(path), "bytes": path.stat().st_size})
            return self.saved[-1]
        n = len(self.saved)
        await self._loc(eid).click(timeout=8000)
        for _ in range(600):
            if len(self.saved) > n:
                d = self.saved[-1]
                if d.get("error"):
                    raise RuntimeError(f"download failed: {d['error']}")
                return d
            await asyncio.sleep(0.1)
        raise RuntimeError("clicking that did not start a download within 60 seconds")
        return {"path": str(path), "bytes": path.stat().st_size}

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:  # noqa: BLE001
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=2500)
        except Exception:  # noqa: BLE001
            pass


def _deny_hit(url: str, pattern: str) -> bool:
    return not SitePolicy(deny=[pattern]).check(url)[0]


def normalise_url(url: str, base: str | None = None) -> str:
    u = (url or "").strip().strip("'\"<>")
    if not u:
        raise ValueError("empty URL")
    if base and u.startswith("/"):
        b = urlsplit(base)
        return f"{b.scheme}://{b.netloc}{u}"
    if not re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        u = "https://" + u
    return u


def _safe(name: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip(" .") or "download"
    return name[:120]


def _filename(disposition: str, url: str) -> str:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?', disposition or "", re.I)
    return _safe(m.group(1) if m else (Path(urlsplit(url).path).name or "download"))


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        p = path.with_name(f"{path.stem} ({i}){path.suffix}")
        if not p.exists():
            return p
    return path
