"""MCP server on stdio (newline-delimited JSON-RPC 2.0), standard library only.

Two ways to use it:
  * use_computer: hand Rooks Desk a whole task in plain English; its own agent loop (screenshot -> model -> act ->
    verify) does it and returns what it did, the commands it ran, the files it touched and a receipt.
  * low-level tools (screenshot, click, type_text, hotkey, scroll, drag, open_app, list_windows, focus_window, elements,
    run_shell, read_file, write_file, list_dir, search_files, move_file, clipboard_get/set) for an agent that can see
    and wants to drive itself. screenshot returns an image plus the numbered element list; click/type take an element id
    from the last screenshot, or x/y in that screenshot's pixels.

Register it:
  Rooks Code   added automatically when rooks-desk is installed (turn off with [mcp] desk = false)
  Claude Code  claude mcp add rooks-desk -- rooks-desk mcp
  Codex        ~/.codex/config.toml   [mcp_servers.rooks-desk] command = "rooks-desk" args = ["mcp"]

Every call goes through the same policy as the agent. When the client supports MCP elicitation, sensitive actions
(shell commands, files outside the allowed folders, deleting, sending, buying, logging in) become a question the
person answers in that client; otherwise they are refused. The calling agent cannot approve for the person.
The kill switch (Ctrl+Alt+Esc, mouse to the top-left corner, `rooks-desk stop`) refuses every action for 30 seconds.
"""

from __future__ import annotations

import base64
import itertools
import json
import os
import sys
import threading
import time

from . import __version__, runner
from .agent import Desk, Stopped
from .policy import Approval, parse_categories
from .receipts import RunLog

PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_PT = {"id": {"type": "integer", "description": "element id from the last screenshot's list"},
       "x": {"type": "number", "description": "x in the last screenshot's pixels"}, "y": {"type": "number"},
       "target": {"type": "string", "description": "what you are clicking, in words (used for safety checks)"}}

TOOLS = [
    {"name": "use_computer",
     "description": "Give Rooks Desk a task to do on this computer in plain English (e.g. 'open Mousepad, write a haiku, save it "
                    "as ~/Documents/haiku.txt'). It looks at the screen, clicks, types, runs terminal commands and edits files "
                    "until done, asking the person before anything sensitive. Returns what it did and a receipt with a screenshot "
                    "per step. Slow: 30 s to minutes.",
     "inputSchema": {"type": "object", "properties": {
         "goal": {"type": "string"}, "max_steps": {"type": "integer", "default": 30, "minimum": 1, "maximum": 80},
         "dry_run": {"type": "boolean", "default": False, "description": "plan and show steps without doing anything"},
         "provider": {"type": "string"}, "model": {"type": "string"}}, "required": ["goal"]}},
    {"name": "screenshot", "description": "See the screen now: a PNG plus the window in front, the open windows and a numbered list of "
                                          "on-screen elements (role, name, position in the image's pixels).",
     "inputSchema": {"type": "object", "properties": {"monitor": {"type": "integer", "default": 1}, "elements": {"type": "boolean", "default": True}}}},
    {"name": "click", "description": "Click an element id or x/y from the last screenshot.",
     "inputSchema": {"type": "object", "properties": {**_PT, "button": {"type": "string", "enum": ["left", "right", "middle"]},
                                                      "count": {"type": "integer", "default": 1}}}},
    {"name": "type_text", "description": "Type text into the focused field (or click element id first). \\n presses Enter.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}, "id": _PT["id"], "enter": {"type": "boolean"}},
                     "required": ["text"]}},
    {"name": "hotkey", "description": "Press a key combination, e.g. 'ctrl+s', 'cmd+space', 'enter', 'alt+tab'.",
     "inputSchema": {"type": "object", "properties": {"keys": {"type": "string"}}, "required": ["keys"]}},
    {"name": "scroll", "description": "Scroll at a point (default: middle of the screen).",
     "inputSchema": {"type": "object", "properties": {**_PT, "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
                                                      "amount": {"type": "integer", "default": 5}}}},
    {"name": "drag", "description": "Drag from one point to another (screenshot pixels).",
     "inputSchema": {"type": "object", "properties": {"from": {"type": "array", "items": {"type": "number"}},
                                                      "to": {"type": "array", "items": {"type": "number"}}}, "required": ["from", "to"]}},
    {"name": "open_app", "description": "Open an app by name (or a file/URL with its default app).",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "list_windows", "description": "Open windows: app, title, position, which is in front.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "focus_window", "description": "Bring a window to the front by part of its title or app name.",
     "inputSchema": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}},
    {"name": "elements", "description": "The accessibility tree of the window in front as a numbered list (no image). For models that cannot see.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "run_shell", "description": "Run a terminal command and return its output and exit code. Asks the person unless read-only.",
     "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}, "timeout": {"type": "number", "default": 120}},
                     "required": ["command"]}},
    {"name": "read_file", "description": "Read a text file.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write a text file (asks outside the allowed folders).",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "text": {"type": "string"}, "append": {"type": "boolean"}},
                     "required": ["path", "text"]}},
    {"name": "list_dir", "description": "List a folder.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "search_files", "description": "Find files by name pattern and optional text inside.",
     "inputSchema": {"type": "object", "properties": {"root": {"type": "string"}, "pattern": {"type": "string"}, "text": {"type": "string"}},
                     "required": ["root"]}},
    {"name": "move_file", "description": "Move or rename a file.",
     "inputSchema": {"type": "object", "properties": {"from": {"type": "string"}, "to": {"type": "string"}}, "required": ["from", "to"]}},
    {"name": "clipboard_get", "description": "Read the clipboard text.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "clipboard_set", "description": "Put text on the clipboard.",
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
]

LOW = {"click": "click", "type_text": "type", "hotkey": "hotkey", "scroll": "scroll", "drag": "drag", "open_app": "open_app",
       "list_windows": "list_windows", "focus_window": "focus_window", "run_shell": "shell", "read_file": "read_file",
       "write_file": "write_file", "list_dir": "list_dir", "search_files": "search_files", "move_file": "move_file",
       "clipboard_get": "clipboard_get", "clipboard_set": "clipboard_set"}


class Server:
    def __init__(self, inp=None, out=None, backend=None, model_factory=None):
        self.inp, self.out = inp or sys.stdin, out or sys.stdout
        self.wlock = threading.Lock()
        self.client_caps: dict = {}
        self.pending: dict[str, tuple[threading.Event, dict]] = {}
        self.ids = itertools.count(1)
        self.backend, self.model_factory = backend, model_factory
        self._desk: Desk | None = None
        self.obs = None
        self.stopped_until = 0.0
        self.overlay = None
        self.act_lock = threading.Lock()

    # --- JSON-RPC plumbing
    def send(self, msg: dict) -> None:
        with self.wlock:
            self.out.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self.out.flush()

    def request(self, method: str, params: dict, timeout: float = 600) -> dict | None:
        rid = f"rd-{next(self.ids)}"
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
            if "method" not in msg and str(msg.get("id")) in self.pending:
                ev, box = self.pending[str(msg["id"])]
                box["result"] = msg.get("result") or {"action": "decline", "error": msg.get("error")}
                ev.set()
                continue
            if msg.get("method") == "tools/call":
                threading.Thread(target=self._handle, args=(msg,), daemon=True).start()
            else:
                self._handle(msg)
        if self.overlay:
            self.overlay.close()
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
                    "serverInfo": {"name": "rooks-desk", "version": __version__},
                    "instructions": "Controls this computer: screen, mouse, keyboard, apps, terminal and files. Call screenshot "
                                    "first, then act with element ids from it; or hand a whole task to use_computer."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            name, args = params.get("name"), params.get("arguments") or {}
            try:
                return self.call(name, args)
            except Exception as e:  # noqa: BLE001 - tool errors are results, so the calling agent can adapt
                return {"content": [{"type": "text", "text": f"{e.__class__.__name__}: {str(e).splitlines()[0][:500] if str(e) else ''}"}],
                        "isError": True}
        return None

    # --- approvals
    def approver(self, ap: Approval) -> bool:
        if "elicitation" not in self.client_caps:
            return False
        r = self.request("elicitation/create", {
            "message": f"Rooks Desk: {ap.summary}. Allow it?",
            "requestedSchema": {"type": "object", "properties": {"approve": {"type": "boolean", "title": "Allow this"}},
                                "required": ["approve"]}})
        return bool(r and r.get("action") == "accept" and (r.get("content") or {}).get("approve"))

    # --- desk for the low-level tools
    def desk(self) -> Desk:
        if self._desk is None:
            log = RunLog(runner.home(), provider="mcp", model="caller")
            self._desk = runner.build_desk(runner.Settings(), log, backend=self.backend)
            self._turn = log.add("turn", "running", tool="desk.mcp", args="low-level MCP session")
        return self._desk

    def _text(self, data, error: bool = False) -> dict:
        return {"content": [{"type": "text", "text": data if isinstance(data, str) else json.dumps(data, ensure_ascii=False, indent=1)}],
                "isError": error}

    def call(self, name: str, args: dict) -> dict:
        if name == "use_computer":
            goal = str(args.get("goal") or "").strip()
            if not goal:
                raise ValueError("goal is required")
            s = runner.Settings(provider=args.get("provider") or None, model=args.get("model") or None,
                                max_steps=max(1, min(int(args.get("max_steps") or 30), 80)), dry_run=bool(args.get("dry_run")),
                                pre_approved=parse_categories(os.getenv("ROOKS_DESK_MCP_PREAPPROVE", "")),
                                workbench_db=os.getenv("ROOKS_DESK_WORKBENCH_DB"))
            model = self.model_factory(s) if self.model_factory else None
            res = runner.run_task(goal, s, approver=self.approver, backend=self.backend, model=model)
            d = res.to_dict()
            d["note"] = "The answer is the agent's own claim; the receipt and step screenshots show what it actually did."
            if res.denied and "elicitation" not in self.client_caps:
                d["note"] += " Sensitive steps were refused because this MCP client cannot ask the person; tell them."
            return self._text(d)
        d = self.desk()
        if time.time() < self.stopped_until or d.kill.stopped:
            if d.kill.stopped:
                self.stopped_until = time.time() + 30
                d.kill.event.clear()
            return self._text(f"The person pressed the kill switch ({d.kill.reason or 'stop'}). Rooks Desk refuses actions for 30 seconds; "
                              "ask them before continuing.", True)
        if self.overlay is None and d.b.name != "fake" and name not in ("screenshot", "elements", "list_windows", "read_file", "list_dir",
                                                                        "search_files", "clipboard_get"):
            from .killswitch import HOTKEY_LABEL
            from .overlay import Overlay
            self.overlay = Overlay(d.kill.trip, HOTKEY_LABEL)
        with self.act_lock:
            if name == "screenshot":
                d.monitor = int(args.get("monitor") or 1)
                self.obs = d.observe(with_elements=args.get("elements", True) is not False)
                d.log.step_shot(len(d.log.rows), self.obs.png)
                return {"content": [{"type": "image", "data": base64.b64encode(self.obs.png).decode(), "mimeType": "image/png"},
                                    {"type": "text", "text": self.obs.describe()}], "isError": False}
            if name == "elements":
                self.obs = d.observe()
                return self._text(self.obs.describe())
            if name not in LOW:
                raise ValueError(f"unknown tool {name}")
            action = LOW[name]
            if action in ("click", "type", "scroll", "drag") and self.obs is None:
                self.obs = d.observe()
            a = dict(args)
            if action == "click" and a.get("button") == "right":
                a.pop("button")
                action = "right_click"
            if a.get("id") is not None and self.obs is not None:
                i = int(a["id"])
                a["_el"] = self.obs.elements[i - 1] if 1 <= i <= len(self.obs.elements) else None
            if action == "type" and a.get("_el") is not None and a["_el"].secret:
                return self._text("That is a password field. Rooks Desk never types passwords: ask the person to type it.", True)
            cat, why = d.needs(action, a, self.obs)
            row = d.log.add("tool_call", "running", self._turn, tool=f"desk.{action}", args=json.dumps({k: v for k, v in a.items()
                                                                                                       if not k.startswith("_")}, default=str)[:2000])
            if cat:
                ap = Approval(cat, action, _summary(action, a, why))
                ok = self.approver(ap)
                d.log.add("decision", "approved" if ok else "denied", self._turn, tool=f"desk.{action}", args=ap.summary, detail=cat)
                if not ok:
                    d.log.finish(row, "denied", failure_reason=cat)
                    extra = "" if "elicitation" in self.client_caps else " (this MCP client cannot ask the person, so sensitive steps are refused)"
                    return self._text(f"The person did not allow: {why}.{extra} Do not try this another way.", True)
                if cat == "app":
                    d.approved_apps.add(str(a.get("name") or a.get("title") or (self.obs.window.app if self.obs and self.obs.window else "")))
            try:
                d.check_stop()
                result = d.do(action, {k: v for k, v in a.items() if not k.startswith("_")}, self.obs)
            except Stopped as e:
                d.log.finish(row, "tool_error", failure_reason="stopped")
                return self._text(f"Stopped by the person: {e}", True)
            except Exception as e:  # noqa: BLE001
                d.log.finish(row, "tool_error", failure_reason=str(e)[:500])
                raise
            d.log.finish(row, "ok", detail=result[:3000])
            if action not in ("read_file", "list_dir", "search_files", "clipboard_get", "list_windows", "shell"):
                self.obs = None  # the screen changed; coordinates from the old screenshot are stale
                result += "\n(Take a new screenshot to see the result.)"
            return self._text(result)


def _summary(action, args, why):
    from .agent import _summary as s
    return s(action, {k: v for k, v in args.items() if not k.startswith("_")}, why)


def main(argv: list[str] | None = None) -> int:
    return Server().serve()
