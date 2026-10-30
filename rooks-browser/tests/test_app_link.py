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
