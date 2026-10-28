"""Server resource use and API latency, for comparing two checkouts the same way. Prints JSON.

  rss_start_mb     resident memory of the ROOKS server once it answers
  rss_idle_mb      after 60 seconds idle with one provider connected
  cpu_idle_s       CPU seconds the server used during those 60 idle seconds (background loops)
  api_ms           median of 20 requests per endpoint, on localhost

Usage: python3 resources.py <repo checkout> [label]
The stand-in AI (fake_ai.py from this folder) is the only provider. Linux only (reads /proc).
"""

import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from harness import free_port  # noqa: E402

repo = os.path.abspath(sys.argv[1])
label = sys.argv[2] if len(sys.argv) > 2 else repo
py = os.environ.get("ROOKS_PY", sys.executable)
tmp = tempfile.mkdtemp(prefix="rooks-res-")
ai_port, port = free_port(), free_port()
ai = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_ai.py"), str(ai_port), os.path.join(tmp, "ai.log")])
env = dict(os.environ, APP_SECRET="s" * 40, TOOLS_API_KEY="t" * 30, DATA_DIR=os.path.join(tmp, "data"), NO_PROXY="127.0.0.1,localhost",
           STATIC_DIR=os.path.join(repo, "workbench", "frontend", "dist"))
srv = subprocess.Popen([py, "-m", "uvicorn", "app.main:app", "--port", str(port)], env=env, cwd=os.path.join(repo, "workbench", "backend"),
                       stdout=open(os.path.join(tmp, "server.log"), "w"), stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{port}"


def wait(url):
    for _ in range(300):
        try:
            urllib.request.urlopen(url, timeout=1)
            return
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    raise RuntimeError(url)


def rss_mb(pid):
    with open(f"/proc/{pid}/status") as f:
        return next(int(line.split()[1]) for line in f if line.startswith("VmRSS")) / 1024


def cpu_s(pid):
    with open(f"/proc/{pid}/stat") as f:
        parts = f.read().rsplit(")", 1)[1].split()
    return (int(parts[11]) + int(parts[12])) / os.sysconf("SC_CLK_TCK")


opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())


def call(path, body=None, method=None):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={"X-Workbench": "1", "Content-Type": "application/json"})
    with opener.open(req, timeout=60) as r:
        return r.read()


try:
    wait(base + "/healthz")
    out = {"label": label, "rss_start_mb": round(rss_mb(srv.pid), 1)}
    call("/api/setup", {"password": "correct horse battery"})
    call("/api/ai-mode", {"mode": "custom"}, "PUT")
    call("/api/providers/connect", {"name": "Stand-in", "base_url": f"http://127.0.0.1:{ai_port}/v1", "api_key": "x"})
    for q in ("How should I plan my week?", "Remind me to water the plants"):
        call("/api/assistant/chat", {"message": q})
    cid = json.loads(call("/api/conversations"))[0]["id"]
    time.sleep(5)
    c0 = cpu_s(srv.pid)
    time.sleep(60)
    out["cpu_idle_s"] = round(cpu_s(srv.pid) - c0, 2)
    out["rss_idle_mb"] = round(rss_mb(srv.pid), 1)
    paths = ["/api/status", "/api/models", f"/api/conversations/{cid}", "/api/conversations", "/api/tasks"]
    if b"connection-directory" not in call("/api/status") and os.path.exists(os.path.join(repo, "workbench", "backend", "app", "connections.py")):
        paths += ["/api/connection-directory", f"/api/conversations/{cid}/jobs"]
    out["api_ms"] = {}
    for p in paths:
        ts = []
        for _ in range(20):
            t = time.perf_counter()
            call(p)
            ts.append((time.perf_counter() - t) * 1000)
        out["api_ms"][p.replace(str(cid), "{id}")] = round(statistics.median(ts), 1)
    print(json.dumps(out))
finally:
    srv.terminate()
    ai.terminate()
