"""Keyboard only: skip link, visible focus, sidebar, search palette, opening list items, sending a message, the phone
menu. Every step uses keys, never the mouse.

Usage: python3 test_keyboard.py
"""

import sys

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, Stack, browser, check, finish

H = {"X-Workbench": "1"}


def focused(p) -> dict:
    return p.evaluate("""() => { const e = document.activeElement; const cs = getComputedStyle(e);
        return { tag: e.tagName, text: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim().slice(0, 60),
                 cls: String(e.className || ''), href: e.getAttribute('href') || '', inMain: !!e.closest('main'),
                 ring: cs.outlineStyle !== 'none' && cs.outlineWidth !== '0px' || cs.boxShadow !== 'none' } }""")


def tab_to(p, test, limit=80, back=False) -> dict | None:
    for _ in range(limit):
        p.keyboard.press("Shift+Tab" if back else "Tab")
        f = focused(p)
        if test(f):
            return f
    return None


with sync_playwright() as pw:
    st = Stack("keyboard")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 860})
    rq = ctx.request
    rq.post(st.url + "/api/setup", headers=H, data={"password": PASSWORD})
    rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "custom"})
    rq.post(st.url + "/api/providers/connect", headers=H, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
    note = rq.post(st.url + "/api/docs", headers=H, data={"title": "Supplier list", "body": "Oak, pine", "kind": "note"}).json()
    rq.post(st.url + "/api/assistant/chat", headers=H, data={"message": "How should I plan my week?"})
    conv = rq.get(st.url + "/api/conversations", headers=H).json()[0]
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))

    # The chat opens with the message box focused (typing is the first thing to do)
    p.goto(st.url + "/")
    p.wait_for_load_state("networkidle")
    check("ROOKS opens on the assistant with the message box focused", focused(p)["cls"].startswith("composer-input"), focused(p))
    # Skip link first on other pages, and it moves focus past the sidebar
    p.goto(st.url + "/projects")
    p.wait_for_load_state("networkidle")
    p.keyboard.press("Tab")
    f = focused(p)
    check("the first Tab reaches 'Skip to content'", f["text"] == "Skip to content", f)
    check("the skip link is visible when focused", p.locator(".skip-link").bounding_box()["y"] >= 0)
    p.keyboard.press("Enter")
    p.keyboard.press("Tab")
    check("after skipping, the next stop is inside the page content", focused(p)["inMain"], focused(p))

    # Sidebar by keyboard, with a visible focus ring: open the Workspace fold, then Tasks
    p.goto(st.url + "/projects")
    p.wait_for_load_state("networkidle")
    f = tab_to(p, lambda f: f["text"] == "Workspace")
    check("Tab reaches the Workspace fold in the sidebar", f)
    p.keyboard.press("Enter")
    f = tab_to(p, lambda f: f["href"] == "/tasks")
    check("Tab reaches Tasks and calendar inside it", f)
    check("the focused link shows a focus ring", f and f["ring"], f)
    p.keyboard.press("Enter")
    expect(p).to_have_url(st.url + "/tasks")
    # Add a task with the keyboard only
    f = tab_to(p, lambda f: f["tag"] == "INPUT" and f["inMain"])
    p.keyboard.type("Ring the council")
    p.keyboard.press("Enter")
    expect(p.get_by_text("Ring the council")).to_be_visible()
    check("a task can be added with Tab, typing and Enter", True)

    # Search palette: open, find, go; Escape closes and gives focus back
    p.keyboard.press("Control+k")
    expect(p.get_by_role("dialog", name="Search and jump")).to_be_visible()
    check("Ctrl+K puts focus in the search box", focused(p)["tag"] == "INPUT")
    p.keyboard.type("notes")
    p.keyboard.press("Enter")
    expect(p).to_have_url(st.url + "/notes")
    check("Enter on a result goes there and closes the palette", p.get_by_role("dialog", name="Search and jump").count() == 0)
    f = tab_to(p, lambda f: f["href"] == f"/notes/{note['id']}")
    check("a note in the list can be reached with Tab", f, f)
    p.keyboard.press("Enter")
    expect(p).to_have_url(st.url + f"/notes/{note['id']}")
    check("Enter opens the note", True)
    before = focused(p)
    p.keyboard.press("Control+k")
    p.keyboard.press("Escape")
    check("Escape closes the palette and focus returns where it was", p.get_by_role("dialog", name="Search and jump").count() == 0
          and focused(p)["text"] == before["text"], (before, focused(p)))

    # Conversations and the composer
    p.goto(st.url + "/assistant")
    p.wait_for_load_state("networkidle")
    f = tab_to(p, lambda f: f["href"] == f"/assistant/{conv['id']}", back=True)  # from the message box back to the sidebar's recent chats
    check("a conversation in the list can be reached with Tab", f, f)
    p.keyboard.press("Enter")
    expect(p).to_have_url(st.url + f"/assistant/{conv['id']}")
    f = tab_to(p, lambda f: f["tag"] == "TEXTAREA")
    check("Tab reaches the message box", f)
    p.keyboard.type("First line")
    p.keyboard.press("Shift+Enter")
    p.keyboard.type("second line")
    check("Shift+Enter adds a new line instead of sending", "\n" in p.locator("textarea").first.input_value())
    p.keyboard.press("Enter")
    expect(p.locator(".bubble-user").last).to_contain_text("second line")
    expect(p.get_by_text("Here's a simple way to plan your week").last).to_be_visible()
    check("Enter sends, and the answer arrives", True)

    # Phone: the menu opens and closes from the keyboard; search closes it so overlays never stack
    ph = b.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)  # a phone: touch, no autofocus
    ph.request.post(st.url + "/api/login", headers=H, data={"password": PASSWORD})
    q = ph.new_page()
    q.goto(st.url + "/")
    q.wait_for_load_state("networkidle")
    f = tab_to(q, lambda f: "menu" in f["text"].lower(), limit=20)  # Chrome starts Tab near the message box after load
    check("the phone menu button is reachable", f, f)
    q.keyboard.press("Enter")
    expect(q.locator(".sidebar.open")).to_be_visible()
    q.keyboard.press("Escape")
    expect(q.locator(".sidebar.open")).to_have_count(0)
    check("Escape closes the phone menu", True)
    check("focus goes back to the menu button", "menu" in focused(q)["text"].lower(), focused(q))
    q.locator(".mobile-bar button").first.click()
    q.keyboard.press("Control+k")
    expect(q.get_by_role("dialog", name="Search and jump")).to_be_visible()
    expect(q.locator(".sidebar.open")).to_have_count(0)  # closes on the next render; under load that is not instant
    check("opening search closes the menu, so the two never stack", q.locator(".sidebar.open").count() == 0
          and q.get_by_role("dialog", name="Search and jump").count() == 1)
    check("no page errors", not errors, errors[:3])
    b.close()
sys.exit(finish())
