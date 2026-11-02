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
        self.script, self.requests, self.models = list(script), [], ["model-a", "model-b"]
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                reply = fake.script.pop(0) if fake.script else openai_reply("(script exhausted)")
                if "__status__" in reply:  # {"__status__": 503}: a provider error, e.g. overloaded
                    data = json.dumps({"error": {"message": "Service temporarily overloaded"}}).encode()
                    self.send_response(reply["__status__"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if body.get("stream"):
                    events = anthropic_events(reply) if "content" in reply and "choices" not in reply else openai_chunks(reply)
                    data = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode() + b"data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):  # the model list, for /model
                data = json.dumps({"data": [{"id": m} for m in fake.models]}).encode()
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


def openai_chunks(reply):
    """A whole Chat Completions reply as the stream a server sends: text in two pieces, each tool call's arguments split."""
    msg, usage = reply["choices"][0]["message"], reply.get("usage") or {}
    text = msg.get("content") or ""
    out = [{"choices": [{"index": 0, "delta": {"content": part}}]} for part in (text[: len(text) // 2], text[len(text) // 2:]) if part]
    for i, c in enumerate(msg.get("tool_calls") or []):
        args = c["function"]["arguments"]
        out.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "id": c["id"], "type": "function",
                                                                        "function": {"name": c["function"]["name"], "arguments": args[:5]}}]}}]})
        out.append({"choices": [{"index": 0, "delta": {"tool_calls": [{"index": i, "function": {"arguments": args[5:]}}]}}]})
    out.append({"choices": [], "usage": usage})
    return out


def anthropic_events(reply):
    u = reply.get("usage") or {}
    out = [{"type": "message_start", "message": {"usage": {"input_tokens": u.get("input_tokens", 0)}}}]
    for i, b in enumerate(reply["content"]):
        if b["type"] == "text":
            out += [{"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}},
                    {"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": b["text"]}}]
        else:
            raw = json.dumps(b["input"])
            out += [{"type": "content_block_start", "index": i, "content_block": {"type": "tool_use", "id": b["id"], "name": b["name"], "input": {}}},
                    {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": raw[:4]}},
                    {"type": "content_block_delta", "index": i, "delta": {"type": "input_json_delta", "partial_json": raw[4:]}}]
    out.append({"type": "message_delta", "usage": {"output_tokens": u.get("output_tokens", 0)}})
    return out
