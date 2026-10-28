"""Experimental: serve one AirLLM model on an OpenAI-compatible address, so ROOKS can connect to it like llama.cpp.

AirLLM (github.com/lyogavin/airllm, Apache-2.0) runs models too large for your GPU's memory by loading one layer at a
time from disk. That makes it slow: expect seconds to minutes per token on large models. This bridge is not part of
ROOKS and is never started by it. You install AirLLM and start the bridge yourself:

  python3 -m venv ~/airllm-venv && ~/airllm-venv/bin/pip install airllm==4.0.0
  ~/airllm-venv/bin/python bridge.py --model <hugging face id or folder> [--port 8090] [--device cuda]

Then in ROOKS: Settings, AI models, add an OpenAI-compatible server at http://127.0.0.1:8090/v1 (from Docker:
http://host.docker.internal:8090/v1) and run its capability tests.

What it does and does not do:
  * chat completions only; one request at a time (the model is not thread safe and a second request would double memory)
  * no tool calling: tool definitions in a request are ignored, so ROOKS's tool test fails, as it should
  * no streaming: a streaming request gets the whole answer as one chunk, so ROOKS's streaming test fails, as it should
  * listens on 127.0.0.1 unless you pass --host; it has no authentication
"""

from __future__ import annotations

import argparse
import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_NEW_TOKENS = 512
_lock = threading.Lock()
_model = None
_name = ""


def _prompt(messages: list[dict]) -> str:
    tok = _model.tokenizer
    msgs = [{"role": m.get("role", "user") if m.get("role") in ("system", "user", "assistant") else "user",
             "content": m["content"] if isinstance(m.get("content"), str) else json.dumps(m.get("content"))} for m in messages]
    if getattr(tok, "chat_template", None):
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    return "\n".join(f"{m['role']}: {m['content']}" for m in msgs) + "\nassistant:"


def generate(messages: list[dict], max_tokens: int) -> tuple[str, int, int]:
    with _lock:
        text = _prompt(messages)
        toks = _model.tokenizer([text], return_tensors="pt", truncation=True, max_length=4096)
        ids = toks["input_ids"]
        if _device != "cpu":
            ids = ids.to(_device)
        out = _model.generate(ids, max_new_tokens=max(1, min(max_tokens, MAX_NEW_TOKENS)), use_cache=True, return_dict_in_generate=True)
        new = out.sequences[0][ids.shape[1]:]
        return _model.tokenizer.decode(new, skip_special_tokens=True), int(ids.shape[1]), int(new.shape[0])


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _json(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            return self._json(200, {"object": "list", "data": [{"id": _name, "object": "model", "owned_by": "airllm"}]})
        self._json(404, {"error": {"message": "Not found"}})

    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            return self._json(404, {"error": {"message": "Only /v1/chat/completions is served."}})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            messages = body["messages"]
        except (ValueError, KeyError):
            return self._json(400, {"error": {"message": "Send JSON with a messages list."}})
        cap = body.get("max_tokens") or body.get("max_completion_tokens") or 256
        started = time.monotonic()
        try:
            text, pt, ct = generate(messages, int(cap))
        except Exception as e:  # noqa: BLE001 - report it to the caller instead of dropping the connection
            return self._json(500, {"error": {"message": f"AirLLM failed: {e.__class__.__name__}: {str(e)[:300]}"}})
        cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        usage = {"prompt_tokens": pt, "completion_tokens": ct, "total_tokens": pt + ct}
        if body.get("stream"):
            # Not real streaming: the whole answer is one chunk
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for chunk in ({"id": cid, "object": "chat.completion.chunk", "model": _name,
                           "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": "stop"}]},
                          {"id": cid, "object": "chat.completion.chunk", "model": _name, "choices": [], "usage": usage}):
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")
            return
        self._json(200, {"id": cid, "object": "chat.completion", "created": int(time.time()), "model": _name,
                         "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                         "usage": usage, "x_seconds": round(time.monotonic() - started, 1)})


def main() -> None:
    global _model, _name, _device
    ap = argparse.ArgumentParser(description="Serve an AirLLM model on an OpenAI-compatible address (experimental).")
    ap.add_argument("--model", required=True, help="Hugging Face model id or local folder")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--device", default="cpu", help="cpu, cuda or mps")
    a = ap.parse_args()
    from airllm import AutoModel

    _device = a.device
    print(f"Loading {a.model} with AirLLM (the first load splits the model into layers on disk; this can take long)...")
    try:
        _model = AutoModel.from_pretrained(a.model, device=a.device)
    except TypeError:
        _model = AutoModel.from_pretrained(a.model)
    _name = a.model.rstrip("/").split("/")[-1]
    print(f"Serving {_name} at http://{a.host}:{a.port}/v1 (one request at a time; no tools; no streaming).")
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


_device = "cpu"

if __name__ == "__main__":
    main()
