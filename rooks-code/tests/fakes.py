"""A scripted fake model server speaking the OpenAI Chat Completions or Anthropic Messages protocol over real HTTP."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def openai_reply(text="", calls=(), tokens=(100, 20)):
    msg = {"role": "assistant", "content": text or None}
    if calls:
        msg["tool_calls"] = [{"id": f"call_{i}", "type": "function",
                              "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                             for i, (n, a) in enumerate(calls)]
    return {"choices": [{"message": msg, "finish_reason": "tool_calls" if calls else "stop"}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}}


def anthropic_reply(text="", calls=(), tokens=(100, 20)):
    content = ([{"type": "text", "text": text}] if text else []) + [
        {"type": "tool_use", "id": f"toolu_{i}", "name": n, "input": a} for i, (n, a) in enumerate(calls)]
    return {"content": content, "stop_reason": "tool_use" if calls else "end_turn",
            "usage": {"input_tokens": tokens[0], "output_tokens": tokens[1]}}


class FakeModel:
    def __init__(self, script):
        self.script, self.requests = list(script), []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                reply = fake.script.pop(0) if fake.script else openai_reply("(script exhausted)")
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
