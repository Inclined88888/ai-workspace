"""The MCP server, as a real subprocess on stdio, the way Claude Code, Codex and Rooks Code start it."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Client:
    def __init__(self, env, caps=None):
        self.p = subprocess.Popen([sys.executable, "-m", "rooks_browser", "mcp"], cwd=ROOT, env=env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, bufsize=1)
        self.n = 0
        self.elicitations = []
        r = self.req("initialize", {"protocolVersion": "2025-06-18", "capabilities": caps or {}, "clientInfo": {"name": "test"}})
        assert r["serverInfo"]["name"] == "rooks-browser"
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, m):
        self.p.stdin.write(json.dumps(m) + "\n")
        self.p.stdin.flush()

    def req(self, method, params=None, answer=None):
        self.n += 1
        self.send({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}})
        while True:
            m = json.loads(self.p.stdout.readline())
            if m.get("method") == "elicitation/create":  # the server asks us (the user) something
                self.elicitations.append(m["params"])
                self.send({"jsonrpc": "2.0", "id": m["id"], "result": answer(m["params"])})
                continue
            if m.get("id") == self.n:
                assert "error" not in m, m
                return m["result"]

    def close(self):
        self.p.stdin.close()
        self.p.wait(10)


def env_for(tmp_path):
    e = dict(os.environ)
    e.update(ROOKS_BROWSER_HOME=str(tmp_path / "h"), ROOKS_BROWSER_NO_PROBE="1", PYTHONPATH=str(ROOT))
    e.pop("SEARXNG_URL", None)
    return e


def test_tools_list_and_read_page(site, tmp_path):
    c = Client(env_for(tmp_path))
    try:
        names = [t["name"] for t in c.req("tools/list")["tools"]]
        assert names == ["browse_web", "read_page", "web_search"]
        r = c.req("tools/call", {"name": "read_page", "arguments": {"url": f"{site}/widget.html"}})
        assert not r["isError"]
        data = json.loads(r["content"][0]["text"])
        assert "Price: $12.50" in data["text"] and data["receipt"].endswith(".jsonl")
        bad = c.req("tools/call", {"name": "read_page", "arguments": {"url": "notascheme://x"}})
        assert bad["isError"]
    finally:
        c.close()


def test_browse_web_asks_through_elicitation(site, tmp_path):
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"action": "open", "args": {"url": f"{site}/contact.html"}},
                                  {"action": "type", "args": {"id": 6, "text": "hi"}},
                                  {"action": "click", "args": {"id": 7}},
                                  {"action": "done", "args": {"answer": "sent", "success": True}}]))
    c = Client(env_for(tmp_path), caps={"elicitation": {}})
    try:
        r = c.req("tools/call", {"name": "browse_web", "arguments": {"goal": "send hi", "provider": "scripted", "model": str(script)}},
                  answer=lambda p: {"action": "accept", "content": {"approve": True}})
        data = json.loads(r["content"][0]["text"])
        assert len(c.elicitations) == 1 and "send a message" in c.elicitations[0]["message"].lower()
        assert data["success"] and any(u.endswith("/sent.html") for u in data["urls"])
    finally:
        c.close()


def test_browse_web_refuses_sensitive_without_elicitation(site, tmp_path):
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"action": "open", "args": {"url": f"{site}/widget.html"}}, {"action": "click", "args": {"id": 7}},
                                  {"action": "done", "args": {"answer": "no", "success": False}}]))
    c = Client(env_for(tmp_path))
    try:
        r = c.req("tools/call", {"name": "browse_web", "arguments": {"goal": "buy", "provider": "scripted", "model": str(script)}})
        data = json.loads(r["content"][0]["text"])
        assert data["denied"] and "cannot ask the user" in data["note"]
    finally:
        c.close()


def test_calling_agent_cannot_self_approve(site, tmp_path):
    script = tmp_path / "s.json"
    script.write_text(json.dumps([{"action": "open", "args": {"url": f"{site}/widget.html"}}, {"action": "click", "args": {"id": 7}},
                                  {"action": "done", "args": {"answer": "no", "success": False}}]))
    c = Client(env_for(tmp_path))
    try:
        schema = c.req("tools/list")["tools"][0]["inputSchema"]["properties"]
        assert "approve" not in schema
        r = c.req("tools/call", {"name": "browse_web", "arguments": {"goal": "buy", "provider": "scripted", "model": str(script),
                                                                     "approve": ["purchase"]}})
        data = json.loads(r["content"][0]["text"])
        assert data["denied"] and not any("order.html" in u for u in data["urls"])
    finally:
        c.close()
