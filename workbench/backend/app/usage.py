"""Token and cost accounting, and the budget checks that pause agents before they overspend.

Prices are per million tokens in USD, set by you in Settings (models change too often to ship a price list).
OpenRouter returns the actual cost of every call, so OpenRouter models need no price entry.
"""

import json
from datetime import datetime, timezone

from . import db


def prices() -> dict[str, dict]:
    try:
        return json.loads(db.get_setting("model_prices", "{}") or "{}")
    except ValueError:
        return {}


def _price_cents(model: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    p = prices().get(model)
    if not p:
        return None
    return (prompt_tokens * float(p.get("in", 0)) + completion_tokens * float(p.get("out", 0))) / 1_000_000 * 100


def record(result: dict, *, channel: str, agent_id: int | None = None, conversation_id: int | None = None) -> float:
    u = result.get("usage") or {}
    pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
    if result.get("cost_usd") is not None:
        cents, priced = float(result["cost_usd"]) * 100, 1
    else:
        c = _price_cents(result["model"], pt, ct)
        cents, priced = (c, 1) if c is not None else (0.0, 0)
    db.run(
        "INSERT INTO usage(ts, model, agent_id, conversation_id, channel, prompt_tokens, completion_tokens, cost_cents, priced) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        db.now(), result["model"], agent_id, conversation_id, channel, pt, ct, cents, priced,
    )
    return cents


def month_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


def agent_month(agent_id: int) -> dict:
    r = db.one(
        "SELECT COALESCE(SUM(cost_cents), 0) AS cents, COALESCE(SUM(prompt_tokens + completion_tokens), 0) AS tokens, "
        "COALESCE(SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END), 0) AS unpriced FROM usage WHERE agent_id = ? AND ts >= ?",
        agent_id, month_start(),
    )
    return {"cents": round(r["cents"], 2), "tokens": r["tokens"], "unpriced_calls": r["unpriced"]}


def over_budget(agent: dict) -> str:
    """Empty string when the agent may run, otherwise the reason it must stop.

    Checked before every model call, so spend can exceed a limit by at most one call.
    """
    m = agent_month(agent["id"])
    if agent["monthly_budget_cents"] and m["cents"] >= agent["monthly_budget_cents"]:
        return f"Monthly budget reached (${m['cents'] / 100:.2f} of ${agent['monthly_budget_cents'] / 100:.2f})"
    if agent["monthly_token_limit"] and m["tokens"] >= agent["monthly_token_limit"]:
        return f"Monthly token limit reached ({m['tokens']:,} of {agent['monthly_token_limit']:,})"
    return ""


def summary() -> dict:
    start = month_start()
    total = db.one("SELECT COALESCE(SUM(cost_cents),0) c, COALESCE(SUM(prompt_tokens+completion_tokens),0) t FROM usage WHERE ts >= ?", start)
    by_model = db.all(
        "SELECT model, SUM(prompt_tokens + completion_tokens) AS tokens, ROUND(SUM(cost_cents), 2) AS cents, "
        "SUM(CASE WHEN priced = 0 THEN 1 ELSE 0 END) AS unpriced_calls FROM usage WHERE ts >= ? GROUP BY model ORDER BY tokens DESC",
        start,
    )
    by_channel = db.all("SELECT channel, ROUND(SUM(cost_cents), 2) AS cents, SUM(prompt_tokens + completion_tokens) AS tokens "
                        "FROM usage WHERE ts >= ? GROUP BY channel", start)
    return {"month_cents": round(total["c"], 2), "month_tokens": total["t"], "by_model": by_model, "by_channel": by_channel,
            "prices": prices()}
