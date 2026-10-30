import json, time
from pathlib import Path
from playwright.sync_api import sync_playwright
B = "http://127.0.0.1:8940"; P = "http://127.0.0.1:8945"
OUT = Path("/workspace/ai-workspace-v02a/screenshots/v02-core")
H = {"X-Workbench": "1"}
with sync_playwright() as p:
    br = p.chromium.launch()
    desk = br.new_context()
    r = desk.request
    r.post(f"{B}/api/login", data={"password": "testpassword1"}, headers=H)
    code = r.post(f"{B}/api/mobile/pair/start", headers=H).json()["code"]
    claim = r.post(f"{B}/api/mobile/pair/claim", data={"code": code, "name": "Phone", "platform": "ios"}, headers=H).json()
    pairing = {"token": claim["device_token"], "urls": [P], "active": P, "name": "Studio", "deviceId": claim.get("device_id", 1)}
    ctx = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    ctx.add_init_script(f"sessionStorage.setItem('aiworkspace.pairing.v1', {json.dumps(json.dumps(pairing))});")
    pg = ctx.new_page()
    def shot(path, name, before=None):
        pg.goto(P + path); pg.wait_for_load_state("networkidle"); time.sleep(1.5)
        if before: before(); time.sleep(1.5)
        pg.screenshot(path=str(OUT / name)); print("shot", name)
    shot("/", "12-phone-app-home-links.png")
    shot("/projects", "13-phone-app-projects.png")
    shot("/history", "14-phone-app-history.png")
    def check():
        pg.get_by_text("✓ Check this").first.click(); time.sleep(2.5)
        pg.get_by_text("The tile quote from Coastal Tiles was $4,280.").last.click(); time.sleep(0.5)
        pg.mouse.wheel(0, 600)
    shot("/chat/1", "15-phone-app-chat-checked.png", check)
    br.close()
