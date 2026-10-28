"""Test harness: a scripted OpenAI compatible model server plus a fresh Workbench app per test session."""

import asyncio
import json
import os
import socket
import threading
import time

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

SCRIPT: list = []          # queued replies: dict message or callable(messages) -> dict
CALLS: list[dict] = []     # every request body the fake model received
OLLAMA_SCRIPT: list = []   # queued native Ollama messages for /api/chat
OLLAMA_CALLS: list[dict] = []


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


fake = FastAPI()


def stream_chunks(chunks, native=False):
    async def body():
        for chunk in chunks:
            if "_gate" in chunk:
                if not await asyncio.to_thread(chunk["_gate"].wait, 3):
                    yield 'data: {"error": "consumer buffered the stream"}\n\n'
                continue
            yield (json.dumps(chunk) + "\n") if native else ("data: " + json.dumps(chunk) + "\n\n")
            await asyncio.sleep(0.001)
    return StreamingResponse(body(), media_type="application/x-ndjson" if native else "text/event-stream")


@fake.post("/v1/chat/completions")
@fake.post("/proxy/v1/chat/completions")
async def completions(req: Request):
    body = await req.json()
    CALLS.append(body)
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
    if body.get("stream"):
        if isinstance(msg, list):
            return stream_chunks(msg)
        chunks = [{"choices": [{"index": 0, "delta": {"content": msg.get("content", "")}}]}]
        for i, tc in enumerate(msg.get("tool_calls", [])):
            chunks.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, **tc}]}}]})
        chunks.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                       "usage": {"prompt_tokens": 1000, "completion_tokens": 500}})
        return stream_chunks(chunks)
    return {"choices": [{"message": msg}], "usage": {"prompt_tokens": 1000, "completion_tokens": 500}}


@fake.post("/api/chat")
async def ollama_chat(req: Request):
    """Ollama's native chat API: tool call arguments are objects, usage is prompt_eval_count/eval_count."""
    body = await req.json()
    OLLAMA_CALLS.append(body)
    scripted = bool(OLLAMA_SCRIPT)
    nxt = OLLAMA_SCRIPT.pop(0) if scripted else {"role": "assistant", "content": "ok"}
    msg = nxt(body["messages"]) if callable(nxt) else nxt
    if body.get("stream"):
        if isinstance(msg, list):
            return stream_chunks(msg, native=True)
        if scripted:
            lines = [{"message": msg, "done": False}, {"done": True, "prompt_eval_count": 1200, "eval_count": 30}]
        else:
            lines = [{"message": {"role": "assistant", "content": w}, "done": False} for w in ("Hel", "lo")]
            lines.append({"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 7, "eval_count": 2})
        return stream_chunks(lines, native=True)
    return {"model": body["model"], "message": msg, "done": True, "prompt_eval_count": 1200, "eval_count": 30}


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
