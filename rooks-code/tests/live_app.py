"""A real AI Workspace backend (uvicorn, temp DATA_DIR, provider keys stripped) for rooks end to end tests."""

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

BACKEND = Path(__file__).resolve().parents[2] / "workbench" / "backend"


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

    def call_sse(self, path: str, body: dict) -> list[dict]:
        """POST and read a server-sent event stream to the end."""
        req = urllib.request.Request(self.url + path, method="POST", data=json.dumps(body).encode(),
                                     headers={"X-Workbench": "1", "Content-Type": "application/json"})
        out = []
        with self.http.open(req, timeout=60) as r:
            for line in r:
                line = line.decode().strip()
                if line.startswith("data: ") and line[6:] != "[DONE]":
                    out.append(json.loads(line[6:]))
        return out

    def close(self):
        self.proc.terminate()
        self.proc.wait(10)
        self.log.close()


@pytest.fixture
def app(tmp_path):
    a = App(tmp_path)
    yield a
    a.close()




def openai_reply(text="", calls=()):
    """One reply as OpenAI chat-completion stream chunks (the app streams tool-using turns)."""
    frames = [{"choices": [{"index": 0, "delta": {"content": text}}]}] if text else []
    for i, (name, args) in enumerate(calls):
        frames.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                                                          "function": {"name": name, "arguments": json.dumps(args)}}]}}]})
    frames.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if calls else "stop"}]})
    frames.append({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    return frames + ["[DONE]"]


def setup(app, model_url: str) -> str:
    """First-run setup with a fake provider. Returns a terminal access token."""
    app.call("POST", "/api/setup", {"password": "testpassword1"})
    pid = app.call("POST", "/api/providers", {"name": "Fake", "kind": "openai", "base_url": model_url, "api_key": "x"})["id"]
    app.call("PUT", "/api/settings", {"default_model": f"{pid}/fake-model"})
    return app.call("POST", "/api/cli-token")["token"]
