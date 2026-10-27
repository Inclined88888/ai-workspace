"""Every major screen at desktop, tablet and phone width, in light and dark: screenshot, sideways scrolling, page
errors and (with AXE_JS) accessibility. Content is created through the real API, with the stand-in AI answering.

Usage: python3 screens.py [light|dark|both] [desktop,tablet,phone]
"""

import sys

from playwright.sync_api import sync_playwright

from harness import PASSWORD, Stack, axe, browser, check, finish, no_overflow, shot

THEMES = {"both": ["light", "dark"], "light": ["light"], "dark": ["dark"]}[sys.argv[1] if len(sys.argv) > 1 else "both"]
SIZES = {"desktop": (1366, 860), "tablet": (820, 1180), "phone": (390, 844)}
WANT = (sys.argv[2].split(",") if len(sys.argv) > 2 else list(SIZES))
ROUTES = ["/", "/needs-you", "/assistant", "/projects", "/missions", "/research", "/computer", "/automations", "/tasks", "/calendar",
          "/notes", "/documents", "/files", "/mail", "/assistant-memory", "/company", "/compare", "/settings", "/capabilities"]


def seed(req, url):
    h = {"X-Workbench": "1"}
    post = lambda p, d: req.post(url + "/api" + p, headers=h, data=d).json()  # noqa: E731
    post("/setup", {"password": PASSWORD})
    req.put(url + "/api/ai-mode", headers=h, data={"mode": "custom"})
    return h, post


with sync_playwright() as pw:
    st = Stack("screens")
    b = browser(pw)
    ctx = b.new_context()
    h, post = seed(ctx.request, st.url)
    r = post("/providers/connect", {"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
    check("stand-in AI connected", r.get("ok"), r.get("error"))
    pid = post("/projects", {"name": "Kitchen renovation", "goals": "Finish by June within $40,000"})["id"]
    post(f"/projects/{pid}/notes", {"kind": "question", "text": "Which benchtop supplier?"})
    post("/tasks", {"title": "Pay the electricity bill", "due": "2020-01-01"})
    post("/tasks", {"title": "Book the plumber", "priority": 2})
    post("/docs", {"title": "Meeting notes", "body": "# Notes\n\n- Benchtop: oak\n- Budget: $40k", "kind": "note"})
    post("/docs", {"title": "Supplier brief", "body": "## Scope\n\nSupply and install.", "kind": "document"})
    mid = post("/missions", {"goal": "Organise the team offsite and draft the invite"})["id"]
    ctx.request.post(st.url + "/api/assistant/chat", headers=h, data={"message": "How should I plan my week?"})
    ctx.request.post(st.url + "/api/assistant/chat", headers=h, data={"message": "Remind me to call the plumber tomorrow"})
    convs = ctx.request.get(st.url + "/api/conversations", headers=h).json()
    routes = ROUTES + [f"/assistant/{convs[0]['id']}", f"/missions/{mid}", f"/projects/{pid}"]
    ctx.close()

    for theme in THEMES:
        for size in WANT:
            w, hgt = SIZES[size]
            c = b.new_context(viewport={"width": w, "height": hgt}, color_scheme=theme, is_mobile=size == "phone", has_touch=size != "desktop")
            c.request.post(st.url + "/api/login", headers={"X-Workbench": "1"}, data={"password": PASSWORD})
            for route in routes:
                p = c.new_page()
                errors = []
                p.on("pageerror", lambda e: errors.append(str(e)))
                p.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
                p.goto(st.url + route)
                p.wait_for_load_state("networkidle")
                p.wait_for_timeout(300)
                name = route.strip("/").replace("/", "-") or "home"
                check(f"{size} {theme} {route}: no sideways scrolling", no_overflow(p))
                # regression (v12): the sidebar used to stretch the page past the window, so it scrolled into blank space
                check(f"{size} {theme} {route}: the page itself never scrolls past the window",
                      p.evaluate("document.documentElement.scrollHeight <= window.innerHeight + 1"))
                check(f"{size} {theme} {route}: no page errors", not errors, errors[:2])
                if size != "tablet":
                    axe(p, f"{size} {theme} {route}")
                shot(p, f"{size}-{theme}-{name}")
                p.close()
            c.close()
    b.close()
sys.exit(finish())
