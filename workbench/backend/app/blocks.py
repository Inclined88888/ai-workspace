"""Structured answer blocks: what a tool result looks like when shown to you, built by code from the tool's own data.

The model never writes blocks. They are derived here, after a tool ran, from its real result, so a table, chart or
card can only show values that exist in ROOKS. Each block is a small typed record (schema version VERSION) that the
frontend renders through an explicit allow-list of components; unknown blocks are skipped there. Fields are copied
one by one (never whole objects), strings are capped, and links are kept only when they are http or https.

Actions are not described here: the frontend decides which controls a block offers from its kind and record id
(open a mission, resume it, preview a file), and every control goes through the normal API, permissions and
approvals.
"""

import json
from urllib.parse import urlparse

VERSION = 1
MAX_ITEMS = 25


def _s(v, n: int = 300) -> str:
    return str(v)[:n] if v is not None else ""


def _url(v) -> str:
    try:
        u = urlparse(str(v or ""))
    except ValueError:
        return ""
    return str(v)[:1000] if u.scheme in ("http", "https") and u.netloc else ""


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def records(kind: str, items: list[dict], title: str = "") -> dict | None:
    items = [i for i in items if i][:MAX_ITEMS]
    return {"type": "records", "kind": kind, "title": title, "items": items} if items else None


def _task(t: dict) -> dict:
    return {"id": _num(t.get("id")), "title": _s(t.get("title")), "due": _s(t.get("due"), 10), "status": _s(t.get("status"), 10),
            "priority": _num(t.get("priority")), "project": _s(t.get("project"), 80)}


def _event(e: dict) -> dict:
    return {"id": _num(e.get("id")), "title": _s(e.get("title")), "start": _s(e.get("start"), 40), "end": _s(e.get("end"), 40),
            "all_day": bool(e.get("all_day")), "location": _s(e.get("location"), 120), "calendar": _s(e.get("calendar_name"), 60)}


def _doc(d: dict) -> dict:
    body = d.get("body") or d.get("preview") or ""
    return {"id": _num(d.get("id")), "title": _s(d.get("title")), "kind": _s(d.get("kind") or "note", 10),
            "excerpt": _s(body, 400), "updated_at": _s(d.get("updated_at"), 25)}


def _source(r: dict, n: int | None = None) -> dict | None:
    url = _url(r.get("url"))
    if not url:
        return None
    return {"n": n, "title": _s(r.get("title") or url, 200), "url": url, "site": (urlparse(url).hostname or "").removeprefix("www."),
            "snippet": _s(r.get("snippet") or r.get("content") or r.get("text"), 400), "date": _s(r.get("date"), 10)}


def _parse(content: str):
    try:
        return json.loads(content)
    except (TypeError, ValueError):
        return None


def _usage(d: dict) -> list[dict]:
    out: list[dict] = [{"type": "kpis", "items": [
        {"label": f"Spent in {d['days']} days", "value": d["total_cents"], "format": "cents"},
        {"label": "Model calls", "value": d["calls"], "format": "number"},
        {"label": "Tokens", "value": d["tokens"], "format": "number"},
        *([{"label": "Monthly limit", "value": d["monthly_limit_cents"], "format": "cents"}] if d.get("monthly_limit_cents") else [])]}]
    if d["unpriced_calls"]:
        out.append({"type": "notice", "tone": "warn", "text": f"{d['unpriced_calls']} call(s) had no known price, so the totals leave them out. "
                                                             "Set prices under Settings, Usage."})
    if len(d["by_day"]) >= 2:
        out.append({"type": "chart", "kind": "line", "title": "Spending by day", "format": "cents",
                    "points": [{"label": x["day"], "value": x["cost_cents"]} for x in d["by_day"]]})
    if d["by_model"]:
        out.append({"type": "chart", "kind": "bar", "title": "Spending by model", "format": "cents",
                    "points": [{"label": m["model"], "value": m["cost_cents"]} for m in d["by_model"][:8]]})
        out.append({"type": "table", "title": "By model", "columns": [
            {"key": "model", "label": "Model"}, {"key": "calls", "label": "Calls", "format": "number"},
            {"key": "tokens", "label": "Tokens", "format": "number"}, {"key": "cost_cents", "label": "Cost", "format": "cents"}],
            "rows": [{k: m[k] for k in ("model", "calls", "tokens", "cost_cents")} for m in d["by_model"][:MAX_ITEMS]]})
    if not d["calls"]:
        out = [{"type": "notice", "tone": "info", "text": f"No model use was recorded in the last {d['days']} days."}]
    return out


def _models(d: dict) -> list[dict]:
    if not d["models"]:
        return [{"type": "notice", "tone": "info", "text": "No models match. Connect a provider under Settings, AI models."}]
    rows = [{"model": m["model"], "provider": m["provider"], "cost": "free" if m["free"] else "paid", "where": "local" if m["local"] else "cloud",
             "tools": {True: "yes", False: "no"}.get(m["tools"], "unknown"), "context": m["context"],
             "price": (f"${m['price_in_per_million_usd']:g} in / ${m['price_out_per_million_usd']:g} out per 1M"
                       if m["price_in_per_million_usd"] is not None else ""), "default": "default" if m["ref"] == d["default"] else ""}
            for m in d["models"][:MAX_ITEMS]]
    return [{"type": "table", "title": f"{d['count']} model(s) from your providers", "columns": [
        {"key": "model", "label": "Model"}, {"key": "provider", "label": "Provider"}, {"key": "cost", "label": "Free or paid"},
        {"key": "where", "label": "Runs"}, {"key": "tools", "label": "Tools"}, {"key": "context", "label": "Context", "format": "number"},
        {"key": "price", "label": "Your price"}, {"key": "default", "label": ""}], "rows": rows,
        "note": "Free and tool support are as reported by the provider; prices are the ones you set in Settings."}]


def _project(d: dict) -> list[dict]:
    if not d.get("found"):
        return [{"type": "notice", "tone": "info", "text": _s(d.get("message"))}]
    out = [records("project", [{"id": d["id"], "name": _s(d["name"], 120), "goals": _s(d["goals"], 600),
                                "open_questions": len(d["open_questions"]), "open_tasks": len(d["open_tasks"]),
                                "missions": len(d["missions"]), "spend_cents": d["spend_cents_this_month"],
                                "budget_cents": d["budget_cents"] or None, "local_only": d["local_only"]}])]
    if d["open_questions"]:
        out.append({"type": "list", "title": "Open questions", "items": [_s(q["text"], 300) for q in d["open_questions"]]})
    if d["decisions"]:
        out.append({"type": "list", "title": "Recent decisions", "items": [_s(q["text"], 300) for q in d["decisions"]]})
    out.append(records("task", [_task(t) for t in d["open_tasks"]], "Open tasks"))
    out.append(records("mission", [{"id": m["id"], "goal": _s(m["goal"]), "status": _s(m["status"], 20)} for m in d["missions"]], "Missions"))
    out.append(records("research", [{"id": r["id"], "question": _s(r["question"]), "status": _s(r["status"], 20)} for r in d["research"]], "Research"))
    return [b for b in out if b]


def _table_analysis(d: dict) -> list[dict]:
    fmt = lambda v: v if isinstance(v, (int, float)) and not isinstance(v, bool) else None  # noqa: E731
    out: list = [{"type": "kpis", "items": [{"label": "Rows", "value": d["rows"], "format": "number"},
                                            {"label": "Columns", "value": d["columns"], "format": "number"},
                                            *[{"label": f"Total {c['column'][:40]}", "value": c["sum"], "format": "number"}
                                              for c in d["summary"] if c.get("type") == "number"][:2]]}]
    rows = []
    for c in d["summary"][:MAX_ITEMS]:
        if c.get("type") == "number":
            detail = f"sum {c['sum']:g}, mean {c['mean']:g}, median {c['median']:g}, min {c['min']:g}, max {c['max']:g}" if c.get("mean") is not None else ""
        else:
            detail = ", ".join(f"{t['value']} ({t['count']})" for t in c.get("top", [])[:3])
        rows.append({"column": _s(c["column"], 80), "type": _s(c.get("type"), 10), "filled": fmt(c["filled"]), "empty": fmt(c["empty"]),
                     "detail": _s(detail, 200)})
    out.append({"type": "table", "title": f"{_s(d['name'], 120)}{' / ' + _s(d['sheet'], 60) if d.get('sheet') else ''}: columns",
                "columns": [{"key": "column", "label": "Column"}, {"key": "type", "label": "Type"},
                            {"key": "filled", "label": "Filled", "format": "number"}, {"key": "empty", "label": "Empty", "format": "number"},
                            {"key": "detail", "label": "Figures"}], "rows": rows, "note": _s(d.get("note"), 300)})
    g = d.get("groups")
    if g and g.get("items"):
        out.append({"type": "chart", "kind": "bar", "title": f"{_s(g['value'], 60)} by {_s(g['by'], 60)}", "format": "number",
                    "points": [{"label": _s(i["label"], 60), "value": fmt(i["value"])} for i in g["items"][:12]]})
    out.append(records("file", [{"id": _num(d.get("file_id")), "name": _s(d.get("name"), 200)}]))
    return out


def from_tool(name: str, args: dict, ok: bool, content: str) -> list[dict]:
    """Blocks for one tool result, or [] when there is nothing better to show than the text."""
    if not ok:
        return []
    d = _parse(content)
    if d is None:
        return []
    try:
        blocks = _build(name, args or {}, d)
    except (KeyError, TypeError, AttributeError, ValueError):
        return []  # an unexpected shape shows as text, never as a half-built block
    return [b for b in blocks if b]


def _build(name: str, args: dict, d) -> list:
    if name == "list_tasks" and isinstance(d, list):
        return [records("task", [_task(t) for t in d], "Tasks")] if d else [{"type": "notice", "tone": "info", "text": "No open tasks."}]
    if name in ("create_task", "update_task") and isinstance(d, dict):
        return [records("task", [_task(d)], "Task created" if name == "create_task" else "Task updated")]
    if name == "get_agenda" and isinstance(d, dict):
        return [records("event", [_event(e) for e in d.get("events", [])], "Coming up"),
                records("task", [_task(t) for t in d.get("tasks_due", [])], "Due"),
                {"type": "kpis", "items": [{"label": "Open tasks", "value": d.get("open_tasks", 0), "format": "number"},
                                           {"label": "Overdue", "value": d.get("overdue", 0), "format": "number"}]}]
    if name == "create_event" and isinstance(d, dict):
        return [records("event", [_event(d)], "Event created")]
    if name in ("search_notes",) and isinstance(d, list):
        return [records("doc", [_doc(x) for x in d], "Notes found")]
    if name in ("read_note", "create_note", "append_to_note") and isinstance(d, dict):
        return [records("doc", [_doc(d)], {"read_note": "", "create_note": "Saved", "append_to_note": "Updated"}[name])]
    if name == "web_search" and isinstance(d, list):
        return [{"type": "sources", "items": [s for s in (_source(r, i) for i, r in enumerate(d, 1)) if s][:MAX_ITEMS]}]
    if name == "read_page" and isinstance(d, dict):
        s = _source({"url": d.get("url"), "title": d.get("url"), "text": d.get("text")})
        return [{"type": "sources", "items": [s]}] if s else []
    if name == "search_files" and isinstance(d, list):
        return [records("file", [{"id": _num(f.get("id")), "name": _s(f.get("name"), 200), "size": _num(f.get("size")),
                                  "excerpt": _s(f.get("excerpt"), 300)} for f in d], "Files")]
    if name == "read_file" and isinstance(d, dict):
        return [records("file", [{"id": _num(d.get("file_id")), "name": _s(d.get("name"), 200), "excerpt": _s(d.get("text"), 600)}])]
    if name == "search_email" and isinstance(d, list):
        return [records("email", [{"from": _s(m.get("from"), 120), "subject": _s(m.get("subject"), 200), "date": _s(m.get("date"), 40)} for m in d],
                        "Email")]
    if name == "read_email" and isinstance(d, dict):
        return [records("email", [{"from": _s(d.get("from"), 120), "subject": _s(d.get("subject"), 200), "date": _s(d.get("date"), 40),
                                   "excerpt": _s(d.get("body"), 400)}], "Email")]
    if name == "save_email_draft" and isinstance(d, dict):
        return [records("email", [{"subject": _s(args.get("subject"), 200), "to": _s(args.get("to"), 200), "draft": True}], "Draft saved, not sent")]
    if name in ("search_memory",) and isinstance(d, list):
        return [records("memory", [{"id": _num(m.get("id")), "content": _s(m.get("content"), 300), "kind": _s(m.get("kind"), 20)} for m in d],
                        "Remembered")]
    if name == "list_schedules" and isinstance(d, list):
        return [records("schedule", [{"id": _num(x.get("id")), "name": _s(x.get("name"), 120), "cron": _s(x.get("cron"), 40),
                                      "enabled": bool(x.get("enabled")), "next_run": _s(x.get("next_run"), 30)} for x in d], "Automations")]
    if name == "get_usage" and isinstance(d, dict):
        return _usage(d)
    if name == "list_models" and isinstance(d, dict):
        return _models(d)
    if name == "project_status" and isinstance(d, dict):
        return _project(d)
    if name == "list_missions" and isinstance(d, dict):
        return [records("mission", [{"id": m["id"], "goal": _s(m["goal"]), "status": _s(m["status"], 20), "steps": m["steps"],
                                     "steps_done": m["steps_done"], "workers": m["workers"], "workers_active": m["workers_active"],
                                     "used_cents": m["used_cents"], "budget_cents": m["budget_cents"] or None} for m in d["missions"]], "Missions")
                or {"type": "notice", "tone": "info", "text": "No missions yet."}]
    # Work started from the conversation shows as a job card that keeps itself current and offers Approve or Stop
    if name == "start_mission" and isinstance(d, dict):
        return [{"type": "job", "kind": "mission", "id": _num(d.get("id")), "title": _s(d.get("goal")), "status": _s(d.get("status"), 20), "live": True}]
    if name == "analyze_table" and isinstance(d, dict):
        return _table_analysis(d)
    if name == "run_coding_agent" and isinstance(d, dict):
        return [{"type": "job", "kind": "coding", "id": _num(d.get("id")), "title": _s(args.get("task")), "status": _s(d.get("status"), 20),
                 "agent": _s(d.get("agent"), 40), "folder": _s(d.get("folder"), 300), "live": True}]
    if name == "start_research" and isinstance(d, dict):
        return [{"type": "job", "kind": "research", "id": _num(d.get("id")), "title": _s(d.get("question")), "status": _s(d.get("status"), 20),
                 "live": True}]
    return []


def for_conversation(rows: list[dict]) -> dict[int, list[dict]]:
    """Blocks for every tool message in a stored conversation (message id -> blocks), rebuilt from what was saved."""
    args: dict[str, tuple[str, dict]] = {}
    out: dict[int, list[dict]] = {}
    for r in rows:
        if r["role"] == "assistant" and r.get("tool_calls_raw"):
            for c in json.loads(r["tool_calls_raw"]):
                try:
                    a = json.loads(c["function"].get("arguments") or "{}")
                except (TypeError, ValueError):
                    a = {}
                args[c.get("id", "")] = (c["function"]["name"], a if isinstance(a, dict) else {})
        elif r["role"] == "tool":
            name, a = args.get(r.get("tool_call_id", ""), (r.get("name", ""), {}))
            ok = not (r["content"] or "").startswith(("Error", "Not ", "Unknown tool", "Invalid arguments"))
            b = from_tool(name or r.get("name", ""), a, ok, r["content"])
            if b:
                out[r["id"]] = b
    return out
