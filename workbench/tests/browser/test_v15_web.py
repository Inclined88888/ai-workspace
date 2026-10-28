"""v15 in a real browser against a real ROOKS server with the stand-in AI: a conversation started from the terminal shows
up in the open browser by itself (live events), work shows the server's status words, and files open beside the
conversation as an image, a PDF or a table, each with a check that what is on disk is what was saved.

Usage: python3 test_v15_web.py
"""

import struct
import sys
import zlib

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, Stack, axe, browser, check, finish, shot

H = {"X-Workbench": "1"}


def png(w=40, h=24) -> bytes:
    raw = b"".join(b"\x00" + bytes([200, 60, 60] * (w // 2) + [60, 60, 200] * (w - w // 2)) for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 144]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj "
       b"4 0 obj<</Length 44>>stream\nBT /F1 18 Tf 20 70 Td (Quarterly memo) Tj ET\nendstream endobj "
       b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")

with sync_playwright() as pw:
    st = Stack("v15-web")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    rq = ctx.request
    rq.post(st.url + "/api/setup", headers=H, data={"password": PASSWORD})
    rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "custom"})
    rq.post(st.url + "/api/providers/connect", headers=H, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
    token = rq.post(st.url + "/api/cli-token", headers=H).json()["token"]
    ids = {}
    for name, mime, data in (("team-photo.png", "image/png", png()), ("quarterly-memo.pdf", "application/pdf", PDF),
                             ("sales-figures.csv", "text/csv", b"Region,Revenue\nNorth,1500.50\nSouth,800\nEast,\n")):
        ids[name] = rq.post(st.url + f"/api/files?name={name}", headers={**H, "Content-Type": mime}, data=data).json()["id"]
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)
    p.goto(st.url + "/")
    expect(p.get_by_role("heading", name="What can I help you get done?")).to_be_visible()

    # A conversation started in the terminal appears in the open browser, without reloading
    term = pw.request.new_context(base_url=st.url, extra_http_headers={"Authorization": f"Bearer {token}", "X-Workbench": "1", "X-Rooks-API": "1"})
    r = term.post("/api/v1/chat", data={"message": "How should I plan my week?", "channel": "cli"})
    cid = next(int(l.split('"conversation_id": ')[1].split("}")[0].split(",")[0]) for l in r.text().splitlines() if '"conversation_id"' in l)
    link = p.locator(f'aside a.recent-link[href="/assistant/{cid}"]')
    expect(link).to_be_visible(timeout=10000)
    check("a conversation started in the terminal shows up in the open browser by itself", True)
    link.click()
    expect(p.get_by_text("Pick three outcomes")).to_be_visible()
    check("the browser opens the terminal's conversation with its reply", True)
    term.dispose()

    # Files open beside the conversation: image, PDF and spreadsheet, each checked on disk
    p.locator("aside").get_by_role("button", name="New chat").click()
    p.get_by_label("Message ROOKS").fill("Find the file team photo")
    p.keyboard.press("Enter")
    expect(p.get_by_text("Here is what I found.")).to_be_visible()
    p.get_by_role("button", name="Preview").last.click()
    panel = p.get_by_role("complementary", name="File preview")
    img = panel.locator("img.preview-image")
    expect(img).to_be_visible()
    check("an image opens as an image", p.evaluate("(i) => i.complete && i.naturalWidth === 40", img.element_handle()))
    expect(panel.get_by_role("status")).to_have_text("Saved and checked: what is on disk is what ROOKS wrote.")
    check("the preview says the file on disk is the one that was saved", True)
    shot(p, "v15-preview-image")
    p.keyboard.press("Escape")

    p.get_by_label("Message ROOKS").fill("Find the file quarterly memo")
    p.keyboard.press("Enter")
    expect(p.get_by_text("Here is what I found.").nth(1)).to_be_visible()
    p.get_by_role("button", name="Preview").last.click()
    frame = panel.locator("iframe.preview-pdf")
    expect(frame).to_be_visible()
    resp = p.request.get(st.url + f"/api/v1/files/{ids['quarterly-memo.pdf']}/view")
    check("a PDF is shown inside ROOKS, as a PDF, never sniffed", resp.status == 200 and resp.headers["content-type"] == "application/pdf"
          and resp.headers.get("x-content-type-options") == "nosniff" and resp.headers["content-disposition"].startswith("inline"), resp.headers)
    p.keyboard.press("Escape")

    p.get_by_label("Message ROOKS").fill("Find the file sales figures")
    p.keyboard.press("Enter")
    expect(p.get_by_text("Here is what I found.").nth(2)).to_be_visible()
    p.get_by_role("button", name="Preview").last.click()
    table = panel.locator("table.preview-table")
    expect(table).to_be_visible()
    check("a spreadsheet opens as a table with its header and rows",
          table.locator("th").all_inner_texts() == ["Region", "Revenue"] and table.locator("tbody tr").count() == 3)
    axe(p, "spreadsheet preview")
    shot(p, "v15-preview-table")
    p.keyboard.press("Escape")

    # Other files are never shown inline
    other = rq.post(st.url + "/api/files?name=page.html", headers={**H, "Content-Type": "text/html"}, data=b"<script>alert(1)</script>").json()["id"]
    check("an HTML upload is refused for inline viewing", p.request.get(st.url + f"/api/v1/files/{other}/view").status == 415)
    img_resp = p.request.get(st.url + f"/api/v1/files/{ids['team-photo.png']}/view")
    check("an image is served inline with a sandbox policy", "sandbox" in img_resp.headers.get("content-security-policy", ""))

    check("no page errors", not errors, errors[:3])
    b.close()
sys.exit(finish())
