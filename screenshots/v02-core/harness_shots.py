import json, os, time
from pathlib import Path
from playwright.sync_api import sync_playwright
B = "http://127.0.0.1:8940"; FAKE = "http://127.0.0.1:8942/v1"
OUT = Path("/workspace/ai-workspace-v02a/screenshots/v02-core"); OUT.mkdir(parents=True, exist_ok=True)
H = {"X-Workbench": "1"}
def chat(r, msg, cid=None):
    res = r.post(f"{B}/api/assistant/chat", data={"message": msg, "conversation_id": cid, "channel": "web"}, headers=H, timeout=60000)
    txt = res.text(); first = next(l for l in txt.splitlines() if l.startswith("data:"))
    print("chat", res.status, txt[-300:].replace("\n", " "))
    return json.loads(first[5:])["conversation_id"]
with sync_playwright() as p:
    br = p.chromium.launch()
    ctx = br.new_context(viewport={"width": 1440, "height": 960})
    r = ctx.request
    print(r.post(f"{B}/api/setup", data={"password": "testpassword1"}, headers=H).status, r.post(f"{B}/api/login", data={"password": "testpassword1"}, headers=H).status)
    pid = r.post(f"{B}/api/providers", data={"name": "Demo", "kind": "openai", "base_url": FAKE, "api_key": "x"}, headers=H).json()["id"]
    r.put(f"{B}/api/settings", data={"default_model": f"{pid}/demo-model", "timezone": "Australia/Brisbane"}, headers=H)
    r.post(f"{B}/api/login", data={"password": "testpassword1"}, headers=H)
    print("tz", r.put(f"{B}/api/assistant/settings", data={"timezone": "Australia/Brisbane"}, headers=H).status)
    for t in ({"title": "Order bathroom tiles", "due": "2026-11-06", "notes": "Smith St bathroom", "project": "Smith St"},
              {"title": "Book the plumber for the rough-in", "notes": "Smith St", "project": "Smith St"}):
        print("task", r.post(f"{B}/api/tasks", data=t, headers=H).status)
    smith = r.post(f"{B}/api/projects", data={"name": "12 Smith St renovation", "kind": "job", "description": "Kitchen and bathroom renovation for Jane Lee.",
                                              "keywords": "Smith St, Jane Lee, Coastal Tiles"}, headers=H).json()
    print("project", smith.get("id"), str(smith)[:120])
    S = smith["id"]
    for it in ({"kind": "person", "title": "Jane Lee", "body": "Client · 0412 555 018"},
               {"kind": "person", "title": "Dave Moretti", "body": "Plumber"},
               {"kind": "deadline", "title": "Order bathroom tiles", "due": "2026-11-06"},
               {"kind": "deadline", "title": "Council inspection", "due": "2026-11-20"},
               {"kind": "note", "title": "Tile quote", "body": "Tile quote from Coastal Tiles: $4,820 including GST, 3 week lead time."},
               {"kind": "link", "ref": "https://example.com/smith-st-plans", "title": "Kitchen plans (shared folder)"}):
        print("item", r.post(f"{B}/api/projects/{S}/items", data=it, headers=H).status)
    for pr in ({"name": "Year 11 Chemistry", "kind": "course", "description": "Term 4 unit on reaction rates.", "keywords": "chemistry, reaction rates"},
               {"name": "Harbour Café brand refresh", "kind": "studio", "description": "New menu boards and logo.", "keywords": "Harbour Café, menu"}):
        print("project", r.post(f"{B}/api/projects", data=pr, headers=H).status)
    r.put(f"{B}/api/projects/active", data={"project_id": S}, headers=H)
    print("doc", r.post(f"{B}/api/docs", data={"title": "Smith St – tile quote", "body": "Tile quote from Coastal Tiles: $4,820 including GST, 3 week lead time.", "kind": "note"}, headers=H).status)
    c1 = chat(r, "What's still outstanding on the Smith St job?")
    r.put(f"{B}/api/checked/mode", data={"conversation_id": c1, "on": True}, headers=H)
    c2 = chat(r, "Book the plumber for Smith St and set up a site meeting Tuesday 9am. Also remember Jane prefers texts.")
    print("seed", r.post(f"{B}/shot/seed-email", headers=H).status)
    r.put(f"{B}/api/settings", data={"assistant_name": "Sunny"}, headers=H)
    pg = ctx.new_page()
    def shot(url, name, before=None, full=False):
        pg.goto(B + url); pg.wait_for_load_state("networkidle"); time.sleep(0.8)
        if before: before(); time.sleep(1.2)
        pg.screenshot(path=str(OUT / name), full_page=full); print("shot", name)
    shot("/projects", "01-projects-list.png")
    shot(f"/projects/{S}", "02-project-detail.png")
    def check():
        pg.get_by_role("button", name="Check this").first.click(); pg.wait_for_selector(".v02-check, mark", timeout=20000); time.sleep(1)
        pg.locator(".v02-claim").nth(1).click(); pg.locator(".v02-checked summary").first.click()
    shot(f"/assistant/{c1}", "03-chat-project-switcher-and-checked-answer.png", check)
    shot("/history", "04-history-timeline.png")
    def undo():
        pg.get_by_role("button", name="Undo").first.click(); time.sleep(1.5)
    shot("/history", "05-history-after-undo.png", undo)
    def remote_like():
        pg.get_by_text("Email").first.click()
    shot("/history", "06-history-cant-undo-fix.png", lambda: pg.get_by_role("textbox").first.fill("Tiles"))
    shot("/history", "07-history-help-me-fix.png", lambda: pg.get_by_role("button", name="Help me fix it").first.click())
    Path("/tmp/v02shot/free").touch()
    def new_proj():
        b = pg.get_by_role("button", name="New project")
        if b.count(): b.first.click(); time.sleep(0.5)
    shot("/projects", "08-free-plan-project-limit.png", new_proj)
    shot("/history", "09-free-plan-export-upgrade.png", lambda: pg.get_by_role("button", name="Spreadsheet").first.click())
    Path("/tmp/v02shot/free").unlink()
    m = br.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, storage_state=ctx.storage_state())
    pg = m.new_page()
    shot(f"/projects/{S}", "10-phone-web-project.png")
    shot("/history", "11-phone-web-history.png")
    br.close()
