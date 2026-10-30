"""Rooks Code finds the open AI Workspace app through app.json and talks to its /v1 bridge (smart router)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from rooks_code import config, providers


@pytest.fixture
def fake_app():
    seen: list[dict] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"ok": True})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append({"path": self.path, "headers": dict(self.headers), "body": json.loads(self.rfile.read(n))})
            self._send({"choices": [{"message": {"role": "assistant", "content": "ready"}}],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1}, "router": {"provider": "Groq"}})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", seen
    srv.shutdown()


def _isolate(monkeypatch, tmp_path):
    for k in ("ROOKS_PROVIDER", "ROOKS_MODEL", "ROOKS_BASE_URL", "WORKBENCH_URL", "WORKBENCH_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    link = tmp_path / "app.json"
    monkeypatch.setenv("AI_WORKSPACE_APP_FILE", str(link))
    return link


def test_open_app_is_the_default_provider(fake_app, tmp_path, monkeypatch):
    url, seen = fake_app
    link = _isolate(monkeypatch, tmp_path)
    link.write_text(json.dumps({"url": url, "key": "k" * 32, "pid": 1}))
    s = config.resolve({}, tmp_path, {})
    assert (s.provider, s.base_url, s.model) == ("workbench", f"{url}/v1", "default")
    assert s.api_key == "k" * 32
    r = providers.make(s).complete("sys", [{"role": "user", "content": "ping"}], [])
    assert r.text == "ready"
    h = {k.lower(): v for k, v in seen[0]["headers"].items()}
    assert seen[0]["path"] == "/v1/chat/completions"
    assert h["x-workbench-channel"] == "rooks-code" and h["authorization"] == "Bearer " + "k" * 32
    assert seen[0]["body"]["model"] == "default"


def test_no_app_falls_back_to_ollama(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert config.resolve({}, tmp_path, {}).provider == "ollama"


def test_stale_app_file_is_ignored(tmp_path, monkeypatch):
    link = _isolate(monkeypatch, tmp_path)
    link.write_text(json.dumps({"url": "http://127.0.0.1:9", "key": "x"}))  # nothing listens on port 9
    assert config.resolve({}, tmp_path, {}).provider == "ollama"
    with pytest.raises(SystemExit):
        config.resolve({"provider": "workbench"}, tmp_path, {})


def test_explicit_workbench_url(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("WORKBENCH_URL", "http://localhost:3100")
    s = config.resolve({}, tmp_path, {})
    assert (s.provider, s.base_url) == ("workbench", "http://localhost:3100/v1")


def test_free_cloud_presets(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    assert config.resolve({"provider": "groq"}, tmp_path, {}).key_env == "GROQ_API_KEY"
    assert "generativelanguage" in config.resolve({"provider": "gemini"}, tmp_path, {}).base_url
