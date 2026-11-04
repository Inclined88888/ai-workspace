"""Live UI check for the B1 run tree, the B2 graph walk and the chat's screen reader surface.

Starts a throwaway backend (temp DATA_DIR, no provider keys, no models.dev) serving the built frontend, seeds one team
lead run and a small knowledge graph, then drives Chromium with Playwright:
  - /team?tab=runs shows the run, its sub-tasks and the synthesis
  - the entity page shows Connections from /knowledge/graph/traverse, 2 hops, with paths; the rel filter narrows it
  - axe-core (WCAG 2.0/2.1/2.2 A and AA rules) on the chat, team runs and entity pages: violations are listed
  - the ARIA snapshot Chromium hands to screen readers is saved for each page (not a VoiceOver run)
Writes screenshots and JSON into docs/evidence/ui/. Exit 0 only if every assertion holds.

Needs: workbench/frontend/dist (npm run build), python playwright with chromium, AXE_JS=path/to/axe.min.js.
Run:   AXE_JS=... python scripts/audit/ui_screens.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "workbench" / "backend"
DIST = ROOT / "workbench" / "frontend" / "dist"
OUT = ROOT / "docs" / "evidence" / "ui" / os.environ.get("UI_BROWSER", "chromium")
H = {"X-Workbench": "1"}

SEED = r"""
from app import db
db.init() if hasattr(db, "init") else None
from app.knowledge import graph
from app.team import lead
graph.schema.ensure() if hasattr(graph, "schema") else None
a = graph.upsert("person", "Alice Example", {"role": "engineer"})
b = graph.upsert("project", "Project Alpha", {"status": "active"})
c = graph.upsert("organisation", "Acme Pty Ltd", {})
d = graph.upsert("person", "Bob Example", {})
graph.link(a["id"], "member_of", b["id"])
graph.link(b["id"], "part_of", c["id"])
graph.link(d["id"], "member_of", b["id"])
r = lead.dispatch("Compare three suppliers for office chairs", ["Price and warranty of supplier A", "Price and warranty of supplier B", "Delivery times for both"])
for i, s in enumerate(r["subtasks"]):
    st = "done" if i < 2 else "failed"
    db.run("UPDATE jobs SET status = ?, result = ? WHERE id = ?", st, f"Finding {i + 1}: sample result text." if st == "done" else "", s["job_id"])
db.run("UPDATE team_lead_runs SET status = 'done', synthesis = ?, finished_at = ? WHERE id = ?",
       "Supplier A is cheaper; B has the longer warranty. Delivery times could not be checked.", db.now(), r["id"])
print(json.dumps({"alice": a["id"], "run": r["id"]}))
"""


def port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def main() -> int:
    from playwright.sync_api import sync_playwright

    axe_js = Path(os.environ["AXE_JS"]).read_text()
    if not (DIST / "index.html").exists():
        sys.exit("build the frontend first: cd workbench/frontend && npm run build")
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
    env.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR=str(tmp / "data"), MODEL_CATALOG_REFRESH="0",
               ROOKS_COMPANION_FILE=str(tmp / "companion.json"), STATIC_DIR=str(DIST), PYTHONPATH=str(BACKEND))
    seed = subprocess.run([sys.executable, "-c", "import json\n" + SEED], cwd=BACKEND, env=env, capture_output=True, text=True)
    if seed.returncode:
        sys.exit("seed failed:\n" + seed.stderr[-3000:])
    ids = json.loads(seed.stdout.strip().splitlines()[-1])
    p = port()
    url = f"http://127.0.0.1:{p}"
    log = open(tmp / "backend.log", "w")  # noqa: SIM115
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(p), "--log-level", "warning"],
                            cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT)
    failures: list = []
    results: dict
    results = {"commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()}

    def check(ok: bool, what: str):
        results.setdefault("checks", []).append({"ok": bool(ok), "check": what})
        if not ok:
            failures.append(what)

    try:
        for _ in range(200):
            try:
                urllib.request.urlopen(url + "/healthz", timeout=1).close()
                break
            except OSError:
                time.sleep(0.1)
        with sync_playwright() as pw:
            engine = os.environ.get("UI_BROWSER", "chromium")  # webkit = the engine of the macOS app window (WKWebView)
            br = getattr(pw, engine).launch()
            ctx = br.new_context(viewport={"width": 1280, "height": 900}, reduced_motion="reduce")  # no mid-fade contrast
            page = ctx.new_page()
            page.goto(url)
            r = page.request.post(url + "/api/setup", data={"password": "testpassword1"}, headers=H)
            check(r.ok, "setup and sign in")
            page.request.post(url + "/api/desktop/welcome-done", headers=H)

            def audit(name: str):
                page.evaluate(axe_js)
                res = page.evaluate("""async () => { const r = await axe.run(document, { runOnly: { type: 'tag', values:
                    ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa'] } });
                    return r.violations.map(v => ({ id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
                    targets: v.nodes.slice(0, 5).map(n => n.target.join(' ')) })); }""")
                tree = page.locator("body").aria_snapshot()  # the ARIA tree Chromium exposes to screen readers
                (OUT / f"{name}.axe.json").write_text(json.dumps(res, indent=1))
                (OUT / f"{name}.aria.yaml").write_text(tree)
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=True)
                results[name] = {"axe_violations": res}
                return res, tree

            # B1: run tree
            page.goto(url + "/team?tab=runs")
            page.wait_for_selector("text=Compare three suppliers", timeout=15000)
            body = page.inner_text("main") if page.query_selector("main") else page.inner_text("body")
            check("Price and warranty of supplier A" in body and "Delivery times for both" in body, "B1: every sub-task listed")
            check("Supplier A is cheaper" in body, "B1: synthesis shown")
            check(page.locator(".lead-sub[data-status=failed]").count() == 1, "B1: failed sub-task marked")
            b1, _ = audit("b1-team-runs")

            # B2: graph walk from Alice, 2 hops: Project Alpha (1), Acme and Bob (2)
            page.goto(url + f"/knowledge?tab=people&entity={ids['alice']}")
            page.wait_for_selector("text=Connections", timeout=15000)
            page.wait_for_selector("text=2 steps away", timeout=15000)
            walk = page.inner_text("#graph-walk-h >> xpath=..")
            check("Project Alpha" in walk and "Acme Pty Ltd" in walk and "Bob Example" in walk, "B2: 2-hop walk shows all three")
            check("member of" in walk and "part of" in walk, "B2: paths show the links")
            page.fill("input[placeholder='e.g. works_at']", "member_of")
            page.wait_for_function("() => !document.querySelector('#graph-walk-h').parentElement.innerText.includes('Acme Pty Ltd')", timeout=10000)
            walk = page.inner_text("#graph-walk-h >> xpath=..")
            check("Bob Example" in walk and "Acme Pty Ltd" not in walk, "B2: rel filter narrows the walk")
            page.fill("input[placeholder='e.g. works_at']", "")
            page.wait_for_selector("text=Acme Pty Ltd", timeout=10000)
            b2, _ = audit("b2-graph-walk")

            # #7: the app's own terminal runs a real shell on this machine
            page.goto(url + "/terminal")
            page.wait_for_selector(".xterm-rows", timeout=15000)
            page.wait_for_function("() => !document.querySelector('[role=status]')?.innerText.includes('Connecting')", timeout=15000)
            page.click(".terminal-box")
            page.keyboard.type("echo app-term-$((6*7))\n")
            page.wait_for_function("() => document.querySelector('.xterm-rows').innerText.includes('app-term-42')", timeout=15000)
            check(True, "#7: typed command ran in the app terminal (app-term-42)")
            term, _ = audit("app-terminal")
            page.goto(url + "/")  # leaving the page ends the shell
            page.wait_for_timeout(2500)
            st = page.request.get(url + "/api/terminal/status", headers=H).json()
            check(st["open"] == 0, f"#7: leaving the page ended the shell (open={st['open']})")

            # chat: the screen reader surface (landmark, heading, live regions)
            page.goto(url + "/assistant")
            page.wait_for_timeout(1500)
            chat, tree = audit("chat")
            import re

            check(bool(re.search(r"^\s*- main\b", tree, re.M)), "chat: a main landmark is exposed")
            check(page.locator("[aria-live]").count() >= 1, "chat: at least one live region")
            for name, v in (("b1-team-runs", b1), ("b2-graph-walk", b2), ("app-terminal", term), ("chat", chat)):
                serious = [x for x in v if x["impact"] in ("serious", "critical")]
                check(not serious, f"{name}: no serious or critical axe violations ({[x['id'] for x in serious]})")
            br.close()
    finally:
        proc.terminate()
        proc.wait(10)
        log.close()
    results["failures"] = failures
    (OUT / "results.json").write_text(json.dumps(results, indent=1))
    for c in results.get("checks", []):
        print(("PASS " if c["ok"] else "FAIL ") + c["check"])
    for k in ("b1-team-runs", "b2-graph-walk", "app-terminal", "chat"):
        if k in results:
            print(k, "axe:", [(v["id"], v["impact"], v["nodes"]) for v in results[k]["axe_violations"]])
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
