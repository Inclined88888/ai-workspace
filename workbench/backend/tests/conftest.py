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
    if body.get("model") == "fail-model":
        return JSONResponse({"error": {"message": "upstream exploded"}}, status_code=500)
    if body.get("stream"):
        return _sse([{"choices": [{"delta": {"content": "streamed"}}]},
                     {"choices": [], "usage": {"prompt_tokens": 600, "completion_tokens": 20}}])
    rf = body.get("response_format")
    if rf is not None and not (rf.get("type") == "json_schema" and {"name", "schema", "strict"} <= set(rf.get("json_schema") or {})):
        return JSONResponse({"error": {"message": f"bad response_format {rf}"}}, status_code=400)
    if body.get("model") == "leaky-model":  # some providers quote the rejected key back
        return JSONResponse({"error": {"message": "Incorrect API key provided: sk-proj-LEAKEDabcdefghijklmnopqrstu"}}, status_code=401)
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
    if body.get("model") == "fail-model":
        return JSONResponse({"error": "upstream exploded"}, status_code=500)
    if body.get("stream"):
        from fastapi.responses import StreamingResponse

        lines = [{"message": {"role": "assistant", "content": "streamed"}, "done": False},
                 {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 640, "eval_count": 22}]
        return StreamingResponse((json.dumps(x) + "\n" for x in lines), media_type="application/x-ndjson")
    for m in body["messages"]:
        bad_args = any(not isinstance(tc["function"].get("arguments"), dict) for tc in m.get("tool_calls") or [])
        if bad_args or (m["role"] == "tool" and not m.get("tool_name")):
            return JSONResponse({"error": f"invalid message {m}"}, status_code=400)
    if OLLAMA_SCRIPT:
        nxt = OLLAMA_SCRIPT.pop(0)
        nxt = nxt(body) if callable(nxt) else nxt
    else:  # the shared script, in Ollama's shape: tool arguments are objects, calls have no ids
        r = _next_reply(body)
        nxt = {"content": r.get("content") or "", "tool_calls": [
            {"function": {"name": tc["function"]["name"], "arguments": json.loads(tc["function"]["arguments"])}} for tc in r.get("tool_calls") or []]}
    NATIVE_CALLS.append({"path": "/api/chat", "headers": dict(req.headers), "body": body})
    return {"model": body["model"], "message": {"role": "assistant", **nxt}, "done": True,
            "prompt_eval_count": nxt.get("_prompt_tokens", 900), "eval_count": 50}


NATIVE_CALLS: list[dict] = []   # every request the native protocol endpoints received: {"path", "headers", "body"}
CONTRACT_KEY = "contract-key"
OAUTH_TOKEN = "oauth-access-token"


def _next_reply(body: dict) -> dict:
    """The next scripted reply, in the one OpenAI-chat shape the tests write; each endpoint translates it."""
    if body.get("model") == "fail-model":
        return {"_fail": True}
    nxt = SCRIPT.pop(0) if SCRIPT else {"role": "assistant", "content": "ok"}
    return nxt(body) if callable(nxt) else nxt


def _sse(events: list[dict], named: bool = False):
    from fastapi.responses import StreamingResponse

    def gen():
        for e in events:
            yield (f"event: {e['type']}\n" if named else "") + f"data: {json.dumps(e)}\n\n"
        if not named:
            yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@fake.get("/api/tags")
def ollama_tags():
    return {"models": [{"name": "qwen3:4b"}, {"name": "llama3.2:3b"}]}


@fake.post("/v1/responses")
async def responses(req: Request):
    """OpenAI Responses API, checked against the documented shapes (openai SDK types: responses/*)."""
    body = await req.json()
    NATIVE_CALLS.append({"path": "/v1/responses", "headers": dict(req.headers), "body": body})
    if req.headers.get("authorization") not in (f"Bearer {CONTRACT_KEY}", f"Bearer {OAUTH_TOKEN}"):
        return JSONResponse({"error": {"message": "bad credentials"}}, status_code=401)
    problems = []
    if "messages" in body or not isinstance(body.get("input"), list):
        problems.append("input must be a list of items; there is no messages field")
    calls = set()
    for it in body.get("input", []):
        if it.get("type") == "function_call":
            if not all(isinstance(it.get(k), str) for k in ("call_id", "name", "arguments")):
                problems.append(f"bad function_call {it}")
            calls.add(it.get("call_id"))
        elif it.get("type") == "function_call_output":
            if it.get("call_id") not in calls:
                problems.append(f"function_call_output for unknown call_id {it.get('call_id')}")
        elif it.get("role") not in ("user", "assistant") or "content" not in it:
            problems.append(f"bad input item {it}")
    for t in body.get("tools") or []:
        if t.get("type") != "function" or "name" not in t or "parameters" not in t or "strict" not in t:
            problems.append(f"bad tool {t}")
    fmt = (body.get("text") or {}).get("format")
    if fmt is not None and not (fmt.get("type") == "json_schema" and {"name", "schema"} <= set(fmt)):
        problems.append(f"bad text.format {fmt}")
    for it in body.get("input", []):
        for part in it.get("content") if isinstance(it.get("content"), list) else []:
            if part.get("type") == "input_image" and not str(part.get("image_url", "")).startswith("data:image/"):
                problems.append("input_image needs an image_url")
    if problems:
        return JSONResponse({"error": {"message": "; ".join(problems)}}, status_code=400)
    reply = _next_reply(body)
    if reply.get("_fail"):
        return JSONResponse({"error": {"message": "upstream exploded"}}, status_code=500)
    usage = {"input_tokens": 700, "output_tokens": 30, "total_tokens": 730}
    if body.get("stream"):
        return _sse([{"type": "response.output_text.delta", "delta": "streamed", "item_id": "m", "output_index": 0, "content_index": 0},
                     {"type": "response.completed", "response": {"status": "completed", "output": [], "usage": usage}}], named=True)
    output = [{"type": "function_call", "call_id": tc["id"], "name": tc["function"]["name"], "arguments": tc["function"]["arguments"]}
              for tc in reply.get("tool_calls") or []]
    if reply.get("content"):
        output.insert(0, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": reply["content"]}]})
    return {"id": "resp_1", "status": "completed", "output": output, "usage": usage}


@fake.post("/v1/messages")
async def messages(req: Request):
    """Anthropic Messages API, checked against the documented shapes (docs page + anthropic SDK types)."""
    body = await req.json()
    NATIVE_CALLS.append({"path": "/v1/messages", "headers": dict(req.headers), "body": body})
    if req.headers.get("x-api-key") != CONTRACT_KEY or not req.headers.get("anthropic-version"):
        return JSONResponse({"type": "error", "error": {"type": "authentication_error", "message": "bad key"}}, status_code=401)
    problems = [] if isinstance(body.get("max_tokens"), int) else ["max_tokens is required"]
    msgs = body.get("messages") or []
    uses = set()
    for i, m in enumerate(msgs):
        if m["role"] not in ("user", "assistant"):
            problems.append(f"role {m['role']} is not allowed; system is top level")
        if i and msgs[i - 1]["role"] == m["role"]:
            problems.append("roles must alternate")
        for b in m["content"] if isinstance(m["content"], list) else []:
            if b["type"] == "tool_use":
                uses.add(b["id"])
                if m["role"] != "assistant" or not isinstance(b.get("input"), dict):
                    problems.append("tool_use belongs in assistant turns with an object input")
            if b["type"] == "tool_result" and (m["role"] != "user" or b.get("tool_use_id") not in uses):
                problems.append(f"tool_result must answer an earlier tool_use: {b.get('tool_use_id')}")
    if msgs and msgs[0]["role"] != "user":
        problems.append("the first message must be from the user")
    for t in body.get("tools") or []:
        if "input_schema" not in t or "name" not in t:
            problems.append(f"bad tool {t}")
    forced = (body.get("tool_choice") or {}).get("name") if (body.get("tool_choice") or {}).get("type") == "tool" else None
    if forced and forced not in {t["name"] for t in body.get("tools") or []}:
        problems.append(f"tool_choice names a tool that was not given: {forced}")
    for m in msgs:
        for b in m["content"] if isinstance(m["content"], list) else []:
            if b["type"] == "image" and not (b.get("source", {}).get("type") == "base64" and b["source"].get("data")):
                problems.append("image blocks need a base64 source")
    if problems:
        return JSONResponse({"type": "error", "error": {"type": "invalid_request_error", "message": "; ".join(problems)}}, status_code=400)
    reply = _next_reply(body)
    if forced and not reply.get("_fail"):  # a forced tool answers with a tool_use whose input is the structured answer
        return {"id": "msg_1", "type": "message", "role": "assistant", "stop_reason": "tool_use",
                "content": [{"type": "tool_use", "id": "toolu_forced", "name": forced, "input": json.loads(reply["content"])}],
                "usage": {"input_tokens": 650, "output_tokens": 25}}
    if reply.get("_fail"):
        return JSONResponse({"type": "error", "error": {"type": "api_error", "message": "upstream exploded"}}, status_code=500)
    if body.get("stream"):
        return _sse([{"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "content": [],
                                                          "usage": {"input_tokens": 650, "output_tokens": 1}}},
                     {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                     {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "streamed"}},
                     {"type": "content_block_stop", "index": 0},
                     {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 25}},
                     {"type": "message_stop"}], named=True)
    content = [{"type": "text", "text": reply["content"]}] if reply.get("content") else []
    content += [{"type": "tool_use", "id": "toolu_" + tc["id"], "name": tc["function"]["name"], "input": json.loads(tc["function"]["arguments"])}
                for tc in reply.get("tool_calls") or []]
    return {"id": "msg_1", "type": "message", "role": "assistant", "content": content,
            "stop_reason": "tool_use" if reply.get("tool_calls") else "end_turn", "usage": {"input_tokens": 650, "output_tokens": 25}}


BROWSER_JOBS: list[dict] = []


@fake.post("/api/jobs")
async def browser_job(req: Request):
    """The browser service's job API, enough for the browse_web tool."""
    BROWSER_JOBS.append(await req.json())
    return {"id": f"job{len(BROWSER_JOBS)}", "status": "queued"}


@fake.get("/api/jobs/{job_id}")
def browser_job_status(job_id: str):
    return {"id": job_id, "status": "done", "result": {"final": "Found it.", "success": True, "urls": ["https://example.com"]}}


@fake.get("/v1/models")
def models():
    return {"data": [{"id": "fake-model"}]}


@fake.get("/leaky/v1/models")  # a provider that quotes the rejected key back in its error
def leaky_models():
    return JSONResponse({"error": {"message": "Incorrect API key provided: sk-proj-LEAKEDabcdefghijklmnopqrstu"}}, status_code=401)


@fake.get("/healthz")  # stands in for an add-on's health endpoint (browser agent, SearXNG)
def fake_healthz():
    return {"ok": True}


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
