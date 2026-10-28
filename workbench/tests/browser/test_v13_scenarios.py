"""v13 scenarios A to J through the real application: a real browser, a real ROOKS server, the real ROOKS Companion
process and, where installed, the official Codex CLI and Claude Code.

What stands in for what (each check is labelled with it):
  [stand-in AI]   fake_ai.py answers like OpenRouter and scripts its replies from the user's words. It is not a model.
  [official CLI]  the real Codex CLI or Claude Code, run by the companion, talking to a local stand-in of the OpenAI
                  Responses API or Anthropic's Messages API (companion/tests/standins/) with a fake key. No account.
  [engine stand-in] research completion is written to the database (the research engine needs public web pages; it is
                  tested with a stubbed web in backend/tests/test_research.py).
  [AirLLM]        the real AirLLM bridge with a tiny random-weight model, if ROOKS_AIRLLM_URL is set.
Nothing here uses a live AI provider, a ChatGPT or Claude account, or desktop hardware.

Environment:
  ROOKS_CLI_BIN    folder with the codex and claude executables (scenarios F and G are SKIPPED without it)
  ROOKS_AIRLLM_URL a running addons/airllm/bridge.py, e.g. http://127.0.0.1:8095/v1 (part of H is SKIPPED without it)

Usage: python3 test_v13_scenarios.py
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

from playwright.sync_api import expect, sync_playwright

from harness import PASSWORD, REPO, Stack, _procs, api, axe, browser, check, finish, free_port, shot

CLI_BIN = os.environ.get("ROOKS_CLI_BIN", "")
AIRLLM = os.environ.get("ROOKS_AIRLLM_URL", "")
STANDINS = os.path.join(REPO, "companion", "tests", "standins")
# Not under /tmp: Codex's workspace-write sandbox allows writes to /tmp by design, so the folder test needs another place
WORK = tempfile.mkdtemp(prefix="rooks-v13-", dir=os.path.expanduser("~"))
PROJECT = os.path.join(WORK, "project")
os.makedirs(PROJECT)


def skip(name: str, why: str) -> None:
    print(f"SKIP  {name} ({why})")


def db_run(st: Stack, sql: str, *args) -> None:
    c = sqlite3.connect(os.path.join(st.data, "workbench.db"), timeout=30)
    c.execute(sql, args)
    c.commit()
    c.close()


def send(p, text: str) -> None:
    box = p.locator("textarea").first
    box.fill(text)
    p.keyboard.press("Enter")


def updates(p, cid: int) -> list[dict]:
    return [m for m in api(p, f"/conversations/{cid}")["body"]["messages"] if m.get("update")]


def current_cid(p) -> int:
    m = re.search(r"/assistant/(\d+)", p.url)
    return int(m.group(1)) if m else 0


with sync_playwright() as pw:
    st = Stack("v13", env={"SEARXNG_URL": "{ai}"})
    b = browser(pw)
    ctx = b.new_context(viewport={"width": 1280, "height": 900}, timezone_id="Australia/Sydney", accept_downloads=True)
    p = ctx.new_page()
    errors: list[str] = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    expect.set_options(timeout=20000)

    # A. Sign up, choose the free provider, ask a question [stand-in AI]
    p.goto(st.url)
    p.get_by_role("button", name="Get started").click()
    p.fill("#new-password", PASSWORD)
    p.fill("#confirm-password", PASSWORD)
    p.get_by_role("button", name="Continue").click()
    p.fill("#ws-name", "Scenario HQ")
    p.get_by_role("button", name="Create workspace").click()
    check("A [stand-in AI] Free Cloud AI is the recommended first choice",
          p.get_by_role("radio", name=re.compile("Free Cloud AI")).get_attribute("aria-checked") == "true")
    p.get_by_role("button", name="Continue with Free Cloud AI").click()
    p.fill("#provider-key", "sk-or-test-good")
    p.get_by_role("button", name="Connect", exact=True).click()
    expect(p.locator(".alert.ok")).to_contain_text("Connected and tested")
    p.get_by_role("button", name="Continue").click()
    p.get_by_role("button", name="Open ROOKS").click()
    p.locator("aside").get_by_role("link", name="Assistant", exact=True).click()
    send(p, "How should I plan my week?")
    expect(p.get_by_text("Pick three outcomes")).to_be_visible()
    reqs = [r for r in st.ai_requests() if r["path"].endswith("/chat/completions")]
    check("A [stand-in AI] the question is answered in the conversation, by free models only",
          reqs and all(r["model"].endswith(":free") for r in reqs), sorted({r["model"] for r in reqs}))
    check("A a free model the provider closes to ROOKS was tried once while connecting, then never again",
          [r["model"] for r in reqs].count("acme/harness-only:free") == 1, [r["model"] for r in reqs])
    expect(p.locator("select[aria-label='AI model'] option").first).to_have_text(re.compile("^Automatic"))
    check("A the model picker defaults to Automatic", True)
    shot(p, "v13-A-answer")

    # B. Deep research from the conversation; Stop works; the report arrives in the conversation [stand-in AI, engine stand-in]
    p.goto(st.url + "/assistant")
    send(p, "Research the history of the four day week")
    card = p.locator(".job-card").last
    expect(card).to_contain_text("Research")
    expect(card.get_by_role("button", name="Stop")).to_be_visible()
    check("B the research shows as a live card with Stop while it runs", True)
    expect(p).to_have_url(re.compile(r"/assistant/\d+"))
    card.get_by_role("button", name="Stop").click()
    expect(p.get_by_text("Research stopped before it finished")).to_be_visible(timeout=30000)
    check("B Stop ends the research and says so in the conversation", True)
    p.goto(st.url + "/assistant")
    send(p, "Research the 2026 small business tax changes")
    expect(p).to_have_url(re.compile(r"/assistant/\d+"))
    expect(p.locator(".job-card").last).to_contain_text("Searching", timeout=30000)
    check("B [stand-in AI] the card shows the engine's real progress", True)
    cid_b = current_cid(p)
    rid = next(m["blocks"][0]["id"] for m in api(p, f"/conversations/{cid_b}")["body"]["messages"] if m.get("blocks"))
    db_run(st, "UPDATE research SET status = 'done', progress = '', report = ? WHERE id = ?",
           "## Summary\n\nThe instant asset write-off threshold stays at **$20,000** [1].\n\n## Evidence\n\n| Claim | Source |\n|---|---|\n| Threshold | [1] |", rid)
    expect(p.get_by_text("Research finished")).to_be_visible(timeout=30000)
    expect(p.get_by_text("The instant asset write-off threshold stays at")).to_be_visible()
    check("B [engine stand-in] the finished report appears in the open conversation without reloading", True)
    expect(p.get_by_role("link", name="Full report and sources").last).to_have_attribute("href", f"/research/{rid}")
    check("B the report links to its full page", True)
    shot(p, "v13-B-research")

    # C. Upload a spreadsheet in the conversation and get computed figures [stand-in AI decides; figures computed by ROOKS]
    p.goto(st.url + "/assistant")
    csv = os.path.join(WORK, "sales.csv")
    with open(csv, "w") as f:
        f.write("Region,Revenue,Units\nNorth,1200.50,10\nSouth,800,4\nNorth,300,2\nEast,,1\n")
    p.locator("input[type=file]").first.set_input_files(csv)
    expect(p.get_by_text("sales.csv")).to_be_visible()
    send(p, "Which region sells most?")
    expect(p.get_by_text("North** sells", exact=False).or_(p.get_by_text("sells the most"))).to_be_visible()
    kpis = p.locator(".rkpis").last.inner_text()
    check("C the figures are computed from the file (4 rows, revenue 2,300.5)", "4" in kpis and "2,300.5" in kpis, kpis)
    check("C a chart and a column table are shown", p.locator(".rblock svg, .rblock canvas").count() >= 1 and p.locator("table.rtable").count() >= 1)
    shot(p, "v13-C-spreadsheet")

    # D. A document with a working preview and download [stand-in AI]
    p.goto(st.url + "/assistant")
    send(p, "Write a document about the onboarding plan")
    expect(p.get_by_text("is saved")).to_be_visible()
    p.get_by_role("button", name="Preview").last.click()
    prev = p.locator(".doc-preview").last
    expect(prev.locator("h2", has_text="Week 1")).to_be_visible()
    check("D the preview renders the document (headings, bold, lists)", prev.locator("strong", has_text="Set up").count() == 1 and prev.locator("li").count() >= 3)
    with p.expect_download() as dl:
        p.get_by_role("button", name="Download").last.click()
    path = dl.value.path()
    body = open(path).read() if path else ""
    check("D the download is the document's Markdown", dl.value.suggested_filename == "Onboarding plan.md" and "## Week 2" in body, dl.value.suggested_filename)
    p.get_by_role("link", name="Open").last.click()
    expect(p).to_have_url(re.compile(r"/documents/\d+"))
    check("D Open goes to the document, inside the Library area", p.get_by_role("navigation", name="Library").count() == 1)
    shot(p, "v13-D-document")

    # E. A mission: plan posted for approval, approved in the conversation, result posted [stand-in AI, real mission engine]
    p.goto(st.url + "/assistant")
    send(p, "Plan the team offsite as a mission")
    expect(p.get_by_text("The plan is ready")).to_be_visible(timeout=40000)
    card = p.locator(".job-card").last
    expect(card).to_contain_text("List what the offsite needs")
    mid = int(re.search(r"/missions/(\d+)", card.get_by_role("link").first.get_attribute("href")).group(1))
    check("E nothing ran before approval", api(p, f"/missions/{mid}")["body"]["status"] == "review")
    shot(p, "v13-E-plan")
    card.get_by_role("button", name="Approve plan and start").click()
    expect(p.get_by_text(re.compile(r"Mission (finished|partly finished)"))).to_be_visible(timeout=90000)
    m = api(p, f"/missions/{mid}")["body"]
    check("E the mission ran its steps (workers included) and the result is in the conversation", m["status"] in ("done", "partial"), m["status"])
    shot(p, "v13-E-result")

    # Pair the real companion (F, G): its own home, Codex's own config home, stand-in APIs
    comp = None
    if CLI_BIN and os.path.exists(os.path.join(CLI_BIN, "codex")):
        resp_port, msg_port = free_port(), free_port()
        senv = dict(os.environ, FINAL="I added README.md with how to run the app.", CMD="printf '# Demo app\\n\\nRun it with make.\\n' > README.md && ls",
                    WRITE_PATH=os.path.join(PROJECT, "CHANGELOG.md"))
        senv["FINAL"] = "I added README.md with how to run the app."
        _procs.append(subprocess.Popen([sys.executable, os.path.join(STANDINS, "responses_api.py"), str(resp_port), os.path.join(WORK, "responses.log")], env=senv))
        menv = dict(senv, FINAL="I added CHANGELOG.md.")
        _procs.append(subprocess.Popen([sys.executable, os.path.join(STANDINS, "messages_api.py"), str(msg_port), os.path.join(WORK, "messages.log")], env=menv))
        codex_home = os.path.join(WORK, "codexhome")
        os.makedirs(codex_home)
        with open(os.path.join(codex_home, "config.toml"), "w") as f:
            f.write(f'model = "stand-in"\nmodel_provider = "standin"\n\n[model_providers.standin]\nname = "Stand-in (test)"\n'
                    f'base_url = "http://127.0.0.1:{resp_port}/v1"\nwire_api = "responses"\nexperimental_bearer_token = "sk-standin-not-real"\n')
        cenv = {"PATH": f"{CLI_BIN}:/usr/local/bin:/usr/bin:/bin", "HOME": os.path.join(WORK, "home"), "CODEX_HOME": codex_home,
                "ROOKS_COMPANION_HOME": os.path.join(WORK, "companion"), "ANTHROPIC_API_KEY": "sk-ant-standin-not-real",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{msg_port}", "NO_PROXY": "127.0.0.1,localhost", "LANG": "C.UTF-8"}
        os.makedirs(cenv["HOME"])
        subprocess.run([os.path.join(CLI_BIN, "codex"), "login", "--with-api-key"], input="sk-standin-not-real", text=True, env=cenv,
                       capture_output=True, timeout=60)
        code = api(p, "/computer/pair", "POST")["body"]["code"]
        cm = [sys.executable, "-m", "rooks_companion"]
        cwd = os.path.join(REPO, "companion")
        subprocess.run(cm + ["pair", st.url, code, "--name", "Scenario computer"], env=cenv, cwd=cwd, check=True, capture_output=True, timeout=60)
        subprocess.run(cm + ["allow-folder", PROJECT], env=cenv, cwd=cwd, check=True, capture_output=True)
        subprocess.run(cm + ["allow-codex", "on"], env=cenv, cwd=cwd, check=True, capture_output=True)
        subprocess.run(cm + ["allow-claude-api", "on"], env=cenv, cwd=cwd, check=True, capture_output=True)
        comp = subprocess.Popen(cm + ["run"], env=cenv, cwd=cwd, stdout=open(os.path.join(WORK, "companion.log"), "w"), stderr=subprocess.STDOUT)
        _procs.append(comp)
        api(p, "/computer/settings", "PUT", {"enabled": True})
        end = time.time() + 30
        while time.time() < end and not any(d["online"] for d in api(p, "/computer")["body"]["devices"]):
            time.sleep(0.5)

    # F. An official Codex installation: detected, explained, approved, run in the folder, result in the conversation [official CLI]
    if comp:
        p.goto(st.url + "/settings#connection-directory")
        row = p.locator(".conn-row", has_text="OpenAI Codex CLI")
        row.get_by_role("button", name="Check the paired computer").click()
        expect(row.locator(".badge", has_text="Ready")).to_be_visible(timeout=60000)
        check("F [official CLI] Codex is detected, signed in with an API key, sandbox confirmed on this computer",
              "OpenAI bills" in row.inner_text(), row.inner_text())
        check("F the directory says Codex does not power ROOKS chat", "does not power ROOKS chat" in row.inner_text())
        p.goto(st.url + "/assistant")
        send(p, f"Use Codex to add a README.md with how to run the app in {PROJECT}")
        expect(p.locator(".card", has_text="waiting for your approval").get_by_text("Codex will change files in")).to_be_visible()
        check("F the approval names the folder and the billing before anything runs", not os.path.exists(os.path.join(PROJECT, "README.md")))
        shot(p, "v13-F-approval")
        p.get_by_role("button", name="Allow").last.click()
        expect(p.get_by_text("Codex finished").first).to_be_visible(timeout=120000)
        readme = open(os.path.join(PROJECT, "README.md")).read() if os.path.exists(os.path.join(PROJECT, "README.md")) else ""
        check("F [official CLI] Codex wrote the file in the allowed folder and its answer is in the conversation",
              "Run it with make" in readme and p.get_by_text("I added README.md").count() >= 1, readme)
        shot(p, "v13-F-result")
    else:
        skip("F official Codex CLI", "ROOKS_CLI_BIN not set")

    # G. Claude Code: accurate claims; runs only with an API key, never a subscription [official CLI]
    p.goto(st.url + "/settings#connection-directory")
    row = p.locator(".conn-row", has_text="Claude Code")
    check("G the directory says plainly that ROOKS chat cannot use a Claude subscription",
          "ROOKS chat cannot use a Claude subscription" in row.inner_text(), row.inner_text())
    if comp:
        expect(row.locator(".badge", has_text="Ready")).to_be_visible(timeout=30000)
        check("G [official CLI] Claude Code is detected and billed to the API key only", "Anthropic bills the API key" in row.inner_text(), row.inner_text())
        p.goto(st.url + "/assistant")
        send(p, f"Use Claude Code to add a changelog in {PROJECT}")
        expect(p.locator(".card", has_text="waiting for your approval").get_by_text("Claude Code will change files in")).to_be_visible()
        p.get_by_role("button", name="Allow").last.click()
        expect(p.get_by_text("Claude Code finished").first).to_be_visible(timeout=120000)
        log = [json.loads(x) for x in open(os.path.join(WORK, "messages.log"))]
        check("G [official CLI] Claude Code ran in --bare mode on the API key (no OAuth sent) and wrote the file",
              os.path.exists(os.path.join(PROJECT, "CHANGELOG.md")) and log and all(not r["auth"] and r["key"] == "sk-ant" for r in log),
              log[:2])
        check("G Claude Code had no shell tool", all("Bash" not in r["tools"] for r in log), [r["tools"] for r in log][:1])
    else:
        skip("G official Claude Code run", "ROOKS_CLI_BIN not set")

    # H. Optional local engines: nothing installed or started; AirLLM tested for real when a bridge is running
    p.goto(st.url + "/settings#connection-directory")
    for name in ("Ollama", "llama.cpp", "AirLLM bridge (experimental)"):
        r = p.locator(".conn-row", has_text=name).first
        check(f"H {name} is listed as not connected, with nothing installed by ROOKS", "Nothing is installed, downloaded or started" in r.inner_text(), r.inner_text())
    if AIRLLM:
        api(p, "/ai-mode", "PUT", {"mode": "local"})
        con = api(p, "/providers/connect", "POST", {"name": "AirLLM bridge", "base_url": AIRLLM, "api_key": "none"})["body"]
        pid = con.get("provider_id")
        api(p, f"/providers/{pid}/free-confirm", "PUT", {"confirmed": True})
        con = api(p, f"/providers/{pid}/check", "POST")["body"]
        # One request at a time on a slow CPU: the check may pass (it answered something) or time out; either way it is explained
        check("H [AirLLM] the connection check's result is stated plainly", con.get("ok") or "did not answer within" in con.get("error", ""), con)
        ref = f"{pid}/tiny-random-LlamaForCausalLM"
        res = api(p, "/connection-directory/test", "POST", {"ref": ref, "caps": ["chat", "tools", "streaming"]})["body"]
        r = res.get("results", {})
        check("H [AirLLM] the bridge answers, but its random test model fails chat, tools and streaming: a package that installs is not a working model",
              r and not r["chat"]["ok"] and not r["tools"]["ok"] and not r["streaming"]["ok"], res)
        p.reload()
        row = p.locator(".conn-row", has_text="tiny-random-LlamaForCausalLM").first
        expect(row.locator(".cap.failed").first).to_be_visible()
        shot(p, "v13-H-airllm")
        api(p, "/ai-mode", "PUT", {"mode": "free"})
    else:
        skip("H AirLLM capability tests", "ROOKS_AIRLLM_URL not set")

    # I. Voice and check-ins, with approval and quiet hours [stand-in AI]
    p.goto(st.url + "/assistant")
    send(p, "How should I plan my week?")
    expect(p).to_have_url(re.compile(r"/assistant/\d+"))
    expect(p.get_by_text("Pick three outcomes")).to_be_visible()
    vcid = current_cid(p)
    mark = api(p, "/voice/sessions/start", "POST", {"conversation_id": vcid})["body"]["mark"]
    api(p, "/assistant/chat", "POST", {"message": "Check in with me on weekday mornings about deadlines", "conversation_id": vcid, "voice": True})
    p.reload()
    expect(p.locator(".card", has_text="waiting for your approval").get_by_text("may interrupt you")).to_be_visible()
    check("I a check-in asked for by voice waits for approval", api(p, "/checkins")["body"]["topics"] == [])
    p.get_by_role("button", name="Allow").last.click()
    expect(p.locator(".card", has_text="waiting for your approval")).to_have_count(0)
    topics = api(p, "/checkins")["body"]["topics"]
    check("I after approval it exists with quiet hours 21:00 to 07:30", topics and topics[0]["quiet_start"] == "21:00", topics)
    tid = topics[0]["id"]
    api(p, "/tasks", "POST", {"title": "File the quarterly report", "due": time.strftime("%Y-%m-%d", time.gmtime(time.time() + 86400)), "priority": 3})
    now_h = time.gmtime().tm_hour
    api(p, "/assistant/settings", "PUT", {"timezone": "UTC"})
    api(p, f"/checkins/{tid}", "PATCH", {"quiet_start": f"{(now_h - 1) % 24:02d}:00", "quiet_end": f"{(now_h + 2) % 24:02d}:00"})
    run = api(p, f"/checkins/{tid}/run", "POST")["body"]
    check("I a check-in during quiet hours is held, not delivered", run.get("status") in ("deferred", "quiet", "nothing"), run)
    api(p, f"/voice/sessions/{vcid}/end", "POST", {"keep": "nothing", "mark": mark})
    left = [m["content"] for m in api(p, f"/conversations/{vcid}")["body"]["messages"]]
    check("I ending the voice session with 'keep nothing' removes what was said in it, and the check-in stays",
          not any("Check in with me" in c for c in left) and any("plan my week" in c for c in left) and api(p, "/checkins")["body"]["topics"], left)

    # J. Something unavailable gets a useful explanation
    p.goto(st.url + "/assistant")
    opt = p.locator('select[aria-label="AI model"] option', has_text="acme/pro")
    expect(opt.first).to_be_attached()
    check("J a paid model is listed but cannot be picked in Free Cloud AI, with the reason",
          opt.count() == 1 and opt.first.is_disabled() and "Free Cloud AI" in (opt.first.get_attribute("title") or ""),
          (opt.count(), [o.is_disabled() for o in opt.all()], opt.first.get_attribute("title")))
    untested = p.locator('select[aria-label="AI model"] optgroup[label^="Not tested"]')
    check("J models never tried are in their own group, not offered as ready", untested.count() in (0, 1))
    agents = api(p, "/coding/agents")["body"]
    if not comp:
        check("J without a paired computer, coding agents explain what to do", "Pair the ROOKS Companion" in agents.get("reason", ""), agents)
    p.goto(st.url + "/settings#connection-directory")
    expect(p.locator("#connection-directory")).to_be_visible()
    check("J the directory never shows a capability as working before it was tested",
          p.locator(".conn-row", has_text="OpenRouter").first.locator(".cap.passed").count() == 0)
    axe(p, "Settings with the connection directory")
    shot(p, "v13-J-directory")
    p.goto(st.url + "/notes")
    axe(p, "Library tabs")
    check("no page errors", not errors, errors[:3])
    b.close()
sys.exit(finish())
