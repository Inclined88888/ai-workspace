"""Screenshots of the main screens at desktop and phone width, light and dark, for before-and-after comparison.

Usage: python3 tour.py OUT_DIR [label]
"""

import os
import sys

from playwright.sync_api import sync_playwright

from harness import PASSWORD, Stack, browser

out = sys.argv[1]
label = sys.argv[2] if len(sys.argv) > 2 else "shot"
os.makedirs(out, exist_ok=True)
H = {"X-Workbench": "1"}
with sync_playwright() as pw:
    st = Stack("tour")
    b = browser(pw)
    for name, vp in (("desktop", {"width": 1440, "height": 900}), ("phone", {"width": 390, "height": 844})):
        for scheme in ("light", "dark"):
            ctx = b.new_context(viewport=vp, color_scheme=scheme)
            rq = ctx.request
            if name == "desktop" and scheme == "light":
                rq.post(st.url + "/api/setup", headers=H, data={"password": PASSWORD})
                rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "custom"})
                rq.post(st.url + "/api/providers/connect", headers=H, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
                for q in ("How should I plan my week?", "Remind me to water the plants", "Save a note with ideas for the offsite"):
                    rq.post(st.url + "/api/assistant/chat", headers=H, data={"message": q})
            else:
                rq.post(st.url + "/api/login", headers=H, data={"password": PASSWORD})
            p = ctx.new_page()
            cid = rq.get(st.url + "/api/conversations", headers=H).json()[0]["id"]
            for page, path in (("home", "/"), ("assistant-new", "/assistant"), ("conversation", f"/assistant/{cid}"), ("settings", "/settings"),
                               ("projects", "/projects")):
                p.goto(st.url + path)
                p.wait_for_load_state("networkidle")
                p.wait_for_timeout(400)
                p.screenshot(path=os.path.join(out, f"{label}-{page}-{name}-{scheme}.png"))
            ctx.close()
    b.close()
print("saved to", out)
