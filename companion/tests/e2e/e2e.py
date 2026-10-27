"""Computer Use end to end with real processes: ROOKS server, ROOKS Companion (X11 adapter on Xvfb, Playwright Chromium
on the same display), a real X11 fixture app, a local website, and a scripted model. Every check reads the real
outcome (window title from the X server, files on disk, the website's submission log, the saved document)."""

import json
import os
import re
import signal
import subprocess
import sys
import time

import httpx

# Run from anywhere: python3 companion/tests/e2e/e2e.py
#   ROOKS_PY_APP        Python with ROOKS's backend requirements (default: python3)
#   ROOKS_PY_COMPANION  Python with python-xlib and playwright installed (default: python3)
#   ROOKS_CHROMIUM      Chromium executable for Playwright (default: Playwright's own)
# Needs Xvfb. Uses ports 8895-8897 and display :99. Writes everything to a temporary folder.
import tempfile

S = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(S, "..", "..", ".."))
PY_APP = os.environ.get("ROOKS_PY_APP", "python3")
PY_COMP = os.environ.get("ROOKS_PY_COMPANION", "python3")
CHROMIUM = os.environ.get("ROOKS_CHROMIUM", "")
BASE = "http://127.0.0.1:8895"
DISPLAY = ":99"
RUN = tempfile.mkdtemp(prefix="rooks-e2e-")
FILES, HOME, DATA = f"{RUN}/files", f"{RUN}/companion-home", f"{RUN}/data"
for d in (FILES, HOME, DATA):
    os.makedirs(d, exist_ok=True)
env_app = {k: v for k, v in os.environ.items() if k != "GITHUB_TOKEN"}
env_app.update(APP_SECRET="s" * 40, TOOLS_API_KEY="t" * 30, DATA_DIR=DATA, NO_PROXY="127.0.0.1,localhost")
results, procs = [], {}


def check(name, ok, detail=""):
    results.append(("PASS" if ok else "FAIL", name, str(detail)[:300]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {str(detail)[:200]}", flush=True)


def start(name, cmd, env=None, cwd=None):
    log = open(f"{RUN}/{name}.log", "a")
    procs[name] = subprocess.Popen(cmd, env=env or os.environ, cwd=cwd, stdout=log, stderr=subprocess.STDOUT)


def stop(name, sig=signal.SIGTERM):
    p = procs.pop(name, None)
    if p:
        p.send_signal(sig)
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait(5)


def wait_http(url, t=30):
    end = time.time() + t
    while time.time() < end:
        try:
            httpx.get(url, timeout=2)
            return True
        except httpx.HTTPError:
            time.sleep(0.3)
    return False


def start_app():
    start("rooks", [PY_APP, "-m", "uvicorn", "app.main:app", "--port", "8895"], env_app, f"{REPO}/workbench/backend")
    assert wait_http(BASE + "/healthz"), "ROOKS did not start"


def fixture(save):
    stop("fixture")
    start("fixture", [PY_COMP, f"{REPO}/companion/tests/fixture_app.py", save], {**os.environ, "DISPLAY": DISPLAY})
    time.sleep(1.0)


def title():
    out = subprocess.run([PY_COMP, "-c", "import sys; sys.path.insert(0, '.');"
                          "from rooks_companion.adapters.x11 import X11Adapter as A; print([w['title'] for w in A().windows() if w['app']=='ROOKS Fixture'][0])"],
                         env={**os.environ, "DISPLAY": DISPLAY}, cwd=f"{REPO}/companion", capture_output=True, text=True)
    return out.stdout.strip()


c = httpx.Client(base_url=BASE, headers={"X-Workbench": "1"}, timeout=120)


def chat(msg, cid=None):
    r = c.post("/api/assistant/chat", json={"message": msg, "conversation_id": cid})
    ev = [json.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")]
    return ev, next((e["conversation_id"] for e in ev if e["type"] == "start"), None)


def tool_results(ev):
    return [(e["name"], e["ok"], e["preview"]) for e in ev if e["type"] == "tool_result"]


try:
    if subprocess.run(["pgrep", "-f", f"Xvfb {DISPLAY}"], capture_output=True).returncode != 0:
        start("xvfb", ["Xvfb", DISPLAY, "-screen", "0", "1280x800x24"])
        time.sleep(1)
    start("model", [PY_APP, f"{S}/fake_model.py"], {**os.environ, "CU_FILES": FILES, "CU_CAPTURE_OUT": f"{RUN}/capture.png"})
    start("site", [sys.executable, f"{S}/site.py", f"{RUN}/submissions.log"])
    wait_http("http://127.0.0.1:8896/v1/models")
    wait_http("http://127.0.0.1:8897/")
    start_app()
    c.post("/api/setup", json={"password": "correct horse 1"})
    pr = c.post("/api/providers/connect", json={"name": "Fake", "base_url": "http://127.0.0.1:8896/v1", "api_key": "x"}).json()
    check("model connected", pr.get("ok"), pr.get("error"))
    c.put("/api/computer/settings", json={"enabled": True, "mode": "allowed"})

    # Pair the companion exactly as a user would, then start it
    code = c.post("/api/computer/pair").json()["code"]
    cenv = {**os.environ, "DISPLAY": DISPLAY, "ROOKS_COMPANION_HOME": HOME, "NO_PROXY": "127.0.0.1,localhost"}
    comp = [PY_COMP, "-m", "rooks_companion"]
    r = subprocess.run(comp + ["pair", BASE, code, "--name", "Test desktop"], env=cenv, cwd=f"{REPO}/companion", capture_output=True, text=True)
    check("companion paired", r.returncode == 0, r.stdout + r.stderr)
    cfg = json.load(open(f"{HOME}/config.json"))
    check("token stored privately", oct(os.stat(f"{HOME}/config.json").st_mode)[-3:] == "600" and oct(os.stat(HOME).st_mode)[-3:] == "700")
    subprocess.run(comp + ["allow-folder", FILES], env=cenv, cwd=f"{REPO}/companion", check=True, capture_output=True)
    cfg = json.load(open(f"{HOME}/config.json"))
    if CHROMIUM:
        cfg["browser_executable"] = CHROMIUM
    json.dump(cfg, open(f"{HOME}/config.json", "w"))

    def start_companion():
        start("companion", comp + ["run"], cenv, f"{REPO}/companion")
        for _ in range(60):
            devs = c.get("/api/computer").json()["devices"]
            if devs and devs[0]["online"] and {"desktop", "browser", "files"} <= set(devs[0]["capabilities"]):
                return devs[0]
            time.sleep(0.5)
        return None

    fixture(f"{RUN}/doc-a.txt")
    dev = start_companion()
    check("companion online with desktop, browser and files", bool(dev) and set(dev["capabilities"]) >= {"desktop", "browser", "files"}, dev)

    # A: desktop app interaction
    before = title()
    ev, _ = chat("TEST-A: click Add one in the fixture app")
    tr = tool_results(ev)
    after = title()
    check("A: observed the app, clicked by element id, observed again", [t[0] for t in tr] == ["computer_observe", "computer_click", "computer_observe"] and all(t[1] for t in tr), tr)
    check("A: the real window changed by exactly one click", "count=0" in before and "count=1" in after, f"{before} -> {after}")

    # V: visual targeting for a control accessibility cannot see (stand-in vision model that reads the real pixels)
    c.put("/api/computer/vision", json={"model": f"{pr['provider_id']}/fake-model"})
    chk = c.post("/api/computer/vision/check").json()["check"]
    vs = c.put("/api/computer/vision", json={"mode": "session", "session_minutes": 30})
    check("V: vision model passed the image test and was enabled (local model, no cloud consent needed)", chk["ok"] and vs.status_code == 200, vs.text[:200])
    fixture(f"{RUN}/doc-v.txt")
    ev, _ = chat("TEST-V1: click the red button")
    tr = tool_results(ev)
    check("V: observe, locate visually, click by visual target, observe again", [t[0] for t in tr] == ["computer_observe", "computer_locate", "computer_click_visual", "computer_observe"]
          and all(t[1] for t in tr), [t[:2] for t in tr])
    check("V: the hidden button was really clicked once", "hidden=1" in title(), title())
    sys.path.insert(0, f"{REPO}/companion")
    from rooks_companion import imaging
    w_, h_, ch_, px_ = imaging.decode(open(f"{RUN}/capture.png", "rb").read())
    def at(x, y):
        o = (y * w_ + x) * ch_
        return tuple(px_[o:o + 3])
    check("V: password field blacked out before the screenshot left the computer; the rest intact", at(120, 215) == (0, 0, 0) and at(340, 210)[0] > 200,
          f"password area {at(120, 215)}, red button {at(340, 210)}")
    ev, vcid = chat("TEST-V2: locate it")
    # the preview is cut at 300 characters, so read the target id out of it rather than parsing it as JSON
    tid = re.search(r'"target": "(v[^"]+)"', next(t[2] for t in tool_results(ev) if t[0] == "computer_locate")).group(1)
    ev, _ = chat(f"TEST-V3: click {tid}")  # a new conversation may not use this conversation's target
    tr = tool_results(ev)
    check("V: a visual target cannot be used from another conversation", tr and not tr[0][1] and "another conversation" in tr[0][2] and "hidden=1" in title(), tr)
    subprocess.run([PY_COMP, "-c", "from Xlib import display; d=display.Display(); "
                    "[w.configure(x=140) for w in d.screen().root.query_tree().children if (w.get_wm_class() or ('',''))[1]=='ROOKS Fixture']; d.sync()"],
                   env={**os.environ, "DISPLAY": DISPLAY}, check=True)
    time.sleep(0.5)
    ev, _ = chat(f"TEST-V3: click {tid}", vcid)
    tr = tool_results(ev)
    check("V: a visual click is refused when the window moved after the screenshot", tr and not tr[0][1] and "moved" in tr[0][2] and "hidden=1" in title(), tr)
    ev, _ = chat("TEST-V4: find something that is not there")
    msg = next((e["content"] for e in ev if e["type"] == "message"), "")
    check("V: when the target is not visible it says so instead of guessing", "could not clearly find" in msg, msg[:160])
    hist = json.dumps(c.get("/api/computer/history").json()) + json.dumps(c.get("/api/audit").json()) + json.dumps(c.get("/api/tool-runs").json())
    check("V: screen sharing is recorded, the image is not", "screen shared" in hist and "base64" not in hist)
    c.put("/api/computer/vision", json={"mode": "off"})

    # B: local files
    for n in ("invoice-1.txt", "invoice-2.txt", "receipt-1.txt", "notes.md"):
        open(f"{FILES}/{n}", "w").write(n)
    ev, _ = chat("TEST-B: organize the files folder")
    tr = tool_results(ev)
    got = {d: sorted(os.listdir(f"{FILES}/{d}")) for d in ("Invoices", "Receipts") if os.path.isdir(f"{FILES}/{d}")}
    check("B: folder organized as asked", got == {"Invoices": ["invoice-1.txt", "invoice-2.txt"], "Receipts": ["receipt-1.txt"]} and os.path.exists(f"{FILES}/notes.md"), got)
    check("B: every file step succeeded", all(t[1] for t in tr), [t[:2] for t in tr])

    # C: browser form with an approval for Submit
    ev, cid = chat("TEST-C: fill in the contact form on the test site")
    tr = tool_results(ev)
    pw = next((t for t in tr if t[0] == "browser_fill" and "Password" in t[2] or (t[0] == "browser_fill" and not t[1])), None)
    check("C: password field refused by the companion", pw is not None and not pw[1] and "password" in pw[2].lower(), tr)
    ap = next((e for e in ev if e["type"] == "approval"), None)
    check("C: Submit waits for approval and nothing was sent yet", ap is not None and ap["name"] == "browser_click" and not os.path.exists(f"{RUN}/submissions.log"), ap)
    if ap:
        r = c.post(f"/api/approvals/{ap['id']}", json={"approve": True}).json()
        check("C: approved submit ran", r["status"] == "approved", r.get("result"))
    subs = [json.loads(l) for l in open(f"{RUN}/submissions.log")] if os.path.exists(f"{RUN}/submissions.log") else []
    check("C: the site received exactly one submission with the typed values and no password", subs == [{"name": "Ada Lovelace", "email": "ada@example.com", "pw": ""}] or subs == [{"name": "Ada Lovelace", "email": "ada@example.com"}], subs)
    ev, _ = chat("TEST-C2: check the page", cid)
    msg = next((e["content"] for e in ev if e["type"] == "message"), "")
    check("C: verified the thank-you page in the browser", "Thanks, Ada Lovelace" in msg, msg)

    # D: document edit and save
    fixture(f"{RUN}/doc-d.txt")
    ev, _ = chat("TEST-D: write the quarterly total and save")
    saved = open(f"{RUN}/doc-d.txt").read() if os.path.exists(f"{RUN}/doc-d.txt") else None
    check("D: document saved with exactly the requested text", saved == "Quarterly total: 1250", repr(saved))
    check("D: window shows it saved", "saved=1" in title(), title())

    # E: Stop during a long typing action
    fixture(f"{RUN}/doc-e.txt")
    import threading
    out = {}
    th = threading.Thread(target=lambda: out.update(ev=chat("TEST-E: type a long paragraph")[0]))
    th.start()
    for _ in range(100):
        cur = c.get("/api/computer").json()["current"]
        if cur and cur["kind"] == "type_text" and cur["status"] == "dispatched":
            break
        time.sleep(0.1)
    time.sleep(1.5)
    t_stop = time.time()
    c.post("/api/computer/stop")
    th.join(120)
    time.sleep(1.0)
    typed = re.search(r"text=(.*)$", title(), re.S)
    n_typed = len(typed.group(1)) if typed else -1
    hist = c.get("/api/computer/history").json()
    tr = tool_results(out.get("ev", []))
    check("E: typing stopped part way", 0 < n_typed < 3150, n_typed)
    check("E: the save after Stop was refused and nothing new was sent", not any(h["kind"] == "key" and h["status"] == "done" for h in hist if h["created_at"] >= time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t_stop - 1)))
          and not os.path.exists(f"{RUN}/doc-e.txt"), [t[:2] for t in tr])
    time.sleep(1.0)
    n_later = len(re.search(r"text=(.*)$", title(), re.S).group(1))
    check("E: no more characters arrived after Stop took effect", n_later == n_typed, f"{n_typed} -> {n_later}")
    c.post("/api/computer/allow")

    # F: permission denial at the planner and on the computer
    c.put("/api/computer/settings", json={"app_rules": {"ROOKS Fixture": "blocked"}})
    n_before = len(c.get("/api/computer/history").json())
    ev, _ = chat("TEST-F1: look at the fixture")
    tr = tool_results(ev)
    check("F: ROOKS refuses a blocked app before anything reaches the computer", tr and not tr[0][1] and "blocked" in tr[0][2] and len(c.get("/api/computer/history").json()) == n_before, tr)
    c.put("/api/computer/settings", json={"app_rules": {}})
    fixture(f"{RUN}/doc-f.txt")
    ev, _ = chat("TEST-F2: click it")
    tr = tool_results(ev)
    check("F: the companion refuses a click whose label does not match the real element", len(tr) > 1 and not tr[1][1] and "not 'Add one'" in tr[1][2] and "deleted=False" in title(), tr)
    ev, _ = chat("TEST-F3: delete everything")
    ap = next((e for e in ev if e["type"] == "approval"), None)
    if ap:
        c.post(f"/api/approvals/{ap['id']}", json={"approve": False})
    check("F: Delete all needs approval; denied means nothing happens", ap is not None and "deleted=False" in title(), title())

    # G: ROOKS dies between the action and its recorded result
    fixture(f"{RUN}/doc-g.txt")
    out = {}
    def g1():
        try:
            out.update(r=chat("TEST-G1: type the line"))
        except httpx.HTTPError as e:  # expected: ROOKS is killed while this request is open
            out.update(err=str(e))
    th = threading.Thread(target=g1)
    th.start()
    for _ in range(100):
        cur = c.get("/api/computer").json()["current"]
        if cur and cur["kind"] == "type_text" and cur["status"] == "dispatched":
            break
        time.sleep(0.1)
    time.sleep(0.5)
    stop("rooks", signal.SIGKILL)  # hard crash, no clean shutdown
    th.join(30)
    time.sleep(6)  # the companion finishes typing while ROOKS is down
    start_app()
    c.post("/api/login", json={"password": "correct horse 1"})
    for _ in range(60):
        h = c.get("/api/computer/history").json()
        g = next((x for x in h if x["kind"] == "type_text" and x["status"] in ("done", "uncertain")), None)
        if g and g["status"] == "done":
            break
        time.sleep(0.5)
    check("G: after the restart the companion's late result was recorded", g is not None and g["status"] == "done", g)
    ev, _ = chat("TEST-G2: type the line again")
    tr = tool_results(ev)
    msg = next((e["content"] for e in ev if e["type"] == "message"), "")
    text_now = re.search(r"text=(.*)$", title(), re.S).group(1)
    check("G: the repeat was refused until the screen was looked at", tr and not tr[0][1] and "Observe the screen first" in tr[0][2], tr[:1])
    check("G: the line exists exactly once", text_now.count("Recovered line") == 1 and "Already there" in msg, f"{text_now.count('Recovered line')} | {msg}")

    # Mission using computer tools, with plan approval
    os.makedirs(f"{FILES}/inbox", exist_ok=True)
    open(f"{FILES}/inbox/contract.txt", "w").write("contract")
    m = c.post("/api/missions", json={"goal": "File the contract from the inbox"}).json()
    for _ in range(60):
        m = c.get(f"/api/missions/{m['id']}").json()
        if m["status"] == "review":
            break
        time.sleep(0.3)
    c.post(f"/api/missions/{m['id']}/start")
    for _ in range(120):
        m = c.get(f"/api/missions/{m['id']}").json()
        if m["status"] in ("done", "partial", "failed"):
            break
        time.sleep(0.5)
    check("Mission: planned, approved, run with computer file tools, verified done", m["status"] == "done" and os.path.exists(f"{FILES}/Contracts/contract.txt")
          and not os.path.exists(f"{FILES}/inbox/contract.txt"), (m["status"], [s["status"] for s in m["plan"]], m["error"]))

    # Business template: spreadsheet cleanup on a real file, through the real companion
    original = "Name,Email\nAda,ada@example.com\nBob,bob@example.com\nAda L,ada@example.com\nCy,cy@example.com\n"
    open(f"{FILES}/customers.csv", "w").write(original)
    m = c.post("/api/templates/spreadsheet-cleanup/start", json={"values": {"path": f"{FILES}/customers.csv", "columns": "Email"}}).json()
    check("T: template made a plan for review and ran nothing", m["status"] == "review" and len(m["plan"]) == 3, m["status"])
    c.post(f"/api/missions/{m['id']}/start")
    for _ in range(120):
        m = c.get(f"/api/missions/{m['id']}").json()
        if m["status"] in ("done", "partial", "failed", "review_step"):
            break
        time.sleep(0.5)
    clean_rows = open(f"{FILES}/customers-clean.csv").read().strip().splitlines() if os.path.exists(f"{FILES}/customers-clean.csv") else []
    check("T: spreadsheet cleanup mission done", m["status"] == "done", (m["status"], [s["status"] for s in m["plan"]], m["error"]))
    check("T: the cleaned copy has one row per email; the original is untouched",
          len(clean_rows) == 4 and sum("ada@example.com" in r for r in clean_rows) == 1 and open(f"{FILES}/customers.csv").read() == original, clean_rows)

    # Audit and screenshots
    audit = c.get("/api/audit").json()
    check("activity log has the desktop actions without typed text", any(a["action"] == "computer" for a in audit)
          and not any("Quarterly total" in a["detail"] for a in audit), [a["detail"] for a in audit if a["action"] == "computer"][:3])
    shot = c.get(f"/api/computer/screenshot/{dev['id']}").json()["image"]
    check("live preview screenshot available to the user", shot.startswith("data:image/png;base64,"), len(shot))
finally:
    for n in list(procs):
        stop(n)
    print(f"\n{sum(r[0] == 'PASS' for r in results)} passed, {sum(r[0] == 'FAIL' for r in results)} failed  (logs in {RUN})")
    json.dump(results, open(f"{RUN}/results.json", "w"), indent=1)
