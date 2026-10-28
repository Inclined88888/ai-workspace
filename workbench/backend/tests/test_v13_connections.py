"""v13: one directory of connections, grouped by what they are; each capability tested and recorded separately; the
optional any-llm engine for Anthropic connections, against a stand-in of Anthropic's Messages API."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import CALLS, SCRIPT, tool_call


@pytest.fixture(autouse=True)
def checked(client):
    """The suite's model as last checked OK (other test files leave other results behind); restored afterwards."""
    from app import db

    key = f"provider_check:{client.pid}"
    before = db.get_setting(key)
    db.set_setting(key, json.dumps({"ok": True, "recommended": "fake-model", "checked_at": db.now(), "error": ""}))
    for k in [r["key"] for r in db.all("SELECT key FROM settings WHERE key LIKE 'cap_check:%'")]:
        db.run("DELETE FROM settings WHERE key = ?", k)
    yield
    db.set_setting(key, before or "")


def _entry(d, eid):
    return next(e for e in d["entries"] if e["id"] == eid)


def test_the_directory_groups_connections_by_what_they_are(client):
    from app import db

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenAI', 'openai', 'https://api.openai.com/v1', '', 'gpt-x', 0, ?)", db.now())
    gw = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                "('Gateway', 'openai', 'https://llm.example.com/v1', '', 'm', 0, ?)", db.now())
    try:
        d = client.get("/api/connection-directory").json()
        fake = _entry(d, f"provider:{client.pid}")
        assert fake["category"] == "C"  # the suite's model server is on this computer
        assert fake["status"] == "connected" and "answered a request with a tool attached" in fake["status_detail"]
        oa = _entry(d, f"provider:{pid}")
        assert oa["category"] == "A" and oa["billing"] == "paid" and oa["status"] == "off"
        assert _entry(d, f"provider:{gw}")["category"] == "E" and _entry(d, f"provider:{gw}")["billing"] == "unknown"
        codex, claude = _entry(d, "coding:codex"), _entry(d, "coding:claude")
        assert codex["category"] == claude["category"] == "B" and not codex["powers_chat"] and not claude["powers_chat"]
        assert "ROOKS chat cannot use a Claude subscription" in claude["limits"]
        assert _entry(d, "library:any-llm")["category"] == "D"
        air = _entry(d, "local:airllm")
        assert air["experimental"] and air["status"] == "not_connected" and "Nothing is installed" in air["status_detail"]
        # a credential that works is not a list of working capabilities
        assert all(c["state"] == "not_tested" for c in fake["capabilities"].values())
    finally:
        db.run("DELETE FROM providers WHERE id IN (?, ?)", pid, gw)


def test_each_capability_is_tested_and_recorded_separately(client):
    ref = f"{client.pid}/fake-model"
    SCRIPT[:] = [{"role": "assistant", "content": "READY"},
                 tool_call("lookup_code", {"room": "B12"}),
                 tool_call("lookup_code", {"room": "B12"}), {"role": "assistant", "content": "The code for B12 is 4417."},
                 {"role": "assistant", "content": '{"sum": 42, "word": "blue"}'}]
    r = client.post("/api/connection-directory/test", json={"ref": ref, "caps": ["chat", "tools", "tool_roundtrip", "json", "streaming"]}).json()
    res = r["results"]
    assert res["chat"]["ok"] and res["tools"]["ok"] and res["tool_roundtrip"]["ok"] and res["json"]["ok"]
    # the stand-in server does not stream: streaming fails on its own, and nothing else is affected
    assert res["streaming"]["ok"] is False and res["streaming"]["detail"]
    caps = _entry(client.get("/api/connection-directory").json(), f"provider:{client.pid}")["capabilities"]
    assert caps["chat"]["state"] == "passed" and caps["streaming"]["state"] == "failed" and caps["vision"]["state"] == "not_tested"


def test_a_model_that_does_not_call_the_tool_fails_only_the_tool_tests(client):
    ref = f"{client.pid}/fake-model"
    SCRIPT[:] = [{"role": "assistant", "content": "I would look it up."}, tool_call("lookup_code", {"room": "A1"})]
    res = client.post("/api/connection-directory/test", json={"ref": ref, "caps": ["tools", "tool_roundtrip"]}).json()["results"]
    assert not res["tools"]["ok"] and "did not call the tool" in res["tools"]["detail"]
    assert not res["tool_roundtrip"]["ok"] and "wrong arguments" in res["tool_roundtrip"]["detail"]


def test_a_test_that_may_be_charged_needs_your_confirmation(client):
    from app import db

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenAI', 'openai', 'https://api.openai.com/v1', '', 'gpt-x', 1, ?)", db.now())
    try:
        n = len(CALLS)
        r = client.post("/api/connection-directory/test", json={"ref": f"{pid}/gpt-x", "caps": ["chat"]})
        assert r.status_code == 402 and "may be charged" in r.json()["detail"] and len(CALLS) == n
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


def test_free_mode_does_not_test_a_model_it_does_not_allow(client):
    from app import db

    db.set_setting("ai_mode", "free")
    try:
        n = len(CALLS)
        r = client.post("/api/connection-directory/test", json={"ref": f"{client.pid}/fake-model", "caps": ["chat"], "allow_paid": True})
        assert r.status_code == 400 and "Free Cloud AI" in r.json()["detail"] and len(CALLS) == n
    finally:
        db.set_setting("ai_mode", "custom")


def test_models_are_marked_ready_or_untested(client):
    ms = client.get("/api/models").json()["models"]
    m = next(x for x in ms if x["ref"] == f"{client.pid}/fake-model")
    assert m["verified"] and m["status"] == "ready"


def test_the_engine_switch_is_for_anthropic_only(client):
    assert client.put(f"/api/providers/{client.pid}/engine", json={"engine": "any-llm"}).status_code == 400


# Anthropic's Messages API, as a stand-in: real request and response shapes, no account
SEEN: list[dict] = []


class _Messages(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append({"path": self.path, "body": body, "key": self.headers.get("x-api-key")})
        last = body["messages"][-1]
        if body.get("stream"):
            ev = [("message_start", {"type": "message_start", "message": {"id": "m1", "type": "message", "role": "assistant", "model": body["model"],
                                                                          "content": [], "stop_reason": None, "usage": {"input_tokens": 9, "output_tokens": 0}}}),
                  ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                  *[("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": t}}) for t in ("1 2 ", "3 4 ", "5")],
                  ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                  ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}),
                  ("message_stop", {"type": "message_stop"})]
            data = "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in ev).encode()
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("content-length", str(len(data))); self.end_headers()
            self.wfile.write(data)
            return
        tool_result = isinstance(last["content"], list) and any(c.get("type") == "tool_result" for c in last["content"])
        if body.get("tools") and not tool_result:
            content, stop = [{"type": "tool_use", "id": "toolu_1", "name": body["tools"][0]["name"], "input": {"room": "B12"}}], "tool_use"
        elif tool_result:
            content, stop = [{"type": "text", "text": "The code is 4417."}], "end_turn"
        else:
            content, stop = [{"type": "text", "text": "READY"}], "end_turn"
        out = json.dumps({"id": "m2", "type": "message", "role": "assistant", "model": body["model"], "content": content, "stop_reason": stop,
                          "stop_sequence": None, "usage": {"input_tokens": 20, "output_tokens": 7}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers()
        self.wfile.write(out)


@pytest.fixture
def messages_api():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Messages)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    SEEN.clear()
    yield f"http://127.0.0.1:{port}/v1"
    srv.shutdown()


def test_anthropic_through_any_llm_uses_the_native_messages_api(client, messages_api):
    pytest.importorskip("any_llm")
    from app import db, llm
    from app.security import encrypt

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('Anthropic', 'anthropic', ?, ?, 'claude-test', 1, ?)", messages_api, encrypt("sk-ant-test"), db.now())
    try:
        # until switched, the connection keeps the compatibility endpoint
        assert llm.engine(db.one("SELECT * FROM providers WHERE id = ?", pid)) == ""
        assert client.put(f"/api/providers/{pid}/engine", json={"engine": "any-llm"}).json()["engine"] == "any-llm"
        db.set_setting(f"provider_free_confirmed:{pid}", "0")
        r = client.post("/api/connection-directory/test", json={"ref": f"{pid}/claude-test", "caps": ["chat", "tools", "tool_roundtrip", "streaming"],
                                                       "allow_paid": True}).json()["results"]
        assert all(r[c]["ok"] for c in ("chat", "tools", "tool_roundtrip", "streaming")), r
        assert all(s["path"] == "/v1/messages" and s["key"] == "sk-ant-test" for s in SEEN)  # native API, never /chat/completions
        roundtrip = next(s["body"] for s in SEEN if any(isinstance(m["content"], list) and any(c.get("type") == "tool_result" for c in m["content"])
                                                        for m in s["body"]["messages"]))
        assert roundtrip["tools"][0]["name"] == "lookup_code" and "input_schema" in roundtrip["tools"][0]
        # the output cap of capability tests reaches the native request too
        assert all(s["body"]["max_tokens"] <= 64 for s in SEEN)
        # an unreachable server is explained like any other provider error
        db.run("UPDATE providers SET base_url = 'http://127.0.0.1:9/v1' WHERE id = ?", pid)
        with pytest.raises(llm.ModelError) as e:
            asyncio.run(llm._chat_once(f"{pid}/claude-test", [{"role": "user", "content": "hi"}], None, ping=True))
        assert "Anthropic" in str(e.value.detail)
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)


# OpenRouter refusing one free model to ROOKS while the key works (seen in real use, October 2026)
class _Router(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        zero = {"prompt": "0", "completion": "0"}
        if self.path.endswith("/models"):
            return self._send(200, {"data": [
                {"id": "lab/inkling-small:free", "context_length": 1048576, "supported_parameters": ["tools"], "pricing": zero},
                {"id": "lab/helper:free", "context_length": 131072, "supported_parameters": ["tools"], "pricing": zero}]})
        if self.path.endswith("/key"):
            return self._send(200, {"data": {"is_free_tier": True}})
        self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        SEEN.append({"model": body["model"]})
        if body["model"] == "lab/inkling-small:free":
            return self._send(403, {"error": {"message": "lab/inkling-small:free is only available on agentic harnesses. Try plugging it into a "
                                              "coding agent or productivity app listed on https://openrouter.ai/apps", "code": 403, "metadata": {}}})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "OK"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0}})


def test_a_free_model_closed_to_rooks_is_skipped_and_the_key_is_not_blamed(client, monkeypatch):
    from app import config, db, llm

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Router)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/api"
    monkeypatch.setattr(config, "OPENROUTER_URL", url)
    SEEN.clear()
    db.set_setting("ai_mode", "free")
    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenRouter', 'openai', ?, '', '', 1, ?)", url + "/v1", db.now())
    try:
        r = asyncio.run(llm.check_provider(pid))
        assert r["ok"] and r["recommended"] == "lab/helper:free", r
        assert [x["model"] for x in SEEN] == ["lab/inkling-small:free", "lab/helper:free"]  # tried the next model, did not stop
        first = r["tried"][0]
        assert first["refused"] and "rejected the API key" not in first["error"] and "nothing is wrong with your key" in first["error"]
        # remembered: never tried again, never picked automatically, shown as not available with the reason
        SEEN.clear()
        assert asyncio.run(llm.check_provider(pid))["ok"] and [x["model"] for x in SEEN] == ["lab/helper:free"]
        m = next(x for x in client.get("/api/models").json()["models"] if x["ref"] == f"{pid}/lab/inkling-small:free")
        assert m["allowed"] is False and "agentic harnesses" in m["blocked"]
        assert llm.effective_default()[0] != f"{pid}/lab/inkling-small:free"
    finally:
        db.run("DELETE FROM providers WHERE id = ?", pid)
        db.set_setting("ai_mode", "custom")
        srv.shutdown()


def test_a_real_bad_key_is_still_called_a_bad_key(client):
    from app import llm

    p = {"name": "OpenRouter", "kind": "openai", "base_url": "https://openrouter.ai/api/v1", "id": 0}
    assert "rejected the API key" in llm.explain(p, 401, '{"error":{"message":"No auth credentials found","code":401}}')
    assert "rejected the API key" in llm.explain(p, 403, '{"error":{"message":"Invalid API key"}}')
    assert "can't use tools" in llm.explain(p, 404, '{"error":{"message":"No endpoints found that support tool use"}}')
