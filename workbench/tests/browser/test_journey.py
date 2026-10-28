"""A new person's first session, in a real browser against a real ROOKS server: install (set up), connect free AI,
find every major area, ask for something, see it done, finish a task, write a note, plan a mission and change settings.

The AI is the stand-in in fake_ai.py (OpenRouter shaped, with real-format prices). Passing says the interface and the
server work together; it says nothing about how a real provider answers.

Usage: python3 test_journey.py
"""

import re
import sys

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, Stack, api, axe, browser, check, finish, shot

# v13: a short sidebar of areas; each area's pages are tabs at the top of the page, the rest is under More
SIDEBAR = ["Assistant", "Home", "Needs you", "Projects", "Library", "Planner", "Work", "Settings"]
TABS = {"Library": ["Notes", "Documents", "Files", "Research"], "Planner": ["Tasks", "Calendar", "Automations"],
        "Work": ["Missions", "Computer", "Agent team"]}
MORE = ["Mail", "Memory & skills", "What ROOKS can do", "Compare models"]


def go(p, name: str) -> None:
    """Open a page the way a person finds it: the sidebar, an area's tabs, or More."""
    side = p.locator("aside").get_by_role("link", name=name, exact=True)
    if side.count() and side.first.is_visible():
        side.first.click()
        return
    area = next((a for a, pages in TABS.items() if name in pages), None)
    if area:
        p.locator("aside").get_by_role("link", name=area, exact=True).click()
        p.get_by_role("navigation", name=area).get_by_role("link", name=name, exact=True).click()
        return
    p.locator("aside summary", has_text="More").click()
    p.locator("aside").get_by_role("link", name=name, exact=True).click()

with sync_playwright() as pw:
    st = Stack("journey")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 860}, timezone_id="Australia/Sydney")
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)

    # 1. Set up the workspace
    p.goto(st.url)
    p.get_by_role("button", name="Get started").click()
    p.fill("#new-password", PASSWORD)
    p.fill("#confirm-password", PASSWORD)
    p.get_by_role("button", name="Continue").click()
    p.fill("#ws-name", "Acme HQ")
    p.get_by_role("button", name="Create workspace").click()
    expect(p.get_by_role("heading", name="How should ROOKS use AI?")).to_be_visible()
    check("Free Cloud AI is the preselected choice", p.get_by_role("radio", name=re.compile("Free Cloud AI")).get_attribute("aria-checked") == "true")
    axe(p, "onboarding: AI choice")

    # 2. Connect free AI: a wrong key is explained, the right one is tested with a free model
    p.get_by_role("button", name="Continue with Free Cloud AI").click()
    p.fill("#provider-key", "sk-or-wrong")
    p.get_by_role("button", name="Connect", exact=True).click()
    err = p.locator(".alert.err")
    expect(err).to_contain_text("rejected the API key")
    check("a wrong key is explained with what to do", "Copy the key again" in err.inner_text())
    p.fill("#provider-key", "sk-or-test-good")
    p.get_by_role("button", name="Connect", exact=True).click()
    ok = p.locator(".alert.ok")
    expect(ok).to_contain_text("Connected and tested")
    check("the connected model is the one whose listed price is $0", "acme/helper:free" in ok.inner_text(), ok.inner_text())
    reqs = [r for r in st.ai_requests() if r["path"].endswith("/chat/completions")]
    check("only models listed at $0 were sent a request while connecting", all(r["model"].endswith(":free") for r in reqs), [r["model"] for r in reqs])
    check("a free model OpenRouter closes to apps like ROOKS is skipped, and the key is not blamed",
          reqs[0]["model"] == "acme/harness-only:free" and "rejected" not in ok.inner_text(), [r["model"] for r in reqs])
    shot(p, "journey-1-connected")
    p.get_by_role("button", name="Continue").click()
    expect(p.get_by_role("heading", name="You're ready")).to_be_visible()
    check("the last step says which timezone reminders use", "Australia/Sydney" in p.inner_text("body"))
    p.get_by_role("button", name="Open ROOKS").click()

    # 3. Every major area can be found from the sidebar and opens
    expect(p.get_by_role("heading", name=re.compile("Good (morning|afternoon|evening)"))).to_be_visible()
    missing = [a for a in SIDEBAR if p.locator("aside").get_by_role("link", name=a, exact=True).count() == 0]
    check("every area is in the sidebar", not missing, missing)
    check("the sidebar is short (8 links before More)", p.locator("aside nav[aria-label=Main] > a.nav-link").count() <= 8,
          p.locator("aside nav[aria-label=Main] > a.nav-link").count())
    for area, pages in TABS.items():
        p.locator("aside").get_by_role("link", name=area, exact=True).click()
        tabs = p.get_by_role("navigation", name=area)
        check(f"{area} shows its pages as tabs", all(tabs.get_by_role("link", name=n, exact=True).count() == 1 for n in pages), pages)
    for a in ["Projects", "Missions", "Research", "Computer", "Automations", "What ROOKS can do", "Settings", "Mail", "Agent team"]:
        go(p, a)
        expect(p.locator("main h1").first).to_be_visible()
    check("each page opens with its own heading", True)
    axe(p, "Settings after setup")

    # 4. Ask ROOKS from Home and see the work done
    go(p, "Home")
    p.fill("#home-ask", "Remind me to call the plumber tomorrow")
    p.get_by_role("button", name="Ask ROOKS").click()
    expect(p.get_by_text(re.compile("Call the plumber.*is on your task list for tomorrow"))).to_be_visible()
    check("the assistant answered and says what it did", True)
    tasks = api(p, "/tasks")["body"]
    task = next((t for t in tasks if t["title"] == "Call the plumber"), None)
    check("the task really exists, due tomorrow", task and task["due"], tasks)
    shot(p, "journey-2-answer")

    # 5. Finish the task in Tasks
    go(p, "Tasks")
    p.get_by_role("button", name='Mark "Call the plumber" as done').click()
    expect(p.get_by_text("Call the plumber")).to_have_count(0)
    done = [t for t in api(p, "/tasks?status=done")["body"] if t["title"] == "Call the plumber"] or \
           [t for t in api(p, "/tasks?include_done=true")["body"] if t["title"] == "Call the plumber" and t["status"] == "done"]
    check("completing the task is saved", done, done)

    # 6. Write a note and leave at once: the edit is kept
    go(p, "Notes")
    p.get_by_role("button", name="New note").click()
    title = p.get_by_label("Title", exact=True)
    title.fill("Plumber questions")
    body = p.locator("textarea.editor-body")
    body.fill("Ask about the hot water system.")
    go(p, "Home")  # no pause: the save must not be lost
    p.wait_for_timeout(800)
    notes = api(p, "/docs?kind=note")["body"]
    check("a note edited and left at once is saved", any("hot water" in (n.get("body") or n.get("preview") or "") for n in notes),
          [(n.get("title"), (n.get("preview") or "")[:40]) for n in notes])

    # 7. Plan a mission: it is listed first, above the ready made workflows
    go(p, "Missions")
    p.locator("textarea").first.fill("Plan the team offsite and draft the invitation")
    p.get_by_role("button", name="Plan it").click()
    expect(p.get_by_role("button", name=re.compile("Approve plan"))).to_be_visible(timeout=30000)
    item = p.get_by_role("list", name="Missions").get_by_role("listitem").first
    box = item.bounding_box()
    check("your mission is listed without scrolling", box and box["y"] + box["height"] <= 860, box)
    check("nothing runs before you approve the plan", "Check the plan" in p.inner_text("main") or "nothing has run" in p.inner_text("main").lower())

    # 8. Settings: the timezone and appearance show that they saved
    p.goto(st.url + "/settings#s-automations")
    tz = p.get_by_label("Timezone")
    expect(tz).to_be_visible()
    tz.select_option("Europe/London")
    p.locator("#s-automations").get_by_role("button", name="Save timezone").click()
    expect(p.locator(".save-state.ok").first).to_contain_text("Saved")
    check("the timezone change is confirmed and stored", api(p, "/assistant/settings")["body"]["timezone"] == "Europe/London")
    p.goto(st.url + "/settings#s-appearance")
    p.get_by_role("radio", name=re.compile("ROOKS dark")).first.click()
    expect(p.get_by_text("Saved for every device")).to_be_visible()
    check("an appearance change is confirmed", True)
    shot(p, "journey-3-settings")

    # 9. Free Cloud AI holds: the paid model is listed as unavailable and is never sent anything
    p.goto(st.url + "/assistant")
    opts = p.locator('select[aria-label="AI model"]').first.locator("option")
    expect(opts.filter(has_text="acme/pro")).to_have_count(1)  # the model list loads after the page
    labels = [opts.nth(i).inner_text() for i in range(opts.count())]
    disabled = [opts.nth(i).inner_text() for i in range(opts.count()) if opts.nth(i).is_disabled()]
    check("the paid model is shown as unavailable in Free Cloud AI", any("acme/pro" in d for d in disabled), labels)
    check("free models come first and can be picked", any("acme/helper:free" in label for label in labels if label not in disabled))
    check("no request ever reached the paid model", not [r for r in st.ai_requests() if r.get("model") == "acme/pro"])
    check("no page errors in the whole journey", not errors, errors[:3])
    b.close()
sys.exit(finish())
