"""ROOKS's own browser on your computer (Playwright, Apache-2.0, optional), in a separate profile so it never sees your
personal browser's cookies or passwords. Elements get refs from each snapshot; a ref from an older snapshot is refused,
so the page is looked at again after it changes. Password fields are never filled."""

from __future__ import annotations

import os
import threading

from .policy import Refused, check_element, label_matches

SNAPSHOT_JS = r"""
() => {
  document.querySelectorAll('[data-rooks-ref]').forEach(e => e.removeAttribute('data-rooks-ref'));
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, [role=button], [role=link], [role=checkbox], [role=tab], [contenteditable=true]';
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect();
    const st = getComputedStyle(el);
    if (r.width === 0 || r.height === 0 || st.visibility === 'hidden' || st.display === 'none') continue;
    const ref = 'r' + (++n);
    el.setAttribute('data-rooks-ref', ref);
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const lab = el.labels && el.labels[0] ? el.labels[0].innerText : '';
    const label = (el.getAttribute('aria-label') || lab || el.innerText || el.getAttribute('placeholder') || el.getAttribute('name') ||
                   (tag === 'input' && ['submit', 'button'].includes(type) ? el.value : '') || el.getAttribute('title') || '').trim().slice(0, 120);
    const secure = type === 'password';
    const value = secure ? '' : ((tag === 'input' || tag === 'textarea' || tag === 'select') ? String(el.value || '').slice(0, 120) : '');
    out.push({id: ref, role: el.getAttribute('role') || (tag === 'input' ? (type || 'text') + ' field' : tag), label, value, secure,
              href: tag === 'a' ? el.href : undefined});
    if (out.length >= 150) break;
  }
  return {url: location.href, title: document.title, text: document.body ? document.body.innerText.slice(0, 4000) : '', elements: out};
}"""


class Browser:
    """One Playwright browser, used from one thread (Playwright's sync API is not thread safe)."""

    def __init__(self, profile_dir: str, headless: bool = False, executable: str | None = None):
        self.profile_dir, self.headless, self.executable = profile_dir, headless, executable
        self._pw = self._ctx = self._page = None
        self._snapshot: dict[str, dict] = {}
        self._lock = threading.Lock()

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _ensure(self):
        if self._page:
            return self._page
        from playwright.sync_api import sync_playwright

        os.makedirs(self.profile_dir, exist_ok=True)
        self._pw = sync_playwright().start()
        kw = {"headless": self.headless}
        if self.executable:
            kw["executable_path"] = self.executable
        self._ctx = self._pw.chromium.launch_persistent_context(self.profile_dir, **kw)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        return self._page

    def close(self) -> None:
        try:
            if self._ctx:
                self._ctx.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = self._ctx = self._page = None

    def open(self, url: str) -> dict:
        if not url.startswith(("http://", "https://")):
            raise Refused("Only http and https pages can be opened.")
        page = self._ensure()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        self._snapshot = {}
        return {"url": page.url, "title": page.title()}

    def snapshot(self) -> dict:
        page = self._ensure()
        snap = page.evaluate(SNAPSHOT_JS)
        self._snapshot = {e["id"]: e for e in snap["elements"]}
        return snap

    def _el(self, ref: str, claimed: str) -> dict:
        el = self._snapshot.get(ref)
        if not el:
            raise Refused(f"{ref} is not in the latest browser snapshot. Take a new snapshot first.")
        if claimed and el.get("label") and not label_matches(claimed, el["label"]):
            raise Refused(f"{ref} is now '{el['label']}', not '{claimed}'. Take a new snapshot.")
        return el

    def click(self, ref: str, label: str, approved: bool) -> dict:
        el = self._el(ref, label)
        check_element(el, approved)
        page = self._ensure()
        page.locator(f'[data-rooks-ref="{ref}"]').first.click(timeout=10000)
        page.wait_for_load_state("domcontentloaded", timeout=15000)
        self._snapshot = {}  # the page may have changed: refs expire
        return {"url": page.url, "title": page.title()}

    def fill(self, ref: str, text: str, label: str) -> dict:
        el = self._el(ref, label)
        check_element(el, False, typing=True)
        self._ensure().locator(f'[data-rooks-ref="{ref}"]').first.fill(text, timeout=10000)
        return {"filled": ref}

    def select(self, ref: str, value: str, label: str) -> dict:
        self._el(ref, label)
        self._ensure().locator(f'[data-rooks-ref="{ref}"]').first.select_option(value, timeout=10000)
        return {"selected": value}

    def back(self) -> dict:
        page = self._ensure()
        page.go_back(wait_until="domcontentloaded", timeout=15000)
        self._snapshot = {}
        return {"url": page.url, "title": page.title()}
