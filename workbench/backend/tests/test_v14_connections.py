"""v14: every connection shows one honest state; the AirLLM bridge is experimental wherever it appears and is never
picked automatically, even when it answers a connection check (it ignores tools and still replies)."""

import asyncio
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import HTTPException


@pytest.fixture(autouse=True)
def checked(client):
    from app import db

    key = f"provider_check:{client.pid}"
    before = db.get_setting(key)
    db.set_setting(key, json.dumps({"ok": True, "recommended": "fake-model", "checked_at": db.now(), "error": ""}))
    yield
    db.set_setting(key, before or "")


class _Bridge(BaseHTTPRequestHandler):
    """Answers like addons/airllm/bridge.py: one model owned_by airllm, plain answers whatever tools are sent."""

    def log_message(self, *a):
        pass

    def _send(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(data)))
        self.end_headers(); self.wfile.write(data)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "big/model-70b", "object": "model", "owned_by": "airllm"}]})

    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"]))
        self._send({"choices": [{"message": {"role": "assistant", "content": "OK"}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


@pytest.fixture
def bridge():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Bridge)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}/v1"
    srv.shutdown()


def _entry(d, eid):
    return next(e for e in d["entries"] if e["id"] == eid)


def test_each_connection_has_one_honest_state(client):
    from app import db

    off = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('OpenAI', 'openai', 'https://api.openai.com/v1', '', 'gpt-x', 0, ?)", db.now())
    new = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('Gateway', 'openai', 'https://llm.example.com/v1', '', 'm', 1, ?)", db.now())
    try:
        d = client.get("/api/connection-directory").json()
        assert set(d["states"]) == {"verified", "untested", "available", "experimental", "problem", "off"}
        assert _entry(d, f"provider:{client.pid}")["state"] == "verified"  # answered a request with a tool attached
        assert _entry(d, f"provider:{off}")["state"] == "off"
        assert _entry(d, f"provider:{new}")["state"] == "untested" and _entry(d, f"provider:{new}")["state_label"] == "Connected, not tested"
        assert _entry(d, "local:ollama")["state"] == "available"
        assert _entry(d, "local:airllm")["state"] == "experimental" and not _entry(d, "local:airllm")["powers_chat"]
        assert _entry(d, "coding:codex")["state"] == "available"  # no paired computer: not connected, and never "verified"
        assert all(e["state"] in d["states"] and e["state_label"] for e in d["entries"])
        db.set_setting(f"provider_check:{new}", json.dumps({"ok": False, "error": "The server refused the key."}))
        assert _entry(client.get("/api/connection-directory").json(), f"provider:{new}")["state"] == "problem"
    finally:
        db.run("DELETE FROM providers WHERE id IN (?, ?)", off, new)
        db.run("DELETE FROM settings WHERE key = ?", f"provider_check:{new}")


def test_the_airllm_bridge_is_experimental_and_never_picked_automatically(client, bridge):
    from app import db, llm

    pid = db.run("INSERT INTO providers(name, kind, base_url, api_key_enc, models, enabled, created_at) VALUES "
                 "('My local server', 'openai', ?, '', '', 1, ?)", bridge, db.now())
    suite_check, suite_default = db.get_setting(f"provider_check:{client.pid}"), db.get_setting("default_model")
    try:
        r = asyncio.run(llm.check_provider(pid))
        assert r["ok"], r  # the bridge answers a request with a tool attached: the check alone cannot tell
        assert llm.experimental(db.one("SELECT * FROM providers WHERE id = ?", pid))  # its model list says owned_by airllm
        ref = f"{pid}/big/model-70b"
        db.set_setting(f"provider_check:{client.pid}", json.dumps({"ok": False, "error": "down"}))  # nothing else working
        assert llm.recommended_ref(db.one("SELECT * FROM providers WHERE id = ?", pid)) is None
        try:
            assert llm.effective_default()[0] != ref  # it is not used as the automatic model ...
        except HTTPException:
            pass  # ... even when nothing else works
        assert ref not in llm.fallback_refs("0/x")  # ... nor as a backup
        e = _entry(client.get("/api/connection-directory").json(), f"provider:{pid}")
        assert e["state"] == "experimental" and e["experimental"] and not e["powers_chat"] and "Not for everyday use" in e["limits"]
        m = next(x for x in client.get("/api/models").json()["models"] if x["ref"] == ref)
        assert m["status"] == "experimental" and not m["verified"]
        db.set_setting("default_model", ref)
        assert llm.effective_default() == (ref, "chosen")  # you can still choose it yourself
    finally:
        db.set_setting("default_model", suite_default or "")
        db.set_setting(f"provider_check:{client.pid}", suite_check or "")
        db.run("DELETE FROM providers WHERE id = ?", pid)
        db.run("DELETE FROM settings WHERE key IN (?, ?)", f"provider_check:{pid}", f"provider_experimental:{pid}")
