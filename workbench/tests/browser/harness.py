"""Shared setup for the browser tests: a fresh ROOKS server on temporary data, the stand-in AI, a Chromium browser,
and small helpers for checks, screenshots and accessibility.

Environment (all optional):
  ROOKS_PY        Python with the backend requirements (default: the Python running the test)
  ROOKS_CHROMIUM  Chromium executable for Playwright (default: Playwright's own)
  AXE_JS          Path to axe-core's axe.min.js. Without it the accessibility checks are reported as SKIPPED.
  ROOKS_SHOTS     Folder for screenshots (default: a temporary folder, printed at the end)

The frontend must be built first (cd workbench/frontend && npm run build); the server serves frontend/dist.
"""

import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
PY = os.environ.get("ROOKS_PY", sys.executable)
CHROMIUM = os.environ.get("ROOKS_CHROMIUM") or None
AXE_JS = os.environ.get("AXE_JS", "")
PASSWORD = "correct horse battery"
_procs: list[subprocess.Popen] = []
_results: list[tuple[str, bool, str]] = []


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait(url: str, seconds: float = 30) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(0.2)
    raise RuntimeError(f"{url} did not start")


def _stop() -> None:
    for p in _procs:
        p.terminate()
    for p in _procs:
        try:
            p.wait(5)
        except subprocess.TimeoutExpired:
            p.kill()


atexit.register(_stop)


class Stack:
    """The stand-in AI plus one ROOKS server with its own data folder."""

    def __init__(self, name: str = "rooks", ollama: bool = False, env: dict | None = None):
        self.dir = tempfile.mkdtemp(prefix=f"rooks-browser-{name}-")
        self.ai_port, self.port = free_port(), free_port()
        self.ai = f"http://127.0.0.1:{self.ai_port}"
        self.log = os.path.join(self.dir, "ai-requests.jsonl")
        _procs.append(subprocess.Popen([sys.executable, os.path.join(HERE, "fake_ai.py"), str(self.ai_port), self.log]))
        _wait(self.ai + "/v1/models")
        data = os.path.join(self.dir, "data")
        os.makedirs(data)
        env_extra = env
        env = {k: v for k, v in os.environ.items() if k not in ("GITHUB_TOKEN", "BRAVE_API_KEY", "TAVILY_API_KEY", "OLLAMA_URL")}
        env.update(APP_SECRET="s" * 40, TOOLS_API_KEY="t" * 30, DATA_DIR=data, NO_PROXY="127.0.0.1,localhost",
                   STATIC_DIR=os.path.join(REPO, "workbench", "frontend", "dist"), OPENROUTER_URL=f"{self.ai}/openrouter/api")
        env.update({k: v.replace("{ai}", self.ai) for k, v in (env_extra or {}).items()})
        self.data = data
        _procs.append(subprocess.Popen([PY, "-m", "uvicorn", "app.main:app", "--port", str(self.port)], env=env,
                                       cwd=os.path.join(REPO, "workbench", "backend"),
                                       stdout=open(os.path.join(self.dir, "server.log"), "w"), stderr=subprocess.STDOUT))
        self.url = f"http://127.0.0.1:{self.port}"
        _wait(self.url + "/healthz")

    def ai_requests(self) -> list[dict]:
        try:
            return [json.loads(line) for line in open(self.log)]
        except FileNotFoundError:
            return []


def api(page, path: str, method: str = "GET", body=None):
    """Call the ROOKS API from inside the signed-in page, so it uses the page's session."""
    return page.evaluate("""async ([u, m, b]) => { const r = await fetch(u, { method: m, headers: { 'X-Workbench': '1',
        'Content-Type': 'application/json' }, body: b === null ? undefined : JSON.stringify(b) });
        const t = await r.text(); try { return { status: r.status, body: JSON.parse(t) } } catch { return { status: r.status, body: t } } }""",
                         [f"/api{path}", method, body])


def check(name: str, ok, detail="") -> bool:
    ok = bool(ok)
    _results.append((name, ok, str(detail)[:400]))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({str(detail)[:300]})" if detail and not ok else ""), flush=True)
    return ok


def axe(page, label: str) -> None:
    """Serious and critical WCAG 2.2 A and AA violations on the current page, as one check."""
    if not AXE_JS or not os.path.exists(AXE_JS):
        print(f"SKIP  accessibility: {label} (set AXE_JS to axe.min.js)")
        return
    page.add_script_tag(path=AXE_JS)
    found = page.evaluate("""async () => (await axe.run(document, { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'] } }))
        .violations.filter(v => ['serious', 'critical'].includes(v.impact)).map(v => v.id + ' x' + v.nodes.length + ': ' + v.nodes.slice(0, 2).map(n => n.target.join(' ')).join(' | '))""")
    check(f"accessibility: {label}", not found, found)


def no_overflow(page) -> bool:
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


SHOTS = os.environ.get("ROOKS_SHOTS") or tempfile.mkdtemp(prefix="rooks-shots-")
os.makedirs(SHOTS, exist_ok=True)


def shot(page, name: str, full: bool = True) -> None:
    page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=full)


def browser(pw):
    return pw.chromium.launch(executable_path=CHROMIUM) if CHROMIUM else pw.chromium.launch()


def finish() -> int:
    passed = sum(1 for _, ok, _ in _results if ok)
    print(f"\n{passed}/{len(_results)} passed. Screenshots: {SHOTS}")
    _stop()
    return 0 if passed == len(_results) else 1
