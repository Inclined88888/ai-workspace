"""One-off browser actions with Playwright: open, click, type, screenshot, read text, list links.

Each call launches a fresh headless Chromium, does one thing and closes it. Selectors use Playwright syntax:
CSS ("button.submit"), text ("text=Sign in"), role ("role=link[name='Docs']") or XPath ("xpath=//h1").
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import AsyncIterator

DEFAULT_TIMEOUT_MS = int(os.getenv("BROWSER_ACTION_TIMEOUT_MS", "30000"))


@dataclass
class PageInfo:
    url: str
    title: str
    status: int | None = None
    screenshot: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize(url: str) -> str:
    if "://" not in url and not url.startswith(("about:", "data:")):
        return f"https://{url}"
    return url


@asynccontextmanager
async def page_session(headless: bool = True, viewport: tuple[int, int] = (1280, 900)) -> AsyncIterator:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        launch: dict = {"headless": headless}
        if os.getenv("CHROME_PATH"):
            launch["executable_path"] = os.environ["CHROME_PATH"]
        if os.getenv("IN_DOCKER", "").lower() == "true":
            launch["args"] = ["--no-sandbox", "--disable-dev-shm-usage"]
        browser = await pw.chromium.launch(**launch)
        try:
            context = await browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
            page = await context.new_page()
            page.set_default_timeout(DEFAULT_TIMEOUT_MS)
            yield page
        finally:
            await browser.close()


async def _goto(page, url: str) -> int | None:
    response = await page.goto(_normalize(url), wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001 - busy pages never go idle; content is usable anyway
        pass
    return response.status if response else None


async def _shot(page, path: str | None, full_page: bool) -> str | None:
    if not path:
        return None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=path, full_page=full_page)
    return str(Path(path).resolve())


async def open_url(url: str, *, screenshot: str | None = None, full_page: bool = False, headless: bool = True) -> PageInfo:
    async with page_session(headless) as page:
        status = await _goto(page, url)
        return PageInfo(page.url, await page.title(), status, await _shot(page, screenshot, full_page))


async def click(url: str, selector: str, *, screenshot: str | None = None, headless: bool = True) -> PageInfo:
    async with page_session(headless) as page:
        await _goto(page, url)
        await page.locator(selector).first.click()
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
        except Exception:  # noqa: BLE001
            pass
        return PageInfo(page.url, await page.title(), None, await _shot(page, screenshot, False))


async def type_text(url: str, selector: str, text: str, *, submit: bool = False, screenshot: str | None = None,
                    headless: bool = True) -> PageInfo:
    async with page_session(headless) as page:
        await _goto(page, url)
        field = page.locator(selector).first
        await field.fill(text)
        if submit:
            await field.press("Enter")
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10000)
            except Exception:  # noqa: BLE001
                pass
        return PageInfo(page.url, await page.title(), None, await _shot(page, screenshot, False))


async def screenshot(url: str, path: str = "screenshot.png", *, full_page: bool = False, selector: str | None = None,
                     headless: bool = True) -> PageInfo:
    async with page_session(headless) as page:
        status = await _goto(page, url)
        if selector:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            await page.locator(selector).first.screenshot(path=path)
            saved = str(Path(path).resolve())
        else:
            saved = await _shot(page, path, full_page)
        return PageInfo(page.url, await page.title(), status, saved)


async def page_text(url: str, *, selector: str | None = None, max_chars: int = 20000, headless: bool = True) -> str:
    async with page_session(headless) as page:
        await _goto(page, url)
        text = await page.locator(selector or "body").first.inner_text()
        return text[:max_chars]


async def links(url: str, *, headless: bool = True, limit: int = 200) -> list[dict]:
    async with page_session(headless) as page:
        await _goto(page, url)
        items = await page.eval_on_selector_all(
            "a[href]", "els => els.map(a => ({text: (a.innerText || '').trim().slice(0, 120), href: a.href}))"
        )
        seen, out = set(), []
        for item in items:
            if item["href"] not in seen and item["href"].startswith(("http://", "https://")):
                seen.add(item["href"])
                out.append(item)
        return out[:limit]
