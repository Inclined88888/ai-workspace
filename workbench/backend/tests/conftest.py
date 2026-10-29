"""Test harness: a scripted OpenAI compatible model server plus a fresh Workbench app per test session."""

import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

SCRIPT: list = []          # queued replies: dict message or callable(messages) -> dict
CALLS: list[dict] = []     # every request body the fake model received


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


fake = FastAPI()


def _tool_pairing_problem(messages: list[dict]) -> str:
    """Every assistant tool call must be answered by a tool message before the next non-tool message."""
    pending: set[str] = set()
    for m in messages:
        if m["role"] == "tool":
            if m.get("tool_call_id") not in pending:
                return f"tool message {m.get('tool_call_id')} has no matching tool call"
            pending.discard(m["tool_call_id"])
            continue
        if pending:
            return f"tool_call_ids did not have response messages: {sorted(pending)}"
        if m["role"] == "assistant":
            pending = {tc["id"] for tc in m.get("tool_calls") or []}
    return f"tool_call_ids did not have response messages: {sorted(pending)}" if pending else ""


@fake.post("/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    CALLS.append(body)
    problem = _tool_pairing_problem(body["messages"])
    if problem:  # same rule OpenAI and Anthropic enforce
        return JSONResponse({"error": {"message": problem}}, status_code=400)
    sys_prompt = body["messages"][0]["content"] if body["messages"] and body["messages"][0]["role"] == "system" else ""
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


OLLAMA_CALLS: list[dict] = []
OLLAMA_SCRIPT: list = []


@fake.post("/api/chat")
async def ollama_chat(req: Request):
    """Ollama's native endpoint: tool arguments are objects, usage is prompt_eval_count/eval_count."""
    body = await req.json()
    OLLAMA_CALLS.append(body)
    nxt = OLLAMA_SCRIPT.pop(0) if OLLAMA_SCRIPT else {"content": "ok"}
    nxt = nxt(body) if callable(nxt) else nxt
    return {"model": body["model"], "message": {"role": "assistant", **nxt}, "done": True,
            "prompt_eval_count": nxt.get("_prompt_tokens", 900), "eval_count": 50}


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


@pytest.fixture(scope="session")
def client(fake_url, tmp_path_factory):
    os.environ.update(APP_SECRET="a" * 40, TOOLS_API_KEY="k" * 30, DATA_DIR=str(tmp_path_factory.mktemp("data")))
    from fastapi.testclient import TestClient

    from app import db, main

    with TestClient(main.app) as c:
        h = {"X-Workbench": "1"}
        assert c.post("/api/setup", json={"password": "testpassword1"}, headers=h).status_code == 200
        r = c.post("/api/providers", json={"name": "Fake", "kind": "openai", "base_url": fake_url, "api_key": "x"}, headers=h)
        pid = r.json()["id"]
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
