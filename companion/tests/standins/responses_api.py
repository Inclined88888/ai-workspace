"""Stand-in for an OpenAI Responses API endpoint, to drive the official Codex CLI without any account."""
import json, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = sys.argv[2] if len(sys.argv) > 2 else "/dev/null"
turn = {"n": 0}

def sse(ev, data):
    return f"event: {ev}\ndata: {json.dumps(data)}\n\n".encode()

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps({"data": [{"id": "stand-in", "object": "model"}], "models": []}).encode())
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        with open(LOG, "a") as f:
            f.write(json.dumps({"path": self.path, "tools": [t.get("name") or t.get("type") for t in body.get("tools", [])],
                                "input_types": [i.get("type") for i in body.get("input", [])]}) + "\n")
        has_output = any(i.get("type") == "function_call_output" for i in body.get("input", []))
        tools = [t.get("name") for t in body.get("tools", [])]
        self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
        rid = "resp_1"
        out = []
        self.wfile.write(sse("response.created", {"type": "response.created", "response": {"id": rid}}))
        if not has_output and ("exec_command" in tools or "shell" in tools):
            name = "exec_command" if "exec_command" in tools else "shell"
            args = {"cmd": __import__("os").environ.get("CMD", "printf 'hello from codex\\n' > hello.txt && cat hello.txt")} if name == "exec_command" else \
                   {"command": ["bash", "-lc", "printf 'hello from codex\\n' > hello.txt && cat hello.txt"]}
            item = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": name, "arguments": json.dumps(args), "status": "completed"}
            self.wfile.write(sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0, "item": item}))
            self.wfile.write(sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}))
        else:
            text = __import__("os").environ.get("FINAL", "I created hello.txt containing 'hello from codex'.")
            item = {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}
            self.wfile.write(sse("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                 "item": {**item, "content": [], "status": "in_progress"}}))
            self.wfile.write(sse("response.output_text.delta", {"type": "response.output_text.delta", "item_id": "msg_1", "output_index": 0, "content_index": 0, "delta": text}))
            self.wfile.write(sse("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": item}))
        self.wfile.write(sse("response.completed", {"type": "response.completed", "response": {"id": rid, "usage": {
            "input_tokens": 10, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 5, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 15}}}))
        self.wfile.flush()

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
