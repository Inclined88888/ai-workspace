#!/usr/bin/env python3
"""Evaluate tool use through a running Workbench; credentials are the same as cli/ws."""

import argparse
import getpass
import json
import os
from pathlib import Path
import urllib.error
import urllib.request

CONFIG = Path.home() / ".config/ai-workspace/cli.json"
# Fixed prompts make runs comparable. Each task starts a new conversation.
TASKS = [
    *[(f"web-{i}", f"Search the web for {q}, read one result with read_page, then summarize it in two sentences.",
       ("web_search", "read_page")) for i, q in enumerate(("Python tutorial", "Ollama documentation", "SQLite documentation", "SearXNG documentation"), 1)],
    *[(f"task-{i}", f"Create a task titled '[local eval] {title}'.", ("create_task",))
      for i, title in enumerate(("Water plants", "Review notes", "Plan meals", "Back up files"), 1)],
    *[(f"agenda-{days}", f"Get my agenda for the next {days} days.", ("get_agenda",)) for days in (1, 7, 14)],
    *[(f"notes-{q}", f"Search my notes for {q} and tell me what you find.", ("search_notes",)) for q in ("plan", "project", "meeting")],
    *[(f"event-{i}", f"Create a local all-day calendar event titled '[local eval] {title}' on 2030-01-{i:02d}.", ("create_event",))
      for i, title in enumerate(("Reading", "Walking", "Planning"), 1)],
    *[(f"memory-{i}", f"Remember this fact: [local eval] {fact}", ("remember",))
      for i, fact in enumerate(("My evaluation colour is blue.", "My evaluation plant is basil.", "My evaluation day is Friday."), 1)],
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # do not forward the terminal token to another endpoint


def request(cfg: dict, path: str, body: dict | None = None):
    req = urllib.request.Request(cfg["url"].rstrip("/") + "/api" + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json", "X-Workbench": "1"})
    return urllib.request.build_opener(NoRedirect).open(req, timeout=600)


def chat(cfg: dict, model: str, prompt: str) -> list[dict]:
    with request(cfg, "/assistant/chat", {"message": prompt, "model": model, "channel": "cli"}) as response:
        return [json.loads(line[5:]) for raw in response if (line := raw.decode().strip()).startswith("data:")]


def score(events: list[dict], expected: tuple[str, ...]) -> dict:
    calls = [e for e in events if e.get("type") == "tool_call"]
    results = [e for e in events if e.get("type") == "tool_result"]
    good = [e["name"] for e in results if e.get("ok")]
    remaining = iter(good)
    matched = all(any(name == want for name in remaining) for want in expected)
    errors = [e.get("error", "Unknown error") for e in events if e.get("error")]
    final = next((e.get("content", "") for e in events if e.get("type") == "message"), "")
    done = any(e.get("type") == "done" for e in events)
    return {"passed": matched and bool(final.strip()) and done and not errors,
            "calls": len(calls), "successful_calls": len(good), "expected": list(expected),
            "successful_tools": good, "errors": errors, "conversation_id": next((e.get("conversation_id") for e in events if e.get("type") == "start"), None)}


def evaluate(model: str, send, tasks=TASKS) -> dict:
    rows = []
    for name, prompt, expected in tasks:
        try:
            row = score(send(model, prompt), expected)
        except (OSError, ValueError) as e:
            row = score([{"error": str(e)}], expected)
        rows.append({"task": name, **row})
    calls = sum(r["calls"] for r in rows)
    good = sum(r["successful_calls"] for r in rows)
    passed = sum(r["passed"] for r in rows)
    return {"model": model, "tasks_passed": passed, "tasks_total": len(rows),
            "task_success_rate": passed / len(rows) if rows else 0,
            "tool_calls": calls, "successful_tool_calls": good, "tool_call_success_rate": good / calls if calls else 0,
            "tasks": rows}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="Workbench URL (defaults to ws login URL)")
    parser.add_argument("--model", action="append", required=True, help="provider_id/model; repeat to compare models")
    parser.add_argument("--output", type=Path, help="write the detailed JSON report")
    args = parser.parse_args(argv)
    try:
        cfg = json.loads(CONFIG.read_text())
    except (OSError, ValueError):
        cfg = {}
    saved_url = cfg.get("url", "")
    cfg["url"] = args.url or saved_url or "http://localhost:3100"
    # A different URL must not silently receive the saved token.
    if args.url and args.url.rstrip("/") != saved_url.rstrip("/"):
        cfg.pop("token", None)
    cfg["token"] = os.getenv("WORKBENCH_CLI_TOKEN") or cfg.get("token") or getpass.getpass("Terminal token: ").strip()
    try:
        with request(cfg, "/memories") as response:
            response.read()
    except (OSError, ValueError) as e:
        parser.exit(1, f"Cannot authenticate with Workbench: {e}\n")
    print("Runs 20 tasks per model; creates [local eval] tasks, events and memories. Use a test workspace.")
    reports = []
    for model in args.model:
        report = evaluate(model, lambda model, prompt: chat(cfg, model, prompt))
        reports.append(report)
        print(f"{model}: tasks {report['tasks_passed']}/{report['tasks_total']} ({report['task_success_rate']:.1%}); "
              f"tool calls {report['successful_tool_calls']}/{report['tool_calls']} ({report['tool_call_success_rate']:.1%})", flush=True)
    if args.output:
        args.output.write_text(json.dumps(reports, indent=2) + "\n")
    return 0 if all(r["tasks_passed"] == r["tasks_total"] for r in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
