#!/usr/bin/env python3
"""
Rooks Companion Hook Bridge

Single-file Python 3 stdlib only. Reads hook JSON from stdin, maps events to the
Companion API, and handles permission/question requests with long-polling.

FAIL OPEN: On any error (server down, file missing, timeout, parse error),
prints nothing and exits 0. Never auto-allows, never blocks the agent.

Usage:
    rooks_companion_hook.py claude <HookEventName>
    rooks_companion_hook.py codex <HookEventName>
    rooks_companion_hook.py gemini <HookEventName>

Hook input is read from stdin. Hook output (for permission decisions) is written to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any
from urllib import request, error, parse

# ──────────────────────────────────────────────────────────────────────────────
# Constants & Config
# ──────────────────────────────────────────────────────────────────────────────

COMPANION_FILE = Path(os.getenv("ROOKS_COMPANION_FILE", Path.home() / ".rooks" / "companion.json"))
REQUEST_TIMEOUT = 1.5          # seconds for fire-and-forget event posts
WAIT_TIMEOUT = 55.0            # seconds for long-poll wait
MAX_WAIT_RETRIES = 2           # retries for wait endpoint
USER_AGENT = "rooks-companion-hook/1.0"

# Event mappings for each agent
CLAUDE_EVENT_MAP = {
    "SessionStart": "start",
    "UserPromptSubmit": "start",
    "PreToolUse": "step",
    "PostToolUse": "step",
    "PostToolUseFailure": "error",
    "Notification": "idle",
    "Stop": "done",
    "SubagentStop": "done",
    "SessionEnd": "done",
}

CODEX_EVENT_MAP = {
    "Stop": "done",
    "PermissionRequest": "permission",
    "Notification": "idle",
}

GEMINI_EVENT_MAP = {
    "SessionStart": "start",
    "BeforeAgent": "start",
    "BeforeTool": "step",
    "AfterTool": "step",
    "AfterAgent": "done",
    "Notification": "idle",
    "SessionEnd": "done",
}

# Kind mapping for step events
TOOL_KIND_MAP = {
    # Claude tools
    "Read": "read",
    "Write": "edit",
    "Edit": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Bash": "run",
    "Glob": "search",
    "Grep": "search",
    "Task": "other",
    "WebFetch": "web",
    "WebSearch": "web",
    "TodoWrite": "other",
    # Codex tools (similar names)
    "run_shell_command": "run",
    "read_file": "read",
    "write_file": "edit",
    "replace": "edit",
    "glob": "search",
    "grep": "search",
    # Gemini tools
    "read_file": "read",
    "run_shell_command": "run",
    "write_file": "edit",
    "replace": "edit",
    "glob": "search",
    "grep": "search",
}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _read_stdin() -> dict[str, Any]:
    """Read and parse JSON from stdin. Returns {} on failure."""
    try:
        data = sys.stdin.read()
        if not data.strip():
            return {}
        return json.loads(data)
    except Exception:
        return {}


def _load_companion() -> tuple[str, str] | None:
    """Load (url, token) from companion file. Returns None on failure."""
    try:
        if not COMPANION_FILE.exists():
            return None
        data = json.loads(COMPANION_FILE.read_text())
        url = data.get("url", "").rstrip("/")
        token = data.get("token", "")
        if not url or not token:
            return None
        return url, token
    except Exception:
        return None


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "X-Workbench": "1",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }


def _post(url: str, token: str, path: str, body: dict, timeout: float = REQUEST_TIMEOUT) -> dict | None:
    """POST JSON to companion API. Returns parsed response or None on failure."""
    try:
        full_url = f"{url}{path}"
        data = json.dumps(body).encode()
        req = request.Request(full_url, data=data, headers=_headers(token), method="POST")
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _get(url: str, token: str, path: str, params: dict | None = None, timeout: float = REQUEST_TIMEOUT) -> dict | None:
    """GET from companion API. Returns parsed response or None on failure."""
    try:
        full_url = f"{url}{path}"
        if params:
            full_url += "?" + parse.urlencode(params)
        req = request.Request(full_url, headers=_headers(token), method="GET")
        with request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _post_event(url: str, token: str, tool: str, session: str, event: str,
                name: str = "", folder: str = "", text: str = "", kind: str = "",
                usage: dict | None = None) -> None:
    """Fire-and-forget event post. Never raises."""
    body = {
        "tool": tool,
        "session": session,
        "name": name,
        "folder": folder,
        "event": event,
        "text": text[:2000],
        "kind": kind,
        "usage": usage,
    }
    _post(url, token, "/api/companion/events", body)


def _create_request(url: str, token: str, tool: str, session: str, kind: str,
                    title: str, detail: str, action: str, target: str,
                    options: list[str], name: str = "") -> dict | None:
    """Create a permission/question request. Returns request dict or None."""
    body = {
        "tool": tool,
        "session": session,
        "name": name,
        "kind": kind,
        "title": title[:300],
        "detail": detail[:4000],
        "action": action,
        "target": target[:1000],
        "options": options[:8],
        "multi": len(options) > 1,
    }
    return _post(url, token, "/api/companion/requests", body)


def _wait_for_decision(url: str, token: str, rid: str) -> dict | None:
    """Long-poll for decision. Returns request dict with status."""
    for _ in range(MAX_WAIT_RETRIES):
        resp = _get(url, token, f"/api/companion/requests/{rid}/wait", {"timeout": WAIT_TIMEOUT})
        if resp and resp.get("status") != "pending":
            return resp
        # On timeout, status stays "pending" - retry once more
    # Final attempt without relying on long-poll
    return _get(url, token, f"/api/companion/requests/{rid}/wait", {"timeout": 1})


# ──────────────────────────────────────────────────────────────────────────────
# Claude Code Hook Handling
# ──────────────────────────────────────────────────────────────────────────────

def _claude_tool_text(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Extract human-readable text and kind from Claude tool call."""
    kind = TOOL_KIND_MAP.get(tool_name, "other")
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return f"Ran {cmd[:80]}", kind
    if tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("path", "")
        verb = "Read" if tool_name == "Read" else "Wrote" if tool_name == "Write" else "Edited"
        return f"{verb} {path}", kind
    if tool_name in ("Glob", "Grep"):
        pattern = tool_input.get("pattern") or tool_input.get("query", "")
        return f"Searched {pattern[:60]}", kind
    if tool_name == "Task":
        desc = tool_input.get("description", "")
        return f"Task: {desc[:60]}", kind
    if tool_name == "WebFetch":
        url = tool_input.get("url", "")
        return f"Fetched {url[:60]}", kind
    if tool_name == "WebSearch":
        query = tool_input.get("query", "")
        return f"Searched web: {query[:60]}", kind
    if tool_name == "TodoWrite":
        return "Updated todo list", kind
    return tool_name, kind


def _handle_claude(hook_event: str, data: dict, url: str, token: str) -> None:
    session = data.get("session_id", "")[:200]
    cwd = data.get("cwd", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    mapped = CLAUDE_EVENT_MAP.get(hook_event)
    if not mapped:
        return

    # Session start / user prompt
    if hook_event in ("SessionStart", "UserPromptSubmit"):
        prompt = data.get("prompt", "") if hook_event == "UserPromptSubmit" else ""
        _post_event(url, token, "claude-code", session, "start",
                    name="Claude Code", folder=cwd, text=prompt[:200], kind="think")
        return

    # Tool events
    if hook_event in ("PreToolUse", "PostToolUse", "PostToolUseFailure"):
        text, kind = _claude_tool_text(tool_name, tool_input)
        event_type = "error" if hook_event == "PostToolUseFailure" else mapped
        _post_event(url, token, "claude-code", session, event_type,
                    name="Claude Code", folder=cwd, text=text, kind=kind)
        return

    # Notification -> idle or question
    if hook_event == "Notification":
        notif_type = data.get("notification_type", "")
        if notif_type == "permission_prompt":
            # This is a permission prompt - create a request
            _post_event(url, token, "claude-code", session, "idle",
                        name="Claude Code", folder=cwd, text="Waiting for permission", kind="other")
        else:
            _post_event(url, token, "claude-code", session, "idle",
                        name="Claude Code", folder=cwd, text="Idle", kind="other")
        return

    # Stop / SubagentStop / SessionEnd -> done
    if hook_event in ("Stop", "SubagentStop", "SessionEnd"):
        _post_event(url, token, "claude-code", session, "done",
                    name="Claude Code", folder=cwd, text="Done", kind="done")
        return


# ──────────────────────────────────────────────────────────────────────────────
# Codex CLI Hook Handling
# ──────────────────────────────────────────────────────────────────────────────

def _handle_codex(hook_event: str, data: dict, url: str, token: str) -> dict | None:
    """
    Handle Codex hook events. Returns permission decision dict for stdout
    if this is a PermissionRequest, else None.
    """
    session = data.get("session_id", data.get("thread_id", ""))[:200]
    cwd = data.get("cwd", data.get("working_directory", ""))

    if hook_event == "Stop":
        _post_event(url, token, "codex", session, "done",
                    name="Codex", folder=cwd, text="Done", kind="done")
        return None

    if hook_event == "Notification":
        _post_event(url, token, "codex", session, "idle",
                    name="Codex", folder=cwd, text="Idle", kind="other")
        return None

    if hook_event == "PermissionRequest":
        # Codex PermissionRequest has: tool_name, tool_input, title?, detail?
        tool_name = data.get("tool_name", "Bash")
        tool_input = data.get("tool_input", {})
        title = data.get("title", f"Codex wants to run {tool_name}")
        detail = data.get("detail", "")
        action = tool_name
        target = json.dumps(tool_input) if tool_input else ""
        options = data.get("options", ["Allow", "Deny"])

        req = _create_request(url, token, "codex", session, "permission",
                              title, detail, action, target, options, name="Codex")
        if not req:
            return None

        rid = req.get("id")
        status = req.get("status", "pending")

        if status == "allow" or not rid:
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                            "decision": {"behavior": "allow" if status == "allow" else "deny"}}}

        # Long-poll for decision
        decided = _wait_for_decision(url, token, rid)
        if not decided:
            return None

        decision = decided.get("status", "deny")
        if decision == "allow":
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                            "decision": {"behavior": "allow"}}}
        elif decision == "always":
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                            "decision": {"behavior": "allow", "updatedInput": tool_input}}}
        else:
            return {"hookSpecificOutput": {"hookEventName": "PermissionRequest",
                                            "decision": {"behavior": "deny"}}}

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Gemini CLI Hook Handling
# ──────────────────────────────────────────────────────────────────────────────

def _gemini_tool_text(tool_name: str, tool_input: dict) -> tuple[str, str]:
    kind = TOOL_KIND_MAP.get(tool_name, "other")
    if tool_name == "run_shell_command":
        cmd = tool_input.get("command", "")
        return f"Ran {cmd[:80]}", kind
    if tool_name in ("read_file", "write_file", "replace"):
        path = tool_input.get("path", "")
        verb = "Read" if tool_name == "read_file" else "Wrote" if tool_name == "write_file" else "Edited"
        return f"{verb} {path}", kind
    if tool_name in ("glob", "grep"):
        pattern = tool_input.get("pattern") or tool_input.get("query", "")
        return f"Searched {pattern[:60]}", kind
    return tool_name, kind


def _handle_gemini(hook_event: str, data: dict, url: str, token: str) -> dict | None:
    """
    Handle Gemini hook events. Returns decision dict for stdout
    if this is a BeforeTool that denies, else None.
    """
    session = data.get("session_id", "")[:200]
    cwd = data.get("cwd", "")
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})

    mapped = GEMINI_EVENT_MAP.get(hook_event)
    if not mapped:
        return None

    if hook_event in ("SessionStart", "BeforeAgent"):
        _post_event(url, token, "gemini-cli", session, "start",
                    name="Gemini CLI", folder=cwd, text="Started", kind="think")
        return None

    if hook_event == "BeforeTool":
        text, kind = _gemini_tool_text(tool_name, tool_input)
        _post_event(url, token, "gemini-cli", session, "step",
                    name="Gemini CLI", folder=cwd, text=text, kind=kind)
        # BeforeTool can deny - but we don't have a permission flow here
        # unless the user configures it. For now, just report.
        return None

    if hook_event == "AfterTool":
        text, kind = _gemini_tool_text(tool_name, tool_input)
        event_type = "error" if data.get("tool_response", {}).get("error") else "step"
        _post_event(url, token, "gemini-cli", session, event_type,
                    name="Gemini CLI", folder=cwd, text=text, kind=kind)
        return None

    if hook_event in ("AfterAgent", "SessionEnd"):
        _post_event(url, token, "gemini-cli", session, "done",
                    name="Gemini CLI", folder=cwd, text="Done", kind="done")
        return None

    if hook_event == "Notification":
        _post_event(url, token, "gemini-cli", session, "idle",
                    name="Gemini CLI", folder=cwd, text="Idle", kind="other")
        return None

    return None


# ──────────────────────────────────────────────────────────────────────────────
# Main Entry Point
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) < 3:
        return 0  # FAIL OPEN: invalid usage, don't block

    agent = sys.argv[1].lower()
    hook_event = sys.argv[2]

    if agent not in ("claude", "codex", "gemini"):
        return 0  # FAIL OPEN

    data = _read_stdin()
    if not data:
        return 0  # FAIL OPEN: no input

    companion = _load_companion()
    if not companion:
        return 0  # FAIL OPEN: companion not running or file missing

    url, token = companion

    try:
        if agent == "claude":
            _handle_claude(hook_event, data, url, token)
        elif agent == "codex":
            decision = _handle_codex(hook_event, data, url, token)
            if decision:
                json.dump(decision, sys.stdout)
        elif agent == "gemini":
            decision = _handle_gemini(hook_event, data, url, token)
            if decision:
                json.dump(decision, sys.stdout)
    except Exception:
        pass  # FAIL OPEN: any exception -> exit 0, no output

    return 0


if __name__ == "__main__":
    sys.exit(main())