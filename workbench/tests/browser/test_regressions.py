"""One check per bug found in the v12 audit, so none comes back. Each says what used to go wrong.

Usage: python3 test_regressions.py
"""

import re
import sys

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, Stack, api, browser, check, finish

H = {"X-Workbench": "1"}

with sync_playwright() as pw:
    st = Stack("regressions")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 860}, timezone_id="Australia/Sydney")
    rq = ctx.request
    rq.post(st.url + "/api/setup", headers=H, data={"password": PASSWORD})
    rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "custom"})
    rq.put(st.url + "/api/assistant/settings", headers=H, data={"timezone": "UTC"})
    rq.post(st.url + "/api/providers/connect", headers=H, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
    for q in ("How should I plan my week?", "Remind me to water the plants"):
        rq.post(st.url + "/api/assistant/chat", headers=H, data={"message": q})
    convs = rq.get(st.url + "/api/conversations", headers=H).json()
    a, c2 = convs[1]["id"], convs[0]["id"]
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)

    # Was: a reply still arriving for one conversation was written into the conversation you switched to
    p.goto(st.url + f"/assistant/{a}")
    p.locator("textarea").first.fill("Take your time with this one")
    p.keyboard.press("Enter")
    p.wait_for_timeout(500)
    p.locator(f'a.recent-link[href="/assistant/{c2}"]').click()
    p.wait_for_timeout(6000)
    check("a slow reply does not appear in the conversation you moved to", p.get_by_text("slow answer").count() == 0)
    check("you stay where you moved to", p.url.endswith(f"/assistant/{c2}"), p.url)
    p.locator(f'a.recent-link[href="/assistant/{a}"]').click()
    expect(p.get_by_text("Here is the slow answer you asked for.")).to_be_visible()
    check("the reply is saved in its own conversation", True)

    # Was: a new chat that finished after you left pulled you back to it
    p.goto(st.url + "/assistant")
    p.locator("textarea").first.fill("Take your time, new chat")
    p.keyboard.press("Enter")
    p.wait_for_timeout(500)
    p.locator(f'a.recent-link[href="/assistant/{c2}"]').click()
    p.wait_for_timeout(6000)
    check("a new chat finishing later does not take you away from where you are", p.url.endswith(f"/assistant/{c2}"), p.url)

    # Was: what you typed followed you into other conversations (and could be sent to the wrong one)
    p.goto(st.url + f"/assistant/{a}")
    p.locator("textarea").first.fill("draft for the first conversation")
    p.locator(f'a.recent-link[href="/assistant/{c2}"]').click()
    expect(p).to_have_url(st.url + f"/assistant/{c2}")
    check("another conversation starts with its own empty message box", p.locator("textarea").first.input_value() == "")
    p.locator(f'a.recent-link[href="/assistant/{a}"]').click()
    expect(p.locator("textarea").first).to_have_value("draft for the first conversation")
    check("going back brings the draft back", True)

    # Was: an edit made less than 0.7 s before switching notes was silently dropped
    n1 = rq.post(st.url + "/api/docs", headers=H, data={"title": "First", "body": "one", "kind": "note"}).json()
    n2 = rq.post(st.url + "/api/docs", headers=H, data={"title": "Second", "body": "two", "kind": "note"}).json()
    p.goto(st.url + f"/notes/{n1['id']}")
    p.locator("textarea.editor-body").fill("one, and an important addition")
    p.locator(f'a.list-link[href="/notes/{n2["id"]}"]').click()
    p.wait_for_timeout(800)
    check("switching notes straight after typing keeps the edit", "important addition" in api(p, f"/docs/{n1['id']}")["body"]["body"])
    # Was: a failed save cleared the status, so it looked saved
    expect(p.get_by_label("Title", exact=True)).to_have_value("Second")
    p.route(re.compile(r".*/api/docs/\d+$"), lambda route: route.fulfill(status=500, body='{"detail": "Disk full"}')
            if route.request.method == "PATCH" else route.continue_())
    p.locator("textarea.editor-body").fill("two, edited while saving fails")
    expect(p.get_by_text("Not saved")).to_be_visible()
    check("a failed save says 'Not saved' and offers Retry", p.get_by_role("button", name="Retry").count() == 1)
    p.unroute(re.compile(r".*/api/docs/\d+$"))
    p.get_by_role("button", name="Retry").click()
    expect(p.locator(".save-state.ok")).to_contain_text("Saved")
    stored = api(p, f"/docs/{n2['id']}")["body"]["body"]
    check("Retry saves it", "saving fails" in stored, stored)

    # Was: your own missions were listed below five workflow templates, off screen
    rq.post(st.url + "/api/missions", headers=H, data={"goal": "Sort out the quarterly report"})
    p.goto(st.url + "/missions")
    yours = p.get_by_role("heading", name="Your missions").bounding_box()
    tmpl = p.get_by_text("Start from a workflow").bounding_box()
    check("your missions come before the ready made workflows", yours and tmpl and yours["y"] < tmpl["y"], (yours, tmpl))
    check("the workflows are folded away once you have missions", p.locator("details:has-text('Start from a workflow')").get_attribute("open") is None)

    # Was: Stop and Take control were offered while Computer Use was off and nothing could run
    p.goto(st.url + "/computer")
    sw = p.get_by_role("switch", name=re.compile("Computer Use"))
    expect(sw).to_be_visible()
    check("with Computer Use off there is no Stop or Take control", p.get_by_role("button", name="Stop now").count() == 0
          and p.get_by_role("button", name="Take control").count() == 0)
    sw.click()
    expect(p.get_by_role("button", name="Stop now")).to_be_visible()
    check("turning it on shows Stop now", True)
    sw.click()
    expect(p.get_by_role("button", name="Stop now")).to_have_count(0)

    # Was: a project's budget had to be typed in cents
    pid = rq.post(st.url + "/api/projects", headers=H, data={"name": "Garden"}).json()["id"]
    p.goto(st.url + f"/projects/{pid}")
    budget = p.get_by_label(re.compile("Monthly budget in US dollars"))
    budget.fill("12.50")
    p.get_by_role("button", name="Save").first.click()
    p.wait_for_timeout(500)
    check("a $12.50 budget is stored as 1250 cents", api(p, f"/projects/{pid}")["body"]["budget_cents"] == 1250)

    # Was: automations ran in UTC with no hint, so 8:00 meant the evening in Sydney
    p.goto(st.url + "/automations")
    expect(p.get_by_text(re.compile("this browser is in Australia/Sydney"))).to_be_visible()
    p.get_by_role("button", name="Use Australia/Sydney").click()
    expect(p.get_by_text("Automations now run in Australia/Sydney")).to_be_visible()
    check("one click moves automations to your timezone", api(p, "/assistant/settings")["body"]["timezone"] == "Australia/Sydney")

    # Was: research showed two empty boxes side by side
    p.goto(st.url + "/research")
    expect(p.get_by_text("No research yet")).to_be_visible()
    check("research with no reports shows one clear empty state", p.locator("main .empty").count() == 1, p.locator("main .empty").count())

    # Was: the computer status was fetched every 2 seconds even with Computer Use off
    p.goto(st.url + "/overview")
    seen = []
    p.on("request", lambda r: seen.append(r.url) if r.url.endswith("/api/computer") else None)
    p.wait_for_timeout(20000)
    # Overview itself reads it once for its summary; the bar then checks every 15 s (it used to be every 2 s: about 10 here)
    check("with Computer Use off the status is checked at most every 15 seconds", len(seen) <= 3, len(seen))

    # Was: research, mail and other lists opened on click only (no keyboard); now each row has a real link or button
    for path in ("/notes", "/assistant", "/files"):
        p.goto(st.url + path)
        p.wait_for_load_state("networkidle")
        bad = p.evaluate("() => [...document.querySelectorAll('main .list-item')].filter(r => r.onclick || (r.getAttribute('onclick'))).length")
        check(f"{path}: list rows are reachable by keyboard", bad == 0)

    # Free Cloud AI: models it does not allow are visible but cannot be picked
    rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "free"})
    p.goto(st.url + "/assistant")
    opt = p.locator('select[aria-label="AI model"] option', has_text="stand-in-model").last
    expect(opt).to_be_attached()
    check("in Free Cloud AI an unverified model is listed but disabled", opt.is_disabled())
    check("no page errors", not errors, errors[:3])
    b.close()
sys.exit(finish())
