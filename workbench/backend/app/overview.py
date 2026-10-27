"""Read-only tools that describe the workspace itself: spending, connected models, a project's state, missions.

They return verified data straight from the database (or the providers' own model lists), so the answer can show
real totals, tables and charts. Nothing here estimates or invents a figure: where a value is unknown it says so.
"""

import json
from datetime import datetime, timedelta, timezone

from . import db


def get_usage(days: int = 30) -> dict:
    """Model spending and calls over the last `days` days, by day and by model. Costs are only as complete as the
    prices known (OpenRouter reports real cost; other providers need prices in Settings)."""
    days = max(1, min(int(days or 30), 366))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.all("SELECT substr(ts, 1, 10) AS day, model, cost_cents, prompt_tokens + completion_tokens AS tokens, priced "
                  "FROM usage WHERE ts >= ? ORDER BY ts", since)
    by_day: dict[str, float] = {}
    by_model: dict[str, dict] = {}
    for r in rows:
        by_day[r["day"]] = by_day.get(r["day"], 0.0) + r["cost_cents"]
        m = by_model.setdefault(r["model"], {"model": r["model"], "cost_cents": 0.0, "calls": 0, "tokens": 0, "unpriced_calls": 0})
        m["cost_cents"] += r["cost_cents"]
        m["calls"] += 1
        m["tokens"] += r["tokens"]
        m["unpriced_calls"] += 0 if r["priced"] else 1
    limit = float(db.get_setting("monthly_limit_cents", "0") or 0)
    return {"days": days, "since": since[:10], "total_cents": round(sum(r["cost_cents"] for r in rows), 4), "calls": len(rows),
            "tokens": sum(r["tokens"] for r in rows), "unpriced_calls": sum(1 for r in rows if not r["priced"]),
            "monthly_limit_cents": limit or None,
            "by_day": [{"day": d, "cost_cents": round(c, 4)} for d, c in sorted(by_day.items())],
            "by_model": sorted(({**m, "cost_cents": round(m["cost_cents"], 4)} for m in by_model.values()), key=lambda m: -m["cost_cents"])}


def list_models(query: str = "") -> dict:
    """The models available from your connected providers, with what is known about each: free or paid, local or
    cloud, tool support, context size, and your price if you set one."""
    from . import llm, usage

    prices = usage.prices()
    q = (query or "").lower().strip()
    out = []
    for m in llm.list_models():
        if q and q not in m["model"].lower() and q not in m["provider"].lower():
            continue
        price = prices.get(m["model"])
        out.append({"ref": m["ref"], "model": m["model"], "provider": m["provider"], "free": bool(m.get("free")),
                    "local": llm.is_local(m["ref"]), "tools": m.get("tools"), "context": m.get("ctx"),
                    "price_in_per_million_usd": price.get("in") if price else None, "price_out_per_million_usd": price.get("out") if price else None})
    ref, _ = llm.effective_default()
    return {"default": ref, "count": len(out), "models": out[:60], "truncated": len(out) > 60}


def project_status(project: str = "") -> dict:
    """Where a project stands: goals, open questions, recent decisions, open tasks, missions, research and spend.
    project is a name (or part of one); empty means your most recently updated active project."""
    from . import projects

    q = (project or "").strip()
    row = (db.one("SELECT id FROM projects WHERE status = 'active' AND lower(name) LIKE ? ORDER BY updated_at DESC LIMIT 1", f"%{q.lower()}%")
           if q else db.one("SELECT id FROM projects WHERE status = 'active' ORDER BY updated_at DESC LIMIT 1"))
    if not row:
        return {"found": False, "message": f"No active project matches '{q}'." if q else "There are no active projects yet."}
    o = projects.overview(row["id"])
    return {"found": True, "id": o["id"], "name": o["name"], "goals": o["goals"], "description": o["description"][:400],
            "budget_cents": o["budget_cents"], "spend_cents_this_month": o["spend_cents"], "local_only": bool(o["local_only"]),
            "open_questions": [{"id": n["id"], "text": n["text"]} for n in o["questions"] if not n["resolved"]][:10],
            "decisions": [{"id": n["id"], "text": n["text"], "at": n["created_at"]} for n in o["decisions"]][:8],
            "open_tasks": [t for t in o["tasks"] if t["status"] != "done"][:15],
            "missions": o["missions"][:8], "research": o["research"][:5]}


def list_missions(status: str = "") -> dict:
    """Missions with their progress and workers. status filters (running, review, paused, done, failed...)."""
    where, args = ("WHERE status = ?", [status]) if status else ("", [])
    out = []
    for m in db.all(f"SELECT id, goal, status, plan, used_cents, budget_cents, error, updated_at FROM missions {where} ORDER BY id DESC LIMIT 15", *args):
        plan = json.loads(m.pop("plan") or "[]")
        w = db.one("SELECT COUNT(*) AS n, SUM(status = 'done') AS done, SUM(status IN ('running', 'queued', 'waiting_approval', 'in_review')) AS active "
                   "FROM worker_tasks WHERE mission_id = ?", m["id"])
        out.append({**m, "steps": len(plan), "steps_done": sum(1 for s in plan if s["status"] == "done"),
                    "workers": w["n"] or 0, "workers_done": w["done"] or 0, "workers_active": w["active"] or 0})
    return {"missions": out}
