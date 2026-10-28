"""v14: the app shell and the workflows around it, in a real browser against a real ROOKS server with the stand-in AI.

Covers: the assistant as the first screen, New chat, conversations in the sidebar, collapsing the sidebar, every
Workspace page reachable, continuing a project (workflow D), honest connection states (workflow E's setup side), the
phone drawer, reduced motion and dark mode. Passing says the interface and the server work together; it says nothing
about how a real provider answers.

Usage: python3 test_v14_shell.py
"""

import re
import sys

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, Stack, api, axe, browser, check, finish, no_overflow, shot

H = {"X-Workbench": "1"}
WELCOME = "What can I help you get done?"

with sync_playwright() as pw:
    st = Stack("v14-shell")
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 860}, timezone_id="Australia/Sydney")
    rq = ctx.request
    rq.post(st.url + "/api/setup", headers=H, data={"password": PASSWORD})
    rq.put(st.url + "/api/ai-mode", headers=H, data={"mode": "custom"})
    rq.post(st.url + "/api/providers/connect", headers=H, data={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
    pid = rq.post(st.url + "/api/projects", headers=H, data={"name": "Kitchen renovation", "goals": "Finish by March"}).json()["id"]
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=15000)
    side = p.locator("aside.sidebar")

    # The first screen is the assistant, ready to type into
    p.goto(st.url + "/")
    expect(p.get_by_role("heading", name=WELCOME)).to_be_visible()
    check("the first screen is the assistant, with the message box focused",
          p.evaluate("() => document.activeElement?.getAttribute('aria-label')") == "Message ROOKS")
    check("no conversation list column and no Chat/Memory/Skills/Settings tabs on the assistant",
          p.locator(".split-list").count() == 0 and p.get_by_role("tab", name="Memory").count() == 0)
    starts = [t.strip() for t in p.locator(".start-chip").all_inner_texts()]
    check("four plain starting points under the message box", starts == ["Write", "Research", "Analyse", "Plan"], starts)
    p.locator(".start-chip", has_text="Plan").click()
    check("a starting point fills the message box instead of sending", p.get_by_label("Message ROOKS").input_value().startswith("Plan "))
    axe(p, "assistant, new chat")
    shot(p, "v14-home")

    # Ask: the conversation gets its own address and appears in the sidebar as the current one
    p.get_by_label("Message ROOKS").fill("How should I plan my week?")
    p.keyboard.press("Enter")
    expect(p.get_by_text("Pick three outcomes")).to_be_visible()
    expect(p).to_have_url(re.compile(r"/assistant/\d+$"))
    cid = int(p.url.rsplit("/", 1)[1])
    current = side.locator(f'a.recent-link[href="/assistant/{cid}"]')
    expect(current).to_have_attribute("aria-current", "page")
    check("the new conversation is listed in the sidebar and marked current", True)
    axe(p, "assistant, conversation")
    shot(p, "v14-conversation")

    # New chat: back to an empty start, the earlier conversation still one click away
    side.get_by_role("button", name="New chat").click()
    expect(p.get_by_role("heading", name=WELCOME)).to_be_visible()
    check("New chat starts empty", p.get_by_label("Message ROOKS").input_value() == "" and p.url.rstrip("/") == st.url)
    current.click()
    expect(p.get_by_text("Pick three outcomes")).to_be_visible()
    check("a conversation in the sidebar opens with its messages", p.url.endswith(f"/assistant/{cid}"))

    # Collapse the sidebar: remembered after a reload, everyday items keep their names for screen readers
    side.get_by_role("button", name="Collapse sidebar").click()
    expect(p.locator(".shell.collapsed")).to_have_count(1)
    check("collapsed, the sidebar hides the conversation list", side.locator(".recent").count() == 0)
    p.reload()
    expect(p.locator(".shell.collapsed")).to_have_count(1)
    check("the collapsed sidebar is remembered", True)
    check("collapsed items still have accessible names", side.get_by_role("link", name="Projects").count() == 1)
    side.get_by_role("button", name="Expand sidebar").click()
    expect(p.locator(".shell.collapsed")).to_have_count(0)

    # Every Workspace page opens with its own heading; grouped pages keep their tabs
    fold = side.get_by_role("button", name="Workspace")
    fold.click()
    names = [n.strip() for n in side.locator("#workspace-pages a.nav-link").all_inner_texts()]
    dead = []
    for name in names:
        side.locator("#workspace-pages").get_by_role("link", name=name, exact=True).click()
        try:
            expect(p.locator("main h1").first).to_be_visible(timeout=8000)
        except AssertionError:
            dead.append(name)
    check(f"every Workspace page opens ({len(names)} pages)", not dead and len(names) >= 10, dead or names)
    side.locator("#workspace-pages").get_by_role("link", name="Tasks and calendar").click()
    tabs = p.get_by_role("navigation", name="Tasks and calendar")
    check("Tasks, Calendar and Automations stay one click apart", [t.strip() for t in tabs.get_by_role("link").all_inner_texts()] == ["Tasks", "Calendar", "Automations"])
    tabs.get_by_role("link", name="Automations").click()
    expect(p.get_by_role("heading", name="Automations")).to_be_visible()

    # Workflow D: continue a project. A chat started from the project works inside it
    p.goto(st.url + f"/projects/{pid}")
    p.get_by_role("link", name="New chat in this project").click()
    expect(p.get_by_role("heading", name="What should we do in Kitchen renovation?")).to_be_visible()
    p.get_by_label("Message ROOKS").fill("Remind me to call the tiler tomorrow")
    p.keyboard.press("Enter")
    expect(p.get_by_text(re.compile("Call the tiler.*is on your task list"))).to_be_visible()
    pcid = int(re.search(r"/assistant/(\d+)", p.url).group(1))
    conv = api(p, f"/conversations/{pcid}")["body"]
    check("D the conversation belongs to the project", conv.get("project_id") == pid, conv.get("project_id"))
    chip = p.locator(".chat-project")
    check("D the conversation shows which project it works in, linked", chip.inner_text() == "Kitchen renovation" and chip.get_attribute("href") == f"/projects/{pid}")
    task = next((t for t in api(p, "/tasks")["body"] if t["title"] == "Call the tiler"), None)
    check("D the task it created is in the project", task and task.get("project_id") == pid, task)
    side.get_by_role("button", name="New chat").click()
    side.locator(f'a.recent-link[href="/assistant/{pcid}"]').click()
    expect(p.locator(".chat-project")).to_have_text("Kitchen renovation")
    check("D reopening it later from the sidebar keeps the project", True)
    shot(p, "v14-project-chat")

    # Workflow E (setup side): connection states say what was proven, the AirLLM bridge is not offered as everyday
    p.goto(st.url + "/settings#models")
    card = p.locator("#connection-directory")
    expect(card).to_be_visible()
    legend = card.locator(".state-key").inner_text()
    check("E the four states are explained", all(s in legend for s in ("Verified", "Connected, not tested", "Available, not set up", "Experimental")), legend)
    row = lambda name: card.locator("li.conn-row", has=p.locator("strong", has_text=name)).first  # noqa: E731
    check("E the stand-in AI that answered a request with a tool attached is Verified", "Verified" in row("Stand-in AI").inner_text())
    check("E AirLLM is Experimental, not an everyday provider", "Experimental" in row("AirLLM").inner_text())
    codex = row("OpenAI Codex CLI").inner_text()
    check("E a coding agent with no paired computer is Available, not set up", "Available, not set up" in codex and "Verified" not in codex, codex)
    dflt = p.locator("label.field", has_text="Default model").locator("select")
    check("E the default model is Automatic, naming the model it uses now", re.match(r"Automatic \(now .+\)", dflt.locator("option:checked").text_content()) is not None,
          dflt.locator("option:checked").text_content())
    shot(p, "v14-connections")

    # Phone: the sidebar is a drawer; nothing scrolls sideways
    ph = b.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, timezone_id="Australia/Sydney",
                       storage_state=ctx.storage_state())
    m = ph.new_page()
    m.on("pageerror", lambda e: errors.append(str(e)))
    m.goto(st.url + "/")
    expect(m.get_by_role("heading", name=WELCOME)).to_be_visible()
    check("phone: the sidebar starts closed", not m.locator("aside.sidebar.open").count())
    check("phone: no sideways scrolling on the first screen", no_overflow(m))
    m.get_by_role("button", name="Open menu").click()
    expect(m.locator("aside.sidebar.open")).to_be_visible()
    m.locator("aside.sidebar").locator(f'a.recent-link[href="/assistant/{cid}"]').click()
    expect(m.locator("aside.sidebar.open")).to_have_count(0)
    expect(m.get_by_text("Pick three outcomes")).to_be_visible()
    check("phone: choosing a conversation closes the drawer and opens it", True)
    check("phone: no sideways scrolling in a conversation", no_overflow(m))
    m.get_by_role("button", name="Open menu").click()
    m.keyboard.press("Escape")
    expect(m.locator("aside.sidebar.open")).to_have_count(0)
    check("phone: Escape closes the drawer", True)
    shot(m, "v14-phone-conversation")
    ph.close()

    # Reduced motion: nothing animates; dark mode passes the same accessibility checks
    rm = b.new_context(viewport={"width": 1280, "height": 860}, reduced_motion="reduce", color_scheme="dark", storage_state=ctx.storage_state())
    r = rm.new_page()
    r.on("pageerror", lambda e: errors.append(str(e)))
    r.goto(st.url + f"/assistant/{cid}")
    expect(r.get_by_text("Pick three outcomes")).to_be_visible()
    dur = r.evaluate("() => [...document.querySelectorAll('aside.sidebar, .shell, .btn, .msg, .recent-link')].map(e => getComputedStyle(e))"
                     ".flatMap(s => [s.transitionDuration, s.animationDuration]).flatMap(v => v.split(',')).map(v => parseFloat(v) * (v.trim().endsWith('ms') ? 1 : 1000))"
                     ".reduce((a, b) => Math.max(a, b), 0)")
    check("reduced motion: no transition or animation longer than a millisecond", dur <= 1, dur)
    axe(r, "dark, conversation")
    r.goto(st.url + "/")
    axe(r, "dark, new chat")
    shot(r, "v14-dark-home")
    rm.close()

    check("no page errors", not errors, errors[:3])
    b.close()
sys.exit(finish())
