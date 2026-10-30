"""Rooks Browser finds the open AI Workspace app through app.json and uses its /v1 bridge (smart router)."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rooks_browser import llm


def test_app_link_provider(tmp_path, monkeypatch):
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"ok": True})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            seen.append((dict(self.headers), json.loads(self.rfile.read(n))))
            self._send({"choices": [{"message": {"content": '{"action": "done", "answer": "ok"}'}}]})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        for k in ("WORKBENCH_URL", "WORKBENCH_API_KEY", "BROWSER_API_KEY", "ROOKS_BROWSER_PROVIDER", "OPENAI_API_KEY",
                  "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
            monkeypatch.delenv(k, raising=False)
        link = tmp_path / "app.json"
        monkeypatch.setenv("AI_WORKSPACE_APP_FILE", str(link))
        assert llm.resolve(None) in ("ollama",)  # no app yet
        link.write_text(json.dumps({"url": f"http://127.0.0.1:{srv.server_address[1]}", "key": "s" * 32}))
        assert llm.resolve(None) == "workbench"
        m = llm.make("workbench")
        r = m.complete("sys", "user")
        h = {k.lower(): v for k, v in seen[0][0].items()}
        assert h["x-workbench-channel"] == "rooks-browser" and h["authorization"] == "Bearer " + "s" * 32
        assert seen[0][1]["model"] == "default" and "done" in r.text
    finally:
        srv.shutdown()


def _empty_app():
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"object": "list", "data": []} if self.path.startswith("/v1/models") else {"ok": True}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            body = b'{"detail":"No model provider configured. Add an API key in Settings."}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _clear_keys(monkeypatch):
    for p in llm.PROVIDERS.values():
        for k in p.key_env:
            monkeypatch.delenv(k, raising=False)
    monkeypatch.delenv("ROOKS_BROWSER_PROVIDER", raising=False)


def test_app_without_a_model_falls_through_to_a_groq_or_gemini_key(tmp_path, monkeypatch):
    srv = _empty_app()
    try:
        _clear_keys(monkeypatch)
        f = tmp_path / "app.json"
        f.write_text(json.dumps({"url": f"http://127.0.0.1:{srv.server_address[1]}", "key": "k"}))
        monkeypatch.setenv("AI_WORKSPACE_APP_FILE", str(f))
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        assert llm.resolve(None) == "gemini"
        m = llm.make("gemini")
        assert m.provider == "gemini" and "generativelanguage" in m.base_url
        monkeypatch.delenv("GEMINI_API_KEY")
        monkeypatch.setenv("GROQ_API_KEY", "test")
        assert llm.resolve(None) == "groq"
        monkeypatch.delenv("GROQ_API_KEY")
        monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")
        try:
            llm.resolve(None)
            raise AssertionError("expected the setup help")
        except ValueError as e:
            assert "ai-workspace" in str(e) and "GROQ_API_KEY" in str(e)
    finally:
        srv.shutdown()


def test_the_apps_no_model_error_is_friendly(monkeypatch):
    srv = _empty_app()
    try:
        m = llm.make("workbench", base_url=f"http://127.0.0.1:{srv.server_address[1]}", api_key="k")
        try:
            m.complete("sys", "hi")
            raise AssertionError("expected an error")
        except RuntimeError as e:
            assert "Free cloud" in str(e) and "HTTP 400" not in str(e)
    finally:
        srv.shutdown()
