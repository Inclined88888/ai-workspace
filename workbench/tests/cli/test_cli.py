"""The installed `rooks` command against a real ROOKS server with the stand-in AI (fake_ai.py, not a model).

It runs the executable named by ROOKS_CLI (default: `rooks` on PATH), which should come from a clean environment
(`python -m venv v && v/bin/pip install ./cli`), with its own HOME so no real settings are read or written.

Usage: ROOKS_CLI=/path/to/venv/bin/rooks python3 test_cli.py
"""

import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "browser"))
from harness import PASSWORD, Stack, check, finish  # noqa: E402

ROOKS = os.environ.get("ROOKS_CLI") or shutil.which("rooks") or ""
if not ROOKS:
    sys.exit("Install the CLI first (pip install ./cli) or set ROOKS_CLI")
HOME = tempfile.mkdtemp(prefix="rooks-cli-home-")
ENV = {"HOME": HOME, "XDG_CONFIG_HOME": os.path.join(HOME, ".config"), "PATH": os.environ.get("PATH", ""), "NO_PROXY": "127.0.0.1,localhost",
       "ROOKS_NO_KEYRING": "1", "LANG": "C.UTF-8"}


def rooks(*args, stdin: str = "", env: dict | None = None, timeout: int = 120):
    r = subprocess.run([ROOKS, *args], input=stdin, capture_output=True, text=True, env={**ENV, **(env or {})}, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def js(out: str):
    return json.loads(out.strip().splitlines()[-1])


st = Stack("cli")
import requests  # noqa: E402 - the backend's own dependency, present wherever the server runs

s = requests.Session()
H = {"X-Workbench": "1"}
s.post(st.url + "/api/setup", headers=H, json={"password": PASSWORD})
s.put(st.url + "/api/ai-mode", headers=H, json={"mode": "custom"})
s.post(st.url + "/api/providers/connect", headers=H, json={"name": "Stand-in AI", "base_url": st.ai + "/v1", "api_key": "x"})
token = s.post(st.url + "/api/cli-token", headers=H).json()["token"]

# Before connecting, and with a wrong token
code, out, err = rooks("status")
check("not connected: exit 3 and says how to connect", code == 3 and "rooks connect" in err, (code, err))
code, out, err = rooks("connect", st.url, "--token-stdin", stdin="wrong-token\n")
check("a wrong token is refused with exit 3", code == 3 and "did not accept" in err, (code, err))

# Connect: the token comes from stdin, never the command line, and is kept privately
code, out, err = rooks("--json", "connect", st.url, "--token-stdin", stdin=token + "\n")
cfg_path = os.path.join(HOME, ".config", "rooks", "config.json")
check("connect succeeds and reports the server version", code == 0 and js(out)["server_version"], (code, out, err))
mode = stat.S_IMODE(os.stat(cfg_path).st_mode)
check("the settings file is readable only by you (0600)", mode == 0o600, oct(mode))
check("the token is never printed", token not in out + err)
code, out, err = rooks("--json", "doctor")
d = js(out)
check("doctor: every check passes, including API version and model", code == 0 and d["ok"], d)

# Ask: the conversation is the browser's conversation
code, out, err = rooks("--json", "ask", "How should I plan my week?")
r = js(out)
cid = r["conversation_id"]
check("ask answers and returns the conversation", code == 0 and "Pick three outcomes" in r["reply"], (code, out[-300:], err[-300:]))
conv = s.get(st.url + f"/api/conversations/{cid}", headers=H).json()
check("the browser opens the same conversation, with the CLI's message and the reply",
      conv["channel"] == "cli" and [m["role"] for m in conv["messages"] if m["content"]] == ["user", "assistant"], conv.get("channel"))
check("it is in the browser's conversation list", any(c["id"] == cid for c in s.get(st.url + "/api/conversations", headers=H).json()))
code, out, err = rooks("--json", "ask", "-c", "Remind me to call the plumber tomorrow")
r = js(out)
check("-c continues the same conversation and the work is really done",
      r["conversation_id"] == cid and "create_task" in r["tools"] and any(t["title"] == "Call the plumber" for t in s.get(st.url + "/api/tasks", headers=H).json()), r)
code, out, err = rooks("--json", "ask", stdin="What is on my task list?\n")
check("a message can come from standard input", code == 0 and "Call the plumber" in js(out)["reply"], out[-300:])
s.post(st.url + "/api/assistant/chat", headers=H, json={"message": "How should I plan my week?", "conversation_id": cid}).text
code, out, err = rooks("--json", "show", str(cid))
check("show prints the conversation including the browser's later messages", code == 0 and len(js(out)["messages"]) >= 6, len(js(out)["messages"]))

# Approvals: never decided without you
code, out, err = rooks("--json", "--no-input", "ask", "Check in with me every weekday morning about deadlines")
r = js(out)
pending = [a["id"] for a in s.get(st.url + "/api/approvals", headers=H).json()]
check("an action that needs approval exits 4 with --no-input, and nothing ran", code == 4 and r["approvals"] and
      r["approvals"][0]["id"] in pending, (code, r.get("approvals"), pending))
aid = r["approvals"][0]["id"]
code, out, err = rooks("--json", "approvals")
check("approvals lists it with its reason", any(a["id"] == aid and a["reason"] for a in js(out)))
code, out, err = rooks("--no-input", "approve", str(aid))
check("approving without a person to ask needs --yes (exit 2)", code == 2 and "--yes" in err, (code, err))
code, out, err = rooks("--json", "approve", str(aid), "--yes")
check("approve --yes runs it once", code == 0 and js(out)["status"] == "approved", out)
code, out, err = rooks("--json", "approve", str(aid), "--yes")
check("approving again answers with the decision already made", code == 0 and js(out).get("already_decided"), out)

# Work: started from the terminal, the same item and phase in the browser's API
code, out, err = rooks("--json", "run", "Research instant asset write-off rules for small businesses", timeout=300)
r = js(out)
w = r["work"][0] if r["work"] else {}
check("run follows the research it started to the end", w.get("terminal") is True and code in (0, 5), (code, w))
check("its exit code matches the outcome", (code == 0) == (w.get("phase") == "completed"), (code, w.get("phase")))
web = s.get(st.url + f"/api/v1/work/{w.get('id')}", headers=H).json()
check("the browser sees the same work item in the same phase", web.get("phase") == w.get("phase") and web.get("conversation_id") == r["conversation_id"], web)
code, out, err = rooks("--json", "jobs")
check("jobs lists it", any(x["id"] == w.get("id") for x in js(out)))
code, out, err = rooks("--json", "jobs", "cancel", w.get("id", "research:0"))
check("cancelling finished work is refused with the reason", code == 1 and "cannot be stopped" in js(out)["error"], out)

# Failures a script can act on
code, out, err = rooks("--json", "status", env={"ROOKS_URL": "http://127.0.0.1:9"})
check("an unreachable server: exit 6, JSON error", code == 6 and js(out)["exit_code"] == 6, (code, out))


class Old(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = json.dumps({"detail": "This ROOKS server speaks API version 2; your client uses 1. Update your client."}).encode()
        self.send_response(426); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body)))
        self.end_headers(); self.wfile.write(body)


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Old)
threading.Thread(target=srv.serve_forever, daemon=True).start()
code, out, err = rooks("status", env={"ROOKS_URL": f"http://127.0.0.1:{srv.server_address[1]}"})
check("a server on another API version: exit 7 with what to update", code == 7 and "Update your client" in err, (code, err))
srv.shutdown()
code, out, err = rooks("ask")
check("nothing to ask: exit 2 with an example", code == 2 and "rooks ask" in err, (code, err))
code, out, err = rooks("--json", "status", env={"ROOKS_TOKEN": "env-token-wrong"})
check("ROOKS_TOKEN overrides the stored token and is never written", code == 3 and "env-token-wrong" not in open(cfg_path).read())

# The interactive conversation, in a real pseudo-terminal: an answer, an approval prompt answered no, /quit
import pty  # noqa: E402
import select  # noqa: E402
import time  # noqa: E402

pid, fd = pty.fork()
if pid == 0:
    os.execve(ROOKS, [ROOKS, "chat"], {**ENV, "TERM": "dumb", "NO_COLOR": "1"})
seen = ""


def until(text: str, limit: float = 60) -> bool:
    global seen
    end = time.monotonic() + limit
    while time.monotonic() < end:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                seen += os.read(fd, 65536).decode("utf-8", "replace")
            except OSError:
                return text in seen
        if text in seen:
            return True
    return False


ok_prompt = until("›")
os.write(fd, b"How should I plan my week?\n")
ok_answer = until("Pick three outcomes")
check("chat: answers in the terminal", ok_prompt and ok_answer, seen[-400:])
os.write(fd, b"Check in with me every weekday morning about deadlines\n")
ok_ask = until("Allow it once? [y/N]")
check("chat: an action that needs approval asks, showing exactly what will run", ok_ask and "create checkin" in seen and "Exactly this" in seen, seen[-600:])
os.write(fd, b"n\n")
check("chat: answering no denies it", until("Denied"), seen[-300:])
os.write(fd, b"/quit\n")
_, status = os.waitpid(pid, 0)
check("chat: /quit leaves with exit 0", os.waitstatus_to_exitcode(status) == 0, status)
denied = [a for a in s.get(st.url + "/api/approvals?status=all", headers=H).json() if a["tool"] == "create_checkin" and a["status"] == "denied"]
check("the denial is recorded on the server", bool(denied))

code, out, err = rooks("disconnect")
check("disconnect removes the settings file", code == 0 and not os.path.exists(cfg_path))
shutil.rmtree(HOME, ignore_errors=True)
sys.exit(finish())
