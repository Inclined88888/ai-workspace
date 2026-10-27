"""Test harness: a scripted OpenAI compatible model server plus a fresh Workbench app per test session."""

import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request

SCRIPT: list = []          # queued replies: dict message or callable(messages) -> dict
CALLS: list[dict] = []     # every request body the fake model received
VISION: dict = {}          # VISION["answer"] = callable(prompt_text, message) -> reply, for requests that carry an image
VISION_CALLS: list[dict] = []


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


fake = FastAPI()


@fake.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    CALLS.append(body)
    sys_prompt = body["messages"][0]["content"] if body["messages"] and body["messages"][0]["role"] == "system" else ""
    image_msg = next((m for m in body["messages"] if isinstance(m.get("content"), list)), None)
    if image_msg is not None:
        VISION_CALLS.append(body)
        text = " ".join(p.get("text", "") for p in image_msg["content"] if p.get("type") == "text")
        content = VISION["answer"](text, image_msg) if VISION.get("answer") else ("Red" if "LEFT half" in text else '{"found": false}')
        return {"choices": [{"message": {"role": "assistant", "content": content}}], "usage": {"prompt_tokens": 500, "completion_tokens": 20}}
    if "long term memory" in sys_prompt:
        msg = {"role": "assistant", "content": json.dumps([{"content": "The user is a nurse in Sydney", "kind": "profile"}])}
    elif "reusable skill" in sys_prompt:
        msg = {"role": "assistant", "content": json.dumps({"save": True, "name": "weekly-plan", "description": "Plan the user's week",
                                                           "steps": "1. get_agenda\n2. list_tasks\n3. summarise"})}
    elif SCRIPT:
        nxt = SCRIPT.pop(0)
        msg = nxt(body["messages"]) if callable(nxt) else nxt
    else:
        msg = {"role": "assistant", "content": "ok"}
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


@fake.post("/v1/embeddings")
async def embeddings(req: Request):
    """Deterministic stand in for an embedding model: words hashed into 64 dimensions (tests plumbing, not meaning)."""
    import hashlib
    import re

    body = await req.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    out = []
    for i, text in enumerate(inputs):
        v = [0.0] * 64
        for w in re.findall(r"[a-z]+", text.lower()):
            v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
        out.append({"index": i, "embedding": v})
    return {"data": out}


@fake.get("/v1/models")
def models():
    return {"data": [{"id": "fake-model"}]}


@pytest.fixture(scope="session")
def fake_url():
    port = _port()
    server = uvicorn.Server(uvicorn.Config(fake, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


RUNNER: dict = {}  # the real runner process used by the tests: {"url", "token", "workspace"}


@pytest.fixture(scope="session")
def runner(tmp_path_factory):
    """The real runner service (workbench/runner/runner.py) in its own process with a temporary workspace."""
    import subprocess
    import sys
    import urllib.request

    ws, port, token = tmp_path_factory.mktemp("workspace"), _port(), "r" * 32
    # RUNNER_PROBE "": internet access unknown, so the outside-content rules for downloads apply as when online
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "RUNNER_TOKEN": token, "WORKSPACE": str(ws), "RUNNER_PORT": str(port), "RUNNER_PROBE": ""}
    proc = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(__file__), "..", "..", "runner", "runner.py")], env=env)
    for _ in range(100):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1)
            break
        except OSError:
            time.sleep(0.05)
    RUNNER.update(url=f"http://127.0.0.1:{port}", token=token, workspace=str(ws))
    yield RUNNER
    proc.terminate()
    proc.wait(5)


@pytest.fixture(scope="session")
def client(fake_url, runner, tmp_path_factory):
    os.environ.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR=str(tmp_path_factory.mktemp("data")),
                      RUNNER_URL=runner["url"], RUNNER_TOKEN=runner["token"])
    for k in ("GITHUB_TOKEN", "BRAVE_API_KEY", "TAVILY_API_KEY"):  # never let a real key in the environment reach a test
        os.environ.pop(k, None)
    from fastapi.testclient import TestClient

    from app import db, main

    with TestClient(main.app) as c:
        h = {"X-Workbench": "1"}
        assert c.post("/api/setup", json={"password": "testpassword1"}, headers=h).status_code == 200
        r = c.post("/api/providers", json={"name": "Fake", "kind": "openai", "base_url": fake_url, "api_key": "x"}, headers=h)
        pid = r.json()["id"]
        for _ in range(100):  # adding a provider checks it in the background; wait so it cannot take scripted replies
            if db.get_setting(f"provider_check:{pid}"):
                break
            time.sleep(0.05)
        c.put("/api/settings", json={"default_model": f"{pid}/fake-model"}, headers=h)
        c.headers.update(h)
        c.pid = pid
        db.set_setting("learning_enabled", "1")
        yield c


def tool_call(name: str, args: dict, call_id: str = "c1") -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}


def events(resp) -> list[dict]:
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]
