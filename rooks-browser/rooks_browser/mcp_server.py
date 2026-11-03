"""MCP server on stdio (newline-delimited JSON-RPC 2.0), standard library only.

Tools: browse_web (the agent, multi-step), read_page (one page's text and links, no model), web_search (no model).
Register it with any MCP client:

  Rooks Code   ~/.config/ai-workspace/rooks.toml   [mcp.browser] command = "rooks-browser" args = ["mcp"]
  Claude Code  claude mcp add rooks-browser -- rooks-browser mcp
  Codex        ~/.codex/config.toml               [mcp_servers.rooks-browser] command = "rooks-browser" args = ["mcp"]

Approvals: when the client supports MCP elicitation (it said so in `initialize`), a sensitive action becomes an
elicitation/create request, so the user answers in Claude Code / Codex itself. Otherwise sensitive actions are refused
and listed in the result. The calling agent cannot approve on the user's behalf: the `approve` argument only exists when
the user turned it on with ROOKS_BROWSER_MCP_PREAPPROVE=1 in the MCP config (and never covers logins).
The browser is headless unless ROOKS_BROWSER_HEADED=1. Model: ROOKS_BROWSER_PROVIDER / ROOKS_BROWSER_MODEL or the
tool's provider/model arguments.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
import threading

from . import __version__, runner, search
from .policy import parse_categories

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

APPROVE_ARG = {"type": "array", "items": {"type": "string", "enum": ["submit", "purchase", "message", "download"]},
               "description": "categories the USER explicitly approved for this task (ask them first)"}


def preapprove_enabled() -> bool:
    return os.getenv("ROOKS_BROWSER_MCP_PREAPPROVE") == "1"


def tools() -> list[dict]:
    out = json.loads(json.dumps(TOOLS))
    if preapprove_enabled():
        out[0]["inputSchema"]["properties"]["approve"] = APPROVE_ARG
    return out


TOOLS = [
    {"name": "browse_web",
     "description": "Give a browser agent a goal in plain English (e.g. 'find the opening hours of the State Library on its "
                    "website'). It searches, opens pages, clicks, types and reads, then returns its answer, the pages it visited "
                    "and a receipt path. Form submits, purchases, messages and logins need the user's approval. Slow: 30 s to minutes.",
     "inputSchema": {"type": "object", "properties": {
         "goal": {"type": "string"}, "max_steps": {"type": "integer", "default": 20, "minimum": 1, "maximum": 60},
         "allowed_sites": {"type": "array", "items": {"type": "string"}, "description": "only these sites, e.g. ['*.example.com']"},
         "start_url": {"type": "string"},
         "provider": {"type": "string"}, "model": {"type": "string"}}, "required": ["goal"]}},
    {"name": "read_page", "description": "Open one URL in a real browser (JavaScript runs) and return its title, visible text and links. No AI, fast.",
     "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}, "max_chars": {"type": "integer", "default": 20000}},
                     "required": ["url"]}},
    {"name": "web_search", "description": "Search the web (SearXNG if configured, else a search page in the browser). Returns titles, URLs and snippets.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
]


class Server:
    def __init__(self, inp=None, out=None):
        self.inp, self.out = inp or sys.stdin, out or sys.stdout
        self.wlock = threading.Lock()
        self.client_caps: dict = {}
        self.pending: dict[str, tuple[threading.Event, dict]] = {}
        self.ids = itertools.count(1)

    def send(self, msg: dict) -> None:
        with self.wlock:
            self.out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.out.flush()

    def request(self, method: str, params: dict, timeout: float = 600) -> dict | None:
        """A request from us to the client (elicitation), answered on the reader thread."""
        rid = f"rb-{next(self.ids)}"
        ev, box = threading.Event(), {}
        self.pending[rid] = (ev, box)
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        ok = ev.wait(timeout)
        self.pending.pop(rid, None)
        return box.get("result") if ok else None

    def serve(self) -> int:
        for line in self.inp:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                self.send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
                continue
            if "method" not in msg and str(msg.get("id")) in self.pending:  # a reply to our request
                ev, box = self.pending[str(msg["id"])]
                box["result"] = msg.get("result") or {"action": "decline", "error": msg.get("error")}
                ev.set()
                continue
            if msg.get("method") == "tools/call":
                threading.Thread(target=self._handle, args=(msg,), daemon=True).start()  # keep reading: elicitation replies
            else:
                self._handle(msg)
        return 0

    def _handle(self, msg: dict) -> None:
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        try:
            result = self.dispatch(method, params)
        except Exception as e:  # noqa: BLE001
            if mid is not None:
                self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)[:500]}})
            return
        if mid is not None and result is not None:
            self.send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif mid is not None and method not in ("notifications/initialized",):
            self.send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}})

    def dispatch(self, method: str, params: dict):
        if method == "initialize":
            self.client_caps = params.get("capabilities") or {}
            v = params.get("protocolVersion")
            return {"protocolVersion": v if v in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {}},
                    "serverInfo": {"name": "rooks-browser", "version": __version__},
                    "instructions": "A real web browser. Use web_search or read_page for quick lookups and browse_web for multi-step goals."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": tools()}
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                data = asyncio.run(self.call(name, args))
                return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, indent=1)}], "isError": False}
            except Exception as e:  # noqa: BLE001 - tool errors are results, so the calling agent can adapt
                return {"content": [{"type": "text", "text": f"{e.__class__.__name__}: {str(e).splitlines()[0][:500] if str(e) else ''}"}],
                        "isError": True}
        if method and method.startswith("notifications/"):
            return None
        return None

    def _settings(self, args: dict) -> runner.Settings:
        from .cli import has_display
        return runner.Settings(provider=args.get("provider") or None, model=args.get("model") or None,
                               max_steps=max(1, min(int(args.get("max_steps") or 20), 60)),
                               headless=not (os.getenv("ROOKS_BROWSER_HEADED") == "1" and has_display()),
                               engine=os.getenv("ROOKS_BROWSER_ENGINE", "auto"), cdp_url=os.getenv("ROOKS_BROWSER_CDP_URL"),
                               allow=list(args.get("allowed_sites") or []), start_url=args.get("start_url") or None,
                               pre_approved=parse_categories(",".join(args.get("approve") or [])) if preapprove_enabled() else set(),
                               workbench_db=os.getenv("ROOKS_BROWSER_WORKBENCH_DB"))

    def approver(self, ap) -> bool:
        if "elicitation" not in self.client_caps:
            return False
        r = self.request("elicitation/create", {
            "message": f"Rooks Browser wants to {ap.description} (on {ap.url}). Allow it?",
            "requestedSchema": {"type": "object", "properties": {"approve": {"type": "boolean", "title": "Allow this action"}},
                                "required": ["approve"]}})
        return bool(r and r.get("action") == "accept" and (r.get("content") or {}).get("approve"))

    async def call(self, name: str, args: dict) -> dict:
        from .cli import one_off_read, one_off_search
        s = self._settings(args)
        if name == "read_page":
            return await one_off_read(str(args.get("url") or ""), s, int(args.get("max_chars") or 20000))
        if name == "web_search":
            return await one_off_search(str(args.get("query") or ""), s)
        if name == "browse_web":
            goal = str(args.get("goal") or "").strip()
            if not goal:
                raise ValueError("goal is required")
            res = await runner.run_task(goal, s, approver=lambda ap: asyncio.to_thread(self.approver, ap))
            d = res.to_dict()
            d["note"] = "The answer is the agent's own claim; the urls and receipt show what it actually did."
            if res.denied and "elicitation" not in self.client_caps:
                d["note"] += (" Sensitive actions were refused because this MCP client cannot ask the user. Tell the user; they "
                              "can run it themselves with `rooks-browser` in a terminal, or use a client with MCP elicitation.")
            return d
        raise ValueError(f"unknown tool {name}")


def main(argv: list[str] | None = None) -> int:
    return Server().serve()
