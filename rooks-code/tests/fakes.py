"""A scripted fake model server speaking the OpenAI Chat Completions or Anthropic Messages protocol over real HTTP."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeModels:
    """A fake GET /models server for list_models tests."""

    def __init__(self, ids=(), fail=False):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if fail:
                    self.send_response(401)
                    self.end_headers()
                    return
                data = json.dumps({"object": "list", "data": [{"id": i, "object": "model"} for i in ids]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                self.send_response(404)
                self.end_headers()

            def log_message(self, *a, **kw):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


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


# --- SSE scripts --------------------------------------------------------------------------------------------------
# Each entry is one SSE frame's data payload (a dict) or the string "[DONE]". The server streams them with a short
# delay so the client really reads a stream, not one buffered blob.

def openai_stream(text="", calls=(), tokens=(100, 20), reasoning=""):
    frames = []
    if reasoning:
        frames.append({"choices": [{"delta": {"reasoning_content": reasoning}}]})
    for piece in ([text[i:i + 3] for i in range(0, len(text), 3)] if text else []):
        frames.append({"choices": [{"delta": {"content": piece}}]})
    for i, (n, a) in enumerate(calls):
        raw = a if isinstance(a, str) else json.dumps(a)
        frames.append({"choices": [{"delta": {"tool_calls": [{"index": i, "id": f"call_{i}", "type": "function",
                                                              "function": {"name": n, "arguments": ""}}]}}]})
        mid = len(raw) // 2
        for piece in (raw[:mid], raw[mid:]):
            frames.append({"choices": [{"delta": {"tool_calls": [{"index": i, "function": {"arguments": piece}}]}}]})
    frames.append({"choices": [], "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]}})
    frames.append("[DONE]")
    return frames


def anthropic_stream(text="", calls=(), tokens=(100, 20), thinking=""):
    frames = [{"type": "message_start", "message": {"usage": {"input_tokens": tokens[0]}}}]
    i = 0
    if thinking:
        frames += [{"type": "content_block_start", "index": i, "content_block": {"type": "thinking"}},
                   {"type": "content_block_delta", "index": i, "delta": {"type": "thinking_delta", "thinking": thinking}},
                   {"type": "content_block_stop", "index": i}]
        i += 1
    if text:
        frames.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
        for piece in ([text[i:i + 3] for i in range(0, len(text), 3)] if text else []):
            frames.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": piece}})
        frames.append({"type": "content_block_stop", "index": i})
        i += 1
    for n, a in calls:
        raw = json.dumps(a)
        frames.append({"type": "content_block_start", "index": i,
                       "content_block": {"type": "tool_use", "id": f"toolu_{i}", "name": n}})
        mid = len(raw) // 2
        for piece in (raw[:mid], raw[mid:]):
            frames.append({"type": "content_block_delta", "index": i,
                           "delta": {"type": "input_json_delta", "partial_json": piece}})
        frames.append({"type": "content_block_stop", "index": i})
        i += 1
    frames += [{"type": "message_delta", "usage": {"output_tokens": tokens[1]}},
               {"type": "message_stop"}]
    return frames


class FakeModel:
    def __init__(self, script, refuse_stream=False):
        self.script, self.requests, self.models = list(script), [], ["model-a", "model-b"]
        self.refuse_stream = refuse_stream
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                fake.requests.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                if body.get("stream") and fake.refuse_stream:
                    # a server that does not speak SSE answers 400 and the client must fall back to a plain call
                    data = json.dumps({"error": {"message": "stream is not supported"}}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                reply = fake.script.pop(0) if fake.script else openai_reply("(script exhausted)")
                if isinstance(reply, dict) and "__status__" in reply:  # {"__status__": 503}: a provider error, e.g. overloaded
                    data = json.dumps({"error": {"message": "Service temporarily overloaded"}}).encode()
                    self.send_response(reply["__status__"])
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
                if isinstance(reply, dict) and body.get("stream"):  # a whole reply asked for as a stream: send it as chunks
                    reply = (anthropic_events(reply) if "content" in reply and "choices" not in reply else openai_chunks(reply)) + ["[DONE]"]
                if isinstance(reply, list):  # an SSE script: stream the frames
                    import time as _time
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    for frame in reply:
                        data = frame if isinstance(frame, str) else json.dumps(frame)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                        _time.sleep(0.005)
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

            def log_message(self, *a, **kw):
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
