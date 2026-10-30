"""A minimal MCP client for stdio servers (vendored from rooks_code/mcp.py): JSON-RPC 2.0, one message per line,
protocol 2025-06-18 with fallback to whatever version the server answers. Rooks Desk uses it to drive other MCP servers
(an AutoCAD server, a filesystem server, ...); their tools are offered to the model as mcp__<server>__<tool>."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading

PROTOCOL = "2025-06-18"


class MCPError(Exception):
    pass


class StdioServer:
    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict | None = None,
                 cwd: str | None = None, timeout: float = 60):
        self.name, self.timeout = name, timeout
        exe = shutil.which(command) or command  # Windows: finds npx.cmd and friends
        self.proc = subprocess.Popen([exe, *(args or [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, cwd=cwd, env={**os.environ, **(env or {})},
                                     text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self.last_images: list[str] = []
        self._replies: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                           "clientInfo": {"name": "rooks-desk", "version": "0.1.0"}})
        self.server_info = info.get("serverInfo", {})
        self.notify("notifications/initialized")
        self.tools = self.request("tools/list", {}).get("tools", [])

    def _read(self) -> None:
        assert self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue  # servers that print banners to stdout
            if "id" in msg and ("result" in msg or "error" in msg):
                self._replies.put(msg)
            elif "id" in msg and "method" in msg:  # server -> client request (ping, roots): answer minimally
                result = {"roots": []} if msg["method"] == "roots/list" else {}
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
        self._replies.put(None)

    def _drain(self) -> None:
        assert self.proc.stderr
        for line in self.proc.stderr:
            self.stderr = (self.stderr + [line.rstrip()])[-20:]

    def _send(self, msg: dict) -> None:
        assert self.proc.stdin
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            raise MCPError(f"MCP server {self.name} exited: {' | '.join(self.stderr[-3:])}") from None

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    def request(self, method: str, params: dict) -> dict:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        while True:
            try:
                msg = self._replies.get(timeout=self.timeout)
            except queue.Empty:
                raise MCPError(f"MCP server {self.name} did not answer {method} within {self.timeout:.0f}s") from None
            if msg is None:
                self._replies.put(None)
                raise MCPError(f"MCP server {self.name} exited: {' | '.join(self.stderr[-3:])}")
            if msg.get("id") != rid:
                continue
            if "error" in msg:
                raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
            return msg.get("result") or {}

    def call(self, tool: str, arguments: dict) -> tuple[bool, str]:
        """Returns (ok, text). Non-text content is summarised by type."""
        r = self.request("tools/call", {"name": tool, "arguments": arguments})
        parts = []
        self.last_images = []
        for c in r.get("content", []):
            if c.get("type") == "image" and c.get("data"):
                self.last_images.append(c["data"])  # e.g. an AutoCAD viewport capture; the agent can save it
            parts.append(c.get("text", "") if c.get("type") == "text" else f"[{c.get('type')} content]")
        if not parts and r.get("structuredContent") is not None:
            parts.append(json.dumps(r["structuredContent"]))
        return not r.get("isError", False), "\n".join(parts)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()


class MCPManager:
    def __init__(self, servers: dict[str, dict], cwd: str, warn=lambda m: print(m, file=sys.stderr)):
        self.servers: dict[str, StdioServer] = {}
        self.trusted: set[str] = set()
        for name, spec in servers.items():
            if spec.get("disabled") or spec.get("enabled") is False:
                continue
            if spec.get("url") and not spec.get("command"):
                warn(f"rooks-desk: MCP server {name}: only stdio servers are supported so far (skipped)")
                continue
            try:
                self.servers[name] = StdioServer(name, spec["command"], spec.get("args", []), spec.get("env"), cwd,
                                                 float(spec.get("timeout", 60)))
                if spec.get("trusted"):
                    self.trusted.add(name)
            except (OSError, MCPError, KeyError) as e:
                warn(f"rooks-desk: MCP server {name} did not start: {e}")

    def tool_specs(self) -> list[dict]:
        specs = []
        for sname, s in self.servers.items():
            for t in s.tools:
                specs.append({"name": f"mcp__{sname}__{t['name']}"[:64],
                              "description": f"[MCP {sname}] {t.get('description', '')}"[:1024],
                              "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}})
        return specs

    def resolve(self, name: str) -> tuple[StdioServer, str] | None:
        if not name.startswith("mcp__"):
            return None
        for sname, s in self.servers.items():
            prefix = f"mcp__{sname}__"
            if name.startswith(prefix):
                tool = name[len(prefix):]
                for t in s.tools:
                    if f"{prefix}{t['name']}"[:64] == name:
                        return s, t["name"]
                return s, tool
        return None

    def close(self) -> None:
        for s in self.servers.values():
            s.close()
