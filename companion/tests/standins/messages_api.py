"""Stand-in Messages API to drive the official Claude Code CLI in --bare mode with a fake key (no account)."""
import json, os, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
CMD = os.environ.get("CMD", "printf 'hello from claude\\n' > hello.txt && cat hello.txt")
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        self.send_response(404); self.end_headers()
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length") or 0)) or b"{}")
        tools = [t.get("name") for t in body.get("tools", [])]
        msgs = body.get("messages", [])
        last = msgs[-1] if msgs else {}
        tool_result = isinstance(last.get("content"), list) and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in last["content"])
        with open(sys.argv[2], "a") as f:
            f.write(json.dumps({"path": self.path, "tools": tools[:40], "stream": body.get("stream"), "key": (self.headers.get("x-api-key") or "")[:6],
                                "auth": bool(self.headers.get("authorization")), "tool_result": tool_result}) + "\n")
        if not self.path.startswith("/v1/messages") or "count_tokens" in self.path:
            out = json.dumps({"input_tokens": 10}).encode()
            self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out); return
        write_path = os.environ.get("WRITE_PATH", "")
        writer = "Write" if "Write" in tools else "Edit" if "Edit" in tools else ""
        use_write = bool(write_path) and bool(writer) and not tool_result
        use_tool = "Bash" in tools and not tool_result and not use_write
        ev = [("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": body.get("model"),
               "content": [], "stop_reason": None, "usage": {"input_tokens": 9, "output_tokens": 0}}})]
        if use_write:
            ev += [("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": writer, "input": {}}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                    "partial_json": json.dumps({"file_path": write_path, "content": "# Changelog\n\n- First release.\n"} if writer == "Write" else
                                               {"file_path": write_path, "old_string": "", "new_string": "# Changelog\n\n- First release.\n"})}}),
                   ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}})]
        elif use_tool:
            ev += [("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta",
                    "partial_json": json.dumps({"command": CMD, "description": "Write hello.txt"})}}),
                   ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}})]
        else:
            text = os.environ.get("FINAL", "I created hello.txt.") if tool_result else "OK"
            ev += [("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                   ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
                   ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                   ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 3}})]
        ev.append(("message_stop", {"type": "message_stop"}))
        if body.get("stream"):
            data = "".join(f"event: {n}\ndata: {json.dumps(d)}\n\n" for n, d in ev).encode()
            self.send_response(200); self.send_header("content-type", "text/event-stream"); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        content = [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": CMD}}] if use_tool else [{"type": "text", "text": "OK"}]
        out = json.dumps({"id": "msg_2", "type": "message", "role": "assistant", "model": body.get("model"), "content": content,
                          "stop_reason": "tool_use" if use_tool else "end_turn", "stop_sequence": None, "usage": {"input_tokens": 20, "output_tokens": 7}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(out))); self.end_headers(); self.wfile.write(out)
ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), H).serve_forever()
