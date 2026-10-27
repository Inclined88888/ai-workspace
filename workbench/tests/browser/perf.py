"""Measured performance of the built app, served by a real ROOKS server with the stand-in AI. Prints JSON.

What it measures (median of several runs where it says so):
  cold_load        first visit to Home: first contentful paint, DOM ready, Home content visible, JS bytes, requests
  cold_load_slow   the same with the CPU slowed 4x (a modest laptop or phone)
  routes           sidebar click to the new page's content, per page (5 runs each)
  conversation     opening a conversation of 120 messages with long answers, until the last one is on screen
  tasks_1000       opening Tasks with 1,000 open tasks, until the list is on screen
  chart            opening a conversation with usage charts, until the chart is drawn
  idle_requests    API requests the Home page makes in 60 seconds with nobody touching it

Usage: python3 perf.py [out.json]
"""

import json
import statistics
import sys
import time

from playwright.sync_api import sync_playwright

from harness import PASSWORD, Stack, browser

ROUTES = [("Tasks", "/tasks"), ("Notes", "/notes"), ("Projects", "/projects"), ("Missions", "/missions"), ("Research", "/research"),
          ("Assistant", "/assistant"), ("Settings", "/settings"), ("Home", "/")]
out: dict = {}


def ready(p, timeout=20000):
    # content is on screen: no skeleton or spinner left inside the main area
    p.wait_for_function("""() => { const m = document.querySelector('main'); return m && m.innerText.trim().length > 20
        && !m.querySelector('.skeleton, .spin') }""", timeout=timeout)


with sync_playwright() as pw:
    st = Stack("perf")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1366, "height": 860})
    h = {"X-Workbench": "1"}
    rq = ctx.request
    rq.post(st.url + "/api/setup", headers=h, data={"password": PASSWORD})
    rq.put(st.url + "/api/ai-mode", headers=h, data={"mode": "custom"})
    rq.post(st.url + "/api/providers/connect", headers=h, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})

    def cold(throttle: int):
        runs = []
        for _ in range(3):
            c = b.new_context(viewport={"width": 1366, "height": 860})
            c.request.post(st.url + "/api/login", headers=h, data={"password": PASSWORD})
            p = c.new_page()
            cdp = c.new_cdp_session(p)
            cdp.send("Network.enable")
            cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
            if throttle > 1:
                cdp.send("Emulation.setCPUThrottlingRate", {"rate": throttle})
            reqs = []
            p.on("requestfinished", lambda r: reqs.append(r))
            t0 = time.monotonic()
            p.goto(st.url + "/")
            ready(p)
            visible = (time.monotonic() - t0) * 1000
            nav = p.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0];
                const f = performance.getEntriesByName('first-contentful-paint')[0];
                return { dom: n.domContentLoadedEventEnd, fcp: f ? f.startTime : null } }""")
            js = sum((r.sizes().get("responseBodySize") or 0) for r in reqs if r.url.endswith(".js"))
            runs.append({"fcp_ms": round(nav["fcp"] or 0), "dom_ready_ms": round(nav["dom"]), "content_visible_ms": round(visible),
                         "js_bytes": js, "requests": len(reqs)})
            c.close()
        return {k: statistics.median(r[k] for r in runs) for k in runs[0]}

    out["cold_load"] = cold(1)
    out["cold_load_slow"] = cold(4)

    # route transitions inside the app
    p = ctx.new_page()
    ctx.request.post(st.url + "/api/login", headers=h, data={"password": PASSWORD})
    p.goto(st.url + "/")
    ready(p)
    routes = {}
    for _ in range(5):
        for label, path in ROUTES:
            link = p.locator(f'aside a.nav-link[href="{path}"]').first
            t0 = time.monotonic()
            link.click()
            p.wait_for_url(f"**{path}" if path != "/" else st.url + "/")
            ready(p)
            routes.setdefault(label, []).append((time.monotonic() - t0) * 1000)
    out["routes_ms"] = {k: round(statistics.median(v)) for k, v in routes.items()}

    # a long conversation
    cid = None
    for i in range(60):
        r = rq.post(st.url + "/api/assistant/chat", headers=h, data={"message": f"Give me a long answer about planning, part {i}", **({"conversation_id": cid} if cid else {})})
        if cid is None:
            cid = next(json.loads(l[5:])["conversation_id"] for l in r.text().splitlines() if l.startswith("data:") and "conversation_id" in l)
    times = []
    for _ in range(3):
        t0 = time.monotonic()
        p.goto(st.url + f"/assistant/{cid}")
        p.wait_for_function("() => document.querySelectorAll('.bubble-user').length >= 60", timeout=30000)
        times.append((time.monotonic() - t0) * 1000)
    out["conversation_120_messages_ms"] = round(statistics.median(times))

    # a big task list
    for i in range(1000):
        rq.post(st.url + "/api/tasks", headers=h, data={"title": f"Task number {i}"})
    times = []
    for _ in range(3):
        t0 = time.monotonic()
        p.goto(st.url + "/tasks")
        p.get_by_text("Task number 0", exact=True).first.wait_for(timeout=30000)
        times.append((time.monotonic() - t0) * 1000)
    out["tasks_1000_ms"] = round(statistics.median(times))

    # charts
    r = rq.post(st.url + "/api/assistant/chat", headers=h, data={"message": "Show my AI spending"})
    ccid = next(json.loads(l[5:])["conversation_id"] for l in r.text().splitlines() if l.startswith("data:") and "conversation_id" in l)
    times = []
    for _ in range(3):
        t0 = time.monotonic()
        p.goto(st.url + f"/assistant/{ccid}")
        p.locator(".viz").first.wait_for(timeout=20000)
        times.append((time.monotonic() - t0) * 1000)
    out["chart_ms"] = round(statistics.median(times))

    # background requests on an idle Home page
    p.goto(st.url + "/")
    ready(p)
    seen = []
    p.on("request", lambda r: seen.append(r.url) if "/api/" in r.url else None)
    p.wait_for_timeout(60000)
    out["idle_requests_60s"] = len(seen)
    out["idle_request_paths"] = sorted({u.split("/api")[1].split("?")[0] for u in seen})
    b.close()

text = json.dumps(out, indent=1)
print(text)
if len(sys.argv) > 1:
    open(sys.argv[1], "w").write(text)
