"""End to end over real processes: the AI Workspace backend (uvicorn) and the real `rooks` CLI, with a scripted model.

rooks links to an app conversation, the app's shared loop runs the turn, the file edit is delegated back to rooks, rooks
shows the diff and asks, the answer is typed in the terminal, the file changes on disk, and the app's conversation,
ledger and Approvals all show the same step.
"""

import http.cookiejar
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from conftest import rooks
from fakes import FakeModel


def openai_reply(text="", calls=()):
    """One reply as OpenAI chat-completion stream chunks (the app streams tool-using turns)."""
    frames = [{"choices": [{"index": 0, "delta": {"content": text}}]}] if text else []
    for i, (name, args) in enumerate(calls):
        frames.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                                                          "function": {"name": name, "arguments": json.dumps(args)}}]}}]})
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
    frames.append({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    return frames + ["[DONE]"]

BACKEND = Path(__file__).resolve().parents[2] / "workbench" / "backend"
pytest.importorskip("fastapi")
pytest.importorskip("uvicorn")


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class App:
    def __init__(self, tmp: Path):
        self.port = _port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {k: v for k, v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN"))}
        env.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR=str(tmp / "data"), MODEL_CATALOG_REFRESH="0",
                   ROOKS_COMPANION_FILE=str(tmp / "companion.json"), PYTHONPATH=str(BACKEND))
        self.log = open(tmp / "backend.log", "w")  # noqa: SIM115
        self.proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port),
                                      "--log-level", "warning"], cwd=BACKEND, env=env, stdout=self.log, stderr=subprocess.STDOUT)
        jar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        for _ in range(200):
            try:
                urllib.request.urlopen(self.url + "/healthz", timeout=1).close()
                return
            except OSError:
                time.sleep(0.1)
        raise RuntimeError("backend did not start: " + (tmp / "backend.log").read_text()[-2000:])

    def call(self, method: str, path: str, body=None, token: str = ""):
        h = {"X-Workbench": "1", "Content-Type": "application/json"}
        if token:
            h["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(self.url + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        with self.http.open(req, timeout=30) as r:
            return json.loads(r.read())

    def close(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()


@pytest.fixture
def app(tmp_path):
    a = App(tmp_path)
    yield a
    a.close()


def test_rooks_runs_on_the_apps_shared_loop_and_the_edit_is_approved_in_the_terminal(project, app):
    model = FakeModel([
        openai_reply("", [("edit_file", {"path": "calc.py", "old_text": "return a - b", "new_text": "return a + b"})]),
        openai_reply("Fixed add(): it subtracted."),
    ])
    try:
        app.call("POST", "/api/setup", {"password": "testpassword1"})
        pid = app.call("POST", "/api/providers", {"name": "Fake", "kind": "openai", "base_url": model.url, "api_key": "x"})["id"]
        app.call("PUT", "/api/settings", {"default_model": f"{pid}/fake-model"})
        token = app.call("POST", "/api/cli-token")["token"]

        r = rooks(project, "-P", "workbench", "fix add in calc.py", stdin="y\n",
                  env={"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": token})
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "Linked to Workbench conversation #" in out
        cid = int(out.split("Linked to Workbench conversation #")[1].split()[0])

        # the change really happened, after the diff and the prompt
        assert (project / "calc.py").read_text() == "def add(a, b):\n    return a + b\n"
        assert "-    return a - b" in out and "+    return a + b" in out
        assert f"Also waiting in the app (Approvals #" in out
        assert "Fixed add()" in out

        # the app saw the same turn: one conversation, the delegated step, the final answer
        conv = app.call("GET", f"/api/conversations/{cid}")
        assert conv["channel"] == "cli"
        roles = [(m["role"], m["name"]) for m in conv["messages"]]
        assert ("tool", "edit_file") in roles
        assert conv["messages"][-1]["content"].startswith("Fixed add()")
        tool_msg = next(m for m in conv["messages"] if m["role"] == "tool")
        assert tool_msg["content"].startswith("edited calc.py")

        # the approval was recorded once, answered in the terminal, with the diff the app's card renders
        aid = int(out.split("Approvals #")[1].split(")")[0])
        st = app.call("GET", f"/v1/rooks/approvals/{aid}", token=token)
        assert st["status"] == "approved"
        assert not [a for a in app.call("GET", f"/api/approvals?conversation_id={cid}") if a["status"] == "pending"]

        # the model was never asked to run the workspace's own read_file/edit tools: it got the terminal's
        offered = {t["function"]["name"] for t in model.requests[0]["body"]["tools"]}
        assert {"read_file", "edit_file", "run_command"} <= offered
        edit = next(t for t in model.requests[0]["body"]["tools"] if t["function"]["name"] == "edit_file")
        assert set(edit["function"]["parameters"]["properties"]) == {"path", "old_text", "new_text", "replace_all"}
    finally:
        model.close()


def test_an_edit_denied_in_the_app_is_not_made(project, app):
    """No keyboard answer (stdin closed): the request waits in the app; denying it there leaves the file alone."""
    import threading

    model = FakeModel([
        openai_reply("", [("edit_file", {"path": "calc.py", "old_text": "return a - b", "new_text": "return a * b"})]),
        openai_reply("Left it alone."),
    ])
    try:
        app.call("POST", "/api/setup", {"password": "testpassword1"})
        pid = app.call("POST", "/api/providers", {"name": "Fake", "kind": "openai", "base_url": model.url, "api_key": "x"})["id"]
        app.call("PUT", "/api/settings", {"default_model": f"{pid}/fake-model"})
        token = app.call("POST", "/api/cli-token")["token"]

        def deny_in_app():
            for _ in range(300):
                pending = [a for a in app.call("GET", "/api/approvals") if a["status"] == "pending" and a["tool"] == "edit_file"]
                if pending:
                    app.call("POST", f"/api/approvals/{pending[0]['id']}", {"approve": False})
                    return
                time.sleep(0.1)

        t = threading.Thread(target=deny_in_app, daemon=True)
        t.start()
        r = rooks(project, "-P", "workbench", "change add", stdin="",
                  env={"WORKBENCH_URL": app.url, "WORKBENCH_API_KEY": token})
        t.join(5)
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "Denied in the app." in out
        assert (project / "calc.py").read_text() == "def add(a, b):\n    return a - b\n"
    finally:
        model.close()
