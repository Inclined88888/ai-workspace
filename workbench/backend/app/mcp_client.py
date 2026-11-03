"""Workbench as an MCP client: connect to configured MCP servers (stdio, Streamable HTTP, legacy SSE) and expose
their tools to the assistant as mcp__<server>__<tool>.

Ported from rooks-code/rooks_code/mcp.py (stdio JSON-RPC 2.0, one message per line, MCP version 2025-06-18 with
fallback to whatever version the server answers) and extended with the two remote transports:

* streamable_http: POST a JSON-RPC message, answer is JSON or an SSE stream (event: message). A response carrying
  an `mcp-session-id` header pins the session, and notifications may be picked up with a listening GET.
* sse (legacy, MCP version 2024-11-05): GET opens an event stream whose first `endpoint` event gives the POST URL.

Remote transports use httpx, so every connection is a row in the egress log (egress.py wraps httpx once at
startup). Stdio servers spawn a local process: nothing leaves the machine. Secrets (env values, header values)
are stored encrypted like provider keys (security.store_connection_secret / keychain when the desktop app
enabled it) and never leave the API: listings show "configured: yes" instead of a value. Tool output is
untrusted by definition (assistant.py marks it tainting).

Servers are configured in the `mcp_servers` table (db.py); the registry is process-local, started lazily on
first use, and `notify_change()` is called by the API after any add/remove/enable so the next tool list is fresh.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
from urllib.parse import urljoin

import httpx

from . import db, security

PROTOCOL = "2025-06-18"
CLIENT = {"name": "ai-workspace-workbench", "version": "0.1.0"}
log = logging.getLogger("workbench.mcp_client")


class MCPError(Exception):
    pass


def _err_text(e: Exception) -> str:
    """One line for the `last_error` column, never echoing a configured secret back (R7)."""
    text = str(e) or e.__class__.__name__
    from .redact import redact

    return redact(text)[:300]


def _secret_keys(spec: dict) -> list[str]:
    return [k for k in spec.get("env", {}) if spec["env"][k] == ""] + \
           [k for k in spec.get("headers", {}) if spec["headers"][k] == ""]


class StdioServer:
    """JSON-RPC 2.0 over the child's stdin/stdout, one message per line (rooks-code port)."""

    def __init__(self, name: str, command: str, args: list[str] | None = None, env: dict | None = None,
                 timeout: float = 60):
        self.name, self.timeout = name, timeout
        exe = shutil.which(command)
        if not exe:
            raise MCPError(f"command not found: {command}")
        self.proc = subprocess.Popen([exe, *(args or [])], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, env={**os.environ, **(env or {})},
                                     text=True, encoding="utf-8", bufsize=1)
        self._id = 0
        self._replies: queue.Queue = queue.Queue()
        self.stderr: list[str] = []
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._drain, daemon=True).start()
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT})
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
        r = self.request("tools/call", {"name": tool, "arguments": arguments})
        return not r.get("isError", False), content_text(r)

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            self.proc.kill()


def content_text(r: dict) -> str:
    """Flatten an MCP result to text for the model. Non-text content is summarised by type."""
    parts = []
    for c in r.get("content", []):
        parts.append(c.get("text", "") if c.get("type") == "text" else f"[{c.get('type')} content]")
    if not parts and r.get("structuredContent") is not None:
        parts.append(json.dumps(r["structuredContent"]))
    return "\n".join(parts)


def _sse_messages(resp: httpx.Response) -> list[dict]:
    """Parse one SSE body into its JSON-RPC messages (`event: message` or a bare data line)."""
    out, event, data = [], "", []
    for line in resp.text.splitlines():
        if not line:
            if data and event in ("", "message"):
                try:
                    out.append(json.loads("\n".join(data)))
                except ValueError:
                    pass
            event, data = "", []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data and event in ("", "message"):
        try:
            out.append(json.loads("\n".join(data)))
        except ValueError:
            pass
    return out


class HttpServer:
    """Streamable HTTP (2025-03-26+): POST per message; the reply is JSON or an SSE stream. One request at a
    time per server (a lock), which keeps replies matched to requests without pending-id bookkeeping."""

    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 60):
        self.name, self.url, self.timeout = name, url, timeout
        self._id = 0
        self._lock = threading.Lock()
        self.session_id = ""
        self.client = httpx.Client(timeout=timeout)
        self.headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                        **(headers or {})}
        info = self.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT},
                            session=True)
        self.server_info = info.get("serverInfo", {})
        self.notify("notifications/initialized")
        self.tools = self.request("tools/list", {}).get("tools", [])

    def _post(self, msg: dict, session: bool = False) -> list[dict]:
        h = dict(self.headers)
        if self.session_id:
            h["mcp-session-id"] = self.session_id
        r = self.client.post(self.url, json=msg, headers=h)
        if session and r.headers.get("mcp-session-id"):
            self.session_id = r.headers["mcp-session-id"]
        if r.status_code == 202 or not r.content:
            return []
        if r.status_code >= 400:
            raise MCPError(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
        if r.headers.get("content-type", "").startswith("text/event-stream"):
            return _sse_messages(r)
        return [r.json()]

    def request(self, method: str, params: dict, session: bool = False) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            for msg in self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}, session=session):
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result") or {}
        raise MCPError(f"MCP server {self.name} returned no reply to {method}")

    def notify(self, method: str, params: dict | None = None) -> None:
        self._post({"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {})})

    def call(self, tool: str, arguments: dict) -> tuple[bool, str]:
        r = self.request("tools/call", {"name": tool, "arguments": arguments})
        return not r.get("isError", False), content_text(r)

    def close(self) -> None:
        self.client.close()


class SseServer(HttpServer):
    """Legacy SSE transport (MCP version 2024-11-05): GET opens the stream; the `endpoint` event names the POST URL.
    The stream runs on its own client on a thread and reconnects after a drop (a proxy or the server itself may
    close an idle stream; answers to outstanding POSTs then just arrive a moment later)."""

    def __init__(self, name: str, url: str, headers: dict | None = None, timeout: float = 60):
        self.name, self.url, self.timeout = name, url, timeout
        self._id = 0
        self._lock = threading.Lock()
        self.client = httpx.Client(timeout=timeout)  # POSTs only; the stream has its own client per attempt
        self.headers = {"Accept": "text/event-stream", **(headers or {})}
        self._replies: queue.Queue = queue.Queue()
        self._endpoint = ""
        self._died = threading.Event()
        self._closed = False
        threading.Thread(target=self._listen_loop, daemon=True).start()
        deadline = time.monotonic() + timeout
        while not self._endpoint:
            if self._closed or self._died.is_set():
                raise MCPError(f"MCP server {name} did not open its event stream within {timeout:.0f}s")
            if time.monotonic() > deadline:
                self.close()
                raise MCPError(f"MCP server {name} did not open its event stream within {timeout:.0f}s")
            time.sleep(0.02)
        info = self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": CLIENT})
        self.server_info = info.get("serverInfo", {})
        self.notify("notifications/initialized")
        self.tools = self.request("tools/list", {}).get("tools", [])

    def _listen_loop(self) -> None:
        while not self._closed:
            try:
                self._listen_once()
            except Exception:  # noqa: BLE001 - a dropped stream reconnects below
                pass
            if self._closed:
                return
            if not self._endpoint:  # never got as far as the endpoint event: the config is wrong, stop trying
                self._died.set()
                self._replies.put(None)
                return
            self._replies.put(None)  # requests waiting on the dropped stream re-POST and catch the new stream
            time.sleep(0.2)
        self._replies.put(None)

    def _listen_once(self) -> None:
        event, data = "", []
        with httpx.Client(timeout=None) as stream_client:
            with stream_client.stream("GET", self.url, headers=self.headers) as resp:
                if resp.status_code >= 400:
                    return
                for line in resp.iter_lines():
                    if self._closed:
                        return
                    if not line:
                        if data:
                            payload = "\n".join(data)
                            if event == "endpoint":
                                self._endpoint = urljoin(self.url, payload)
                            elif event in ("", "message"):
                                try:
                                    msg = json.loads(payload)
                                except ValueError:
                                    msg = None
                                if isinstance(msg, dict):
                                    if "id" in msg and ("result" in msg or "error" in msg):
                                        self._replies.put(msg)
                                    elif "id" in msg and "method" in msg:
                                        result = {"roots": []} if msg["method"] == "roots/list" else {}
                                        self._raw({"jsonrpc": "2.0", "id": msg["id"], "result": result})
                        event, data = "", []
                    elif line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data.append(line[5:].lstrip())

    def _raw(self, msg: dict) -> httpx.Response:
        h = {"Content-Type": "application/json", **{k: v for k, v in self.headers.items() if k.lower() != "accept"}}
        r = self.client.post(self._endpoint, json=msg, headers=h, timeout=self.timeout)
        if r.status_code >= 400:
            raise MCPError(f"{self.name}: HTTP {r.status_code}: {r.text[:200]}")
        return r

    def _post(self, msg: dict, session: bool = False) -> list[dict]:  # answers come on the stream, not the POST
        self._raw(msg)
        return []

    def request(self, method: str, params: dict, session: bool = False) -> dict:
        with self._lock:
            self._id += 1
            rid = self._id
            self._raw({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
            while True:
                try:
                    msg = self._replies.get(timeout=self.timeout)
                except queue.Empty:
                    raise MCPError(f"MCP server {self.name} did not answer {method} within {self.timeout:.0f}s") from None
                if msg is None:
                    if self._closed:
                        self._replies.put(None)
                        raise MCPError(f"MCP server {self.name} closed its event stream")
                    # The stream dropped and is reconnecting; the server never saw the POST, so send it again.
                    self._raw({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
                    continue
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    raise MCPError(f"{self.name}: {msg['error'].get('message', msg['error'])}")
                return msg.get("result") or {}

    def close(self) -> None:
        self._closed = True
        self.client.close()


def _row_spec_slots(spec: dict) -> dict:
    """Split env/headers into plaintext values (kept in spec_json) and empty slots (secrets, stored encrypted).
    A slot also appears for a name listed under spec["secret_env"] / spec["secret_headers"] so the UI can mark
    well-known values (tokens, keys) secret without the user typing a placeholder."""
    env = dict(spec.get("env", {}))
    headers = dict(spec.get("headers", {}))
    for k in spec.get("secret_env", []):
        env.setdefault(k, "")
    for k in spec.get("secret_headers", []):
        headers.setdefault(k, "")
    return {"env": env, "headers": headers}


# Registry (process-local; configuration lives in the `mcp_servers` table)

_lock = threading.Lock()
_servers: dict[str, StdioServer | HttpServer] = {}
_open_failed: set[str] = set()


def _enabled_rows() -> list[dict]:
    try:
        return db.all("SELECT * FROM mcp_servers WHERE enabled = 1")
    except Exception:  # noqa: BLE001 - before db.init (import order), no servers yet
        return []


def _resolve(row: dict) -> dict:
    spec = json.loads(row["spec_json"])
    if row["secret_enc"]:
        secrets = json.loads(security.decrypt(row["secret_enc"]) or "{}")
        for k, v in secrets.get("env", {}).items():
            spec.setdefault("env", {})[k] = v
        for k, v in secrets.get("headers", {}).items():
            spec.setdefault("headers", {})[k] = v
    return spec


def _open(row: dict):
    spec = _resolve(row)
    name, timeout = row["name"], float(spec.get("timeout", 60))
    if spec.get("url"):
        transport = spec.get("transport") or "streamable_http"
        cls = SseServer if transport == "sse" else HttpServer
        return cls(name, spec["url"], spec.get("headers"), timeout)
    return StdioServer(name, spec["command"], spec.get("args", []), spec.get("env"), timeout)


def ensure_started(retry_failed: bool = False) -> None:
    """Open every enabled server once; a server that failed to start stays failed until the config changes
    (notify_change) or a caller asks for a retry (a user-facing tool call should not hammer a dead server, but
    the one call that hits it tries once more)."""
    with _lock:
        if retry_failed:
            _open_failed.clear()
        rows = _enabled_rows()
        want = {r["name"] for r in rows}
        for name in list(_servers):
            if name not in want:
                _servers.pop(name).close()
        for row in rows:
            if row["name"] in _servers or row["name"] in _open_failed:
                continue
            try:
                _servers[row["name"]] = _open(row)
                _open_failed.discard(row["name"])
                if row["last_error"]:
                    db.run("UPDATE mcp_servers SET last_error = '' WHERE id = ?", row["id"])
            except Exception as e:  # noqa: BLE001 - one bad server must not block the others
                if row["name"] not in _open_failed:  # log once per failure streak
                    log.warning("MCP server %s did not start: %s", row["name"], e)
                _open_failed.add(row["name"])
                db.run("UPDATE mcp_servers SET last_error = ? WHERE id = ?", _err_text(e), row["id"])


def restart(name: str) -> None:
    with _lock:
        if s := _servers.pop(name, None):
            s.close()
        _open_failed.discard(name)


def notify_change() -> None:
    """The API calls this after add/remove/enable; the next assistant turn reconnects."""
    restart_all()


def restart_all() -> None:
    with _lock:
        for s in _servers.values():
            s.close()
        _servers.clear()
        _open_failed.clear()


def get(name: str):
    with _lock:
        return _servers.get(name)


def tool_specs() -> list[dict]:
    ensure_started()
    specs = []
    with _lock:
        servers = list(_servers.items())
    for sname, s in servers:
        for t in s.tools:
            specs.append({"name": f"mcp__{sname}__{t['name']}"[:64],
                          "description": f"[MCP {sname}] {t.get('description', '')}"[:1024],
                          "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}})
    return specs


def resolve(qualified: str):
    """mcp__<server>__<tool> -> (server, tool, server_name), matching the truncated spec names."""
    if not qualified.startswith("mcp__"):
        return None
    with _lock:
        servers = list(_servers.items())
    for sname, s in servers:
        prefix = f"mcp__{sname}__"
        if qualified.startswith(prefix):
            tool = qualified[len(prefix):]
            for t in s.tools:
                if f"{prefix}{t['name']}"[:64] == qualified:
                    return s, t["name"], sname
            return s, tool, sname
    return None


def call(qualified: str, arguments: dict) -> tuple[bool, str]:
    found = resolve(qualified)
    if not found:  # maybe never started, or its last start failed: try once more, then give up
        ensure_started(retry_failed=True)
        found = resolve(qualified)
    if not found:
        raise MCPError(f"No running MCP server answers the tool {qualified}")
    server, tool, _ = found
    return server.call(tool, arguments)


def connected_tools() -> list[dict]:
    """What the API's tool list returns: per enabled server, its tools (or the startup error)."""
    ensure_started()
    rows = _enabled_rows()
    out = []
    with _lock:
        servers = dict(_servers)
    for row in rows:
        s = servers.get(row["name"])
        out.append({"name": row["name"], "status": "connected" if s else "error",
                    "last_error": "" if s else db.one("SELECT last_error FROM mcp_servers WHERE id = ?", row["id"])["last_error"],
                    "server_info": getattr(s, "server_info", {}) if s else {},
                    "tools": [{"name": t["name"], "qualified": f"mcp__{row['name']}__{t['name']}"[:64],
                               "description": t.get("description", "")} for t in (s.tools if s else [])]})
    return out


def save_server(name: str, spec: dict, secrets: dict | None = None, server_id: int | None = None) -> int:
    """Insert or update one configured server. Names listed in spec["secret_env"] / spec["secret_headers"] (or env
    entries given with an empty value) are secret slots: their values come from `secrets`, are stored encrypted
    like provider keys, and never appear in spec_json. Updating with an empty secret value keeps the stored one."""
    slots = _row_spec_slots(spec)
    plain = {"env": {k: v for k, v in slots["env"].items() if v}, "headers": {k: v for k, v in slots["headers"].items() if v}}
    new_env = (secrets or {}).get("env", {})
    new_headers = (secrets or {}).get("headers", {})
    prior: dict = {"env": {}, "headers": {}}
    if server_id:
        old = db.one("SELECT secret_enc FROM mcp_servers WHERE id = ?", server_id)
        if old and old["secret_enc"]:
            prior = json.loads(security.decrypt(old["secret_enc"]) or "{}")
    merged = {
        "env": {k: new_env.get(k) or prior.get("env", {}).get(k, "") for k in slots["env"] if not slots["env"][k]},
        "headers": {k: new_headers.get(k) or prior.get("headers", {}).get(k, "") for k in slots["headers"] if not slots["headers"][k]},
    }
    stored = security.encrypt(json.dumps(merged)) if (merged["env"] or merged["headers"]) else ""
    row = {"name": name, "transport": spec.get("transport") or ("streamable_http" if spec.get("url") else "stdio"),
           "url": spec.get("url", ""), "command": spec.get("command", ""), "enabled": int(spec.get("enabled", True)),
           "spec_json": json.dumps({**spec, "env": plain["env"], "headers": plain["headers"]}), "secret_enc": stored}
    if server_id:
        db.run("UPDATE mcp_servers SET name=?, transport=?, url=?, command=?, enabled=?, spec_json=?, secret_enc=? WHERE id=?",
               row["name"], row["transport"], row["url"], row["command"], row["enabled"], row["spec_json"], row["secret_enc"], server_id)
        return server_id
    return db.run("INSERT INTO mcp_servers(name, transport, url, command, enabled, spec_json, secret_enc, created_at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  row["name"], row["transport"], row["url"], row["command"], row["enabled"], row["spec_json"],
                  row["secret_enc"], db.now())


def public_row(row: dict) -> dict:
    """A server row as the UI may see it: secret slots are named, never valued."""
    spec = json.loads(row["spec_json"])
    secrets = {"env": [], "headers": []}
    if row["secret_enc"]:
        try:
            saved = json.loads(security.decrypt(row["secret_enc"]) or "{}")
            secrets = {"env": sorted(saved.get("env", {})), "headers": sorted(saved.get("headers", {}))}
        except Exception:  # noqa: BLE001 - unreadable secret store: treat as none
            pass
    return {"id": row["id"], "name": row["name"], "transport": row["transport"], "url": row["url"],
            "command": row["command"], "enabled": bool(row["enabled"]), "spec": spec, "secrets": secrets,
            "last_error": row["last_error"], "created_at": row["created_at"],
            "needs_secrets": {"env": [k for k, v in spec.get("env", {}).items() if not v and k not in secrets["env"]],
                              "headers": [k for k, v in spec.get("headers", {}).items() if not v and k not in secrets["headers"]]}}
