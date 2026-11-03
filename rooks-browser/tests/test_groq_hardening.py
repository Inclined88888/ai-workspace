"""Groq-style failures: 429 rate limits with retry hints, 413 'request too large', 400 tool_use_failed."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from rooks_browser import llm


def serve(replies):
    """A fake /chat/completions that answers with the given (code, body, headers) in order; records request bodies."""
    seen = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            code, body, headers = replies[min(len(seen) - 1, len(replies) - 1)]
            data = json.dumps(body).encode()
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, seen


OK = (200, {"choices": [{"message": {"content": '{"thought": "t", "action": "done", "args": {"text": "hi"}}'}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3}}, {})


def chat(srv, **kw):
    m = llm.OpenAIChat("groq", "openai/gpt-oss-120b", f"http://127.0.0.1:{srv.server_address[1]}", "k", json_mode=False, **kw)
    m.sleep = lambda s: waits.append(s)
    return m


waits: list = []


def test_rate_limit_waits_and_retries():
    waits.clear()
    limited = (429, {"error": {"message": "Rate limit reached for model on tokens per minute (TPM): Limit 8000. Please try again in 1.5s."}}, {})
    srv, seen = serve([limited, (429, {"error": {"message": "slow down"}}, {"retry-after": "2"}), OK])
    try:
        r = chat(srv).complete("sys", "user")
        assert llm.parse_action(r.text)["action"] == "done"
        assert len(seen) == 3 and waits[0] >= 1.5 and waits[1] >= 2
    finally:
        srv.shutdown()


def test_daily_limit_gives_up_with_a_clear_message():
    waits.clear()
    daily = (429, {"error": {"message": "Rate limit reached on tokens per day (TPD). Please try again in 2h3m."}}, {})
    srv, _ = serve([daily])
    try:
        chat(srv).complete("sys", "user")
        raise AssertionError("should have raised")
    except llm.ProviderHTTPError as e:
        assert "rate limit" in str(e).lower() and waits == []
    finally:
        srv.shutdown()


def test_request_too_large_trims_the_page():
    srv, seen = serve([(413, {"error": {"message": "Request too large for model on tokens per minute (TPM): Limit 8000, Requested 9500"}}, {}), OK])
    try:
        big = "TASK: x\n\nCURRENT PAGE:\n" + "\n".join(f"[{i}] link number {i}" for i in range(3000)) + "\n\nReply with one JSON object"
        r = chat(srv).complete("sys", big)
        assert llm.parse_action(r.text)["action"] == "done"
        sent = seen[1]["messages"][1]["content"]
        assert len(sent) < len(big) * 0.7 and "trimmed" in sent and sent.endswith("Reply with one JSON object")
    finally:
        srv.shutdown()


def test_tool_use_failed_uses_the_failed_generation():
    bad = (400, {"error": {"code": "tool_use_failed", "message": "Failed to call a function",
                           "failed_generation": 'I will click. {"thought": "go", "action": "click", "args": {"id": 4}}'}}, {})
    srv, seen = serve([bad])
    try:
        r = chat(srv).complete("sys", "user")
        assert llm.parse_action(r.text) == {"thought": "go", "action": "click", "args": {"id": 4}} or llm.parse_action(r.text)["action"] == "click"
        assert len(seen) == 1
    finally:
        srv.shutdown()


def test_groq_defaults(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    m = llm.make("groq", None)
    assert m.max_prompt_chars > 0 and m.extra_body.get("reasoning_effort") == "low" and m.extra_body.get("max_completion_tokens")
    assert len(llm.fit_prompt("a\n" * 20000, 14000)) <= 14000
