"""Record what a person does in a browser window, for Workbench's "Show me how".

A visible browser opens (the same engines as jobs). A small script in every page reports clicks, typing, choices,
ticks, form submits and Enter presses, each described by meaning: role and accessible name (the same labelling the
agent's snapshot uses), label, placeholder and visible text. Navigations come from the browser itself. Password and
one-time-code fields are reported as secret with no value; nothing typed into them leaves the page.

  POST /api/recordings {"start_url"}       -> {"id", "status"}
  GET  /api/recordings/{id}                -> {"id", "status", "events": [...]}
  POST /api/recordings/{id}/stop           -> same, final; the window closes
"""

from __future__ import annotations

import asyncio
import time
import uuid

from .browser import ENGINES

RECORDER_JS = r"""
(() => {
  if (window.__rooksRec) return; window.__rooksRec = true;
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  const labelOf = el => {
    if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
    const by = el.getAttribute('aria-labelledby');
    if (by) { const t = by.split(/\s+/).map(i => document.getElementById(i)).filter(Boolean).map(e => e.innerText).join(' '); if (t) return t; }
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) return l.innerText; }
    const wrap = el.closest && el.closest('label'); if (wrap && wrap !== el) return wrap.innerText;
    return '';
  };
  const roleOf = el => {
    const r = el.getAttribute('role'); if (r) return r;
    const t = el.tagName.toLowerCase();
    if (t === 'a') return 'link';
    if (t === 'button' || t === 'summary') return 'button';
    if (t === 'select') return 'combobox';
    if (t === 'textarea') return 'textbox';
    if (t === 'input') {
      const ty = (el.type || 'text').toLowerCase();
      if (['submit', 'button', 'reset', 'image'].includes(ty)) return 'button';
      if (ty === 'checkbox' || ty === 'radio') return ty;
      if (ty === 'search') return 'searchbox';
      return 'textbox';
    }
    if (el.isContentEditable) return 'textbox';
    return 'generic';
  };
  const interactive = 'a[href], button, input, select, textarea, summary, [role], [onclick], [contenteditable=""], [contenteditable=true]';
  const describe = el => {
    const type = (el.getAttribute('type') || '').toLowerCase();
    const text = clean(el.innerText || (['submit', 'button'].includes(type) ? el.value : '') || '');
    const label = clean(labelOf(el));
    return { role: roleOf(el), name: clean(label || text || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || ''),
             label, placeholder: clean(el.getAttribute('placeholder') || ''), text, tag: el.tagName.toLowerCase(), type,
             href: el.tagName === 'A' ? el.href : '' };
  };
  const secret = el => {
    const t = (el.getAttribute('type') || '').toLowerCase(), ac = (el.getAttribute('autocomplete') || '').toLowerCase();
    return t === 'password' || ac.includes('one-time-code') || ac.startsWith('cc-') || /otp|cvv|cvc|card.?number/i.test(el.name || el.id || '');
  };
  const send = ev => { try { window.__rooksRecord(Object.assign({ url: location.href, title: document.title }, ev)); } catch (e) {} };
  document.addEventListener('click', e => {
    const el = e.target.closest ? e.target.closest(interactive) : null; if (!el) return;
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'select' || (tag === 'input' && !['submit', 'button', 'image', 'reset', 'checkbox', 'radio'].includes(type)) || tag === 'textarea') return;
    if (type === 'checkbox' || type === 'radio') return;  // reported by change
    send({ kind: 'click', target: describe(el) });
  }, true);
  document.addEventListener('change', e => {
    const el = e.target; if (!el || !el.tagName) return;
    const tag = el.tagName.toLowerCase(), type = (el.getAttribute('type') || '').toLowerCase();
    if (type === 'checkbox' || type === 'radio') return send({ kind: 'check', target: describe(el), value: el.checked ? 'on' : 'off' });
    if (tag === 'select') return send({ kind: 'select', target: describe(el), value: clean(el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : el.value) });
    if (tag === 'input' || tag === 'textarea') return send({ kind: 'type', target: describe(el), secret: secret(el), value: secret(el) ? '' : String(el.value || '').slice(0, 500) });
  }, true);
  document.addEventListener('submit', e => { send({ kind: 'submit', target: e.target ? describe(e.target) : {} }); }, true);
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter') return;
    const el = e.target; if (!el || !el.tagName) return;
    if (['input', 'textarea'].includes(el.tagName.toLowerCase())) {
      send({ kind: 'type', target: describe(el), secret: secret(el), value: secret(el) ? '' : String(el.value || '').slice(0, 500) });
      send({ kind: 'key', key: 'Enter', target: describe(el) });
    }
  }, true);
})();
"""


class Recording:
    def __init__(self, start_url: str = "", headless: bool = False, engine: str = "auto"):
        self.id = uuid.uuid4().hex[:12]
        self.start_url, self.headless, self.engine = start_url, headless, engine
        self.events: list[dict] = []
        self.status = "starting"
        self.error = ""
        self.t0 = time.time()
        self._pw = self._browser = self.context = self.page = None

    def _add(self, ev: dict) -> None:
        ev = {k: v for k, v in ev.items() if v not in (None, "")}
        ev["t"] = round(time.time() - self.t0, 2)
        if ev.get("secret"):
            ev["value"] = ""
        self.events.append(ev)

    async def start(self) -> "Recording":
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        order = [self.engine] if self.engine != "auto" else list(ENGINES)
        errors = []
        for eng in order:
            try:
                kw = {"headless": self.headless, "args": ["--no-first-run", "--no-default-browser-check"]}
                if eng != "chromium":
                    kw["channel"] = eng
                self._browser = await self._pw.chromium.launch(**kw)
                break
            except Exception as e:  # noqa: BLE001
                errors.append(f"{eng}: {str(e).splitlines()[0][:120]}")
        if not self._browser:
            await self._pw.stop()
            self.status, self.error = "failed", "No browser found: " + "; ".join(errors)
            raise RuntimeError(self.error)
        self.context = await self._browser.new_context(no_viewport=not self.headless)
        await self.context.expose_binding("__rooksRecord", lambda _src, ev: self._add(dict(ev or {})))
        await self.context.add_init_script(RECORDER_JS)
        self.page = await self.context.new_page()
        self.page.on("framenavigated", lambda f: f == self.page.main_frame and f.url != "about:blank"
                     and self._add({"kind": "navigate", "url": f.url}))
        self.status = "recording"
        if self.start_url:
            await self.page.goto(self.start_url, wait_until="domcontentloaded")
        return self

    async def stop(self) -> None:
        self.status = "stopped"
        try:
            if self._browser:
                await self._browser.close()
        finally:
            if self._pw:
                await self._pw.stop()

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "error": self.error, "start_url": self.start_url, "events": list(self.events)}


class Recordings:
    """Recording sessions on the server's event loop (one window each)."""

    def __init__(self, loop: asyncio.AbstractEventLoop, headless: bool = False, engine: str = "auto"):
        self.loop, self.headless, self.engine = loop, headless, engine
        self.items: dict[str, Recording] = {}

    def start(self, start_url: str = "") -> dict:
        if start_url and "://" not in start_url:
            start_url = "https://" + start_url
        rec = Recording(start_url, self.headless, self.engine)
        asyncio.run_coroutine_threadsafe(rec.start(), self.loop).result(60)
        self.items[rec.id] = rec
        return rec.to_dict()

    def get(self, rid: str) -> dict:
        return self.items[rid].to_dict()

    def stop(self, rid: str) -> dict:
        rec = self.items[rid]
        if rec.status == "recording":
            asyncio.run_coroutine_threadsafe(rec.stop(), self.loop).result(30)
        return rec.to_dict()
