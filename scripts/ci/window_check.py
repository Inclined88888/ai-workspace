"""After the one-command install: start the app the way a user does and prove the window would show the UI, not a blank
page. Used by the CI "macOS window" job (Apple Silicon, Chrome installed, no model keys).

1. `ai-workspace --no-open` prints a one-time sign-in link. Following it with a cookie jar must land on the UI HTML
   (`<div id="root"`), its script must load, and the same link must not work twice.
2. Plain `ai-workspace` (the real window path) opens Chrome on a fresh one-time link; with headless window args Chrome
   renders it and saves a screenshot. The rendered DOM must contain the React app, not an error page.
3. `--status` and `--logs` must print, the Applications shortcut must carry the real icon, and no temporary .html
   files may be left in the data folder (the old cause of ERR_FILE_NOT_FOUND)."""

import http.cookiejar
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BIN = Path(os.environ.get("AI_WORKSPACE_BIN") or Path.home() / ".local" / "bin")
LAUNCHER = str(BIN / "ai-workspace")
STATE = Path.home() / ".ai-workspace" / "launcher.json"
OUT = Path(os.environ.get("WINDOW_CHECK_OUT", "window-check"))
RENDER = r"""
import re, sys, time
from playwright.sync_api import sync_playwright
mode, target, shot = sys.argv[1:4]
with sync_playwright() as p:
    if mode == "cdp":  # attach to the window the launcher opened
        for _ in range(60):
            try:
                browser = p.chromium.connect_over_cdp(target)
                break
            except Exception:
                time.sleep(1)
        else:
            sys.exit("could not attach to the app window")
        page = None
        for _ in range(60):
            pages = [pg for c in browser.contexts for pg in c.pages if pg.url.startswith("http")]
            if pages:
                page = pages[0]
                break
            time.sleep(1)
        assert page, "the app window has no http page"
    else:
        try:
            browser = p.chromium.launch(channel="chrome")
        except Exception:
            browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(target)
    page.wait_for_selector("#root > *", timeout=30000)
    page.wait_for_timeout(1500)
    page.screenshot(path=shot)
    text = page.inner_text("body")
    print("url:", page.url)
    print("text:", re.sub(r"\s+", " ", text)[:300])
    assert "ERR_FILE_NOT_FOUND" not in text and "couldn" not in text.lower(), text[:300]
    assert len(text.strip()) > 20, "the page is blank"
"""


def playwright_python() -> str:
    """The Python that rooks-browser was installed with (it has Playwright and its Chromium)."""
    roots = [Path.home() / ".local" / "share" / "uv" / "tools" / "rooks-browser",
             Path(os.environ.get("APPDATA", "")) / "uv" / "tools" / "rooks-browser"]
    for root in roots:
        for cand in (root / "bin" / "python", root / "Scripts" / "python.exe"):
            if cand.exists():
                return str(cand)
    sys.exit("rooks-browser's Python (with Playwright) was not found")


def render(mode: str, target: str, shot: Path) -> None:
    out = subprocess.run([playwright_python(), "-c", RENDER, mode, target, str(shot)], capture_output=True, text=True,
                         timeout=180)
    print(out.stdout + out.stderr[-2000:])
    assert out.returncode == 0, "rendering the UI failed"
    assert shot.exists() and shot.stat().st_size > 10000, "no screenshot"


def step(msg: str) -> None:
    print(f"\n== {msg}", flush=True)


def run(*args: str) -> str:
    out = subprocess.run([LAUNCHER, *args], capture_output=True, text=True, timeout=60)
    print(out.stdout + out.stderr)
    return out.stdout


def wait_state(proc: subprocess.Popen, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            sys.exit(f"the launcher exited early:\n{proc.stdout.read() if proc.stdout else ''}")
        try:
            st = json.loads(STATE.read_text())
            with urllib.request.urlopen(st["url"] + "/healthz", timeout=2):
                return st
        except Exception:
            time.sleep(1)
    sys.exit("the app did not answer /healthz in time")


def fresh_link(st: dict) -> str:
    req = urllib.request.Request(st["url"] + "/api/desktop/handoff", method="POST", data=b"",
                                 headers={"Authorization": f"Bearer {st['token']}", "X-Workbench": "1"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return f"{st['url']}/api/desktop/handoff?code={json.load(r)['code']}"


def stop(proc: subprocess.Popen) -> None:
    run("--stop")
    try:
        proc.wait(30)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.unlink(missing_ok=True)

    step("1. ai-workspace --no-open, then follow the one-time link with a cookie jar")
    proc = subprocess.Popen([LAUNCHER, "--no-open"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    st = wait_state(proc)
    link = ""
    deadline = time.monotonic() + 30
    while not link and time.monotonic() < deadline:
        line = proc.stdout.readline()
        print(line, end="")
        m = re.search(r"(http://127\.0\.0\.1:\d+/api/desktop/handoff\?code=\S+)", line)
        link = m.group(1) if m else ""
    assert link, "the launcher did not print a one-time sign-in link"
    assert st["token"] not in link, "the launch token leaked into the link"
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(link, timeout=10) as r:
        html = r.read().decode()
        print(f"landed on {r.url} ({r.status}), {len(html)} bytes")
    assert '<div id="root"' in html, f"the UI HTML did not load:\n{html[:500]}"
    assert any(c.name for c in jar), "no session cookie was set"
    for asset in re.findall(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"', html):
        with opener.open(st["url"] + asset, timeout=10) as r:
            assert r.status == 200 and len(r.read()) > 1000, asset
            print(f"asset ok: {asset}")
    with opener.open(st["url"] + "/api/status", timeout=10) as r:
        print("api/status:", r.read().decode()[:200])
    try:
        urllib.request.urlopen(link, timeout=10)
        sys.exit("the one-time link worked twice")
    except urllib.error.HTTPError as e:
        assert e.code == 403, e.code
        print("second use refused (403), as it should be")

    step("2. Render the UI in Chrome from a fresh one-time link")
    render("goto", fresh_link(st), (OUT / "ui.png").resolve())

    step("3. --status and --logs")
    status = run("--status")
    assert "web UI: ok" in status, "--status did not report the web UI"
    logs = run("--logs")
    assert "launcher.log" in logs or "workbench.log" in logs
    stop(proc)

    step("4. The real window path: plain `ai-workspace` opens the Chrome app window on a one-time link")
    shot = (OUT / "window.png").resolve()
    env = {**os.environ, "AI_WORKSPACE_WINDOW_ARGS": "--headless=new --remote-debugging-port=9333"}
    STATE.unlink(missing_ok=True)
    proc = subprocess.Popen([LAUNCHER], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    wait_state(proc)
    try:
        render("cdp", "http://127.0.0.1:9333", shot)
    finally:
        stop(proc)
        print(proc.stdout.read())
    run("--logs")

    step("5. No temporary pages, and the shortcut has the real icon")
    data = Path(re.search(r"data: (.+)", status).group(1).strip())
    leftovers = [p for p in data.rglob("*.html") if "window" not in p.parts]
    assert not leftovers, f"temporary pages left behind: {leftovers}"
    if sys.platform == "darwin":
        app = Path.home() / "Applications" / "AI Workspace.app"
        plist = (app / "Contents" / "Info.plist").read_text()
        assert "CFBundleIconFile" in plist, plist
        icns = app / "Contents" / "Resources" / "icon.icns"
        assert icns.exists() and icns.stat().st_size > 10000, "the shortcut has no icon"
        print(f"shortcut icon ok: {icns} ({icns.stat().st_size} bytes)")
    print("\nwindow check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
