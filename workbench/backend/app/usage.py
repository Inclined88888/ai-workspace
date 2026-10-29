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


def price_usd(model_ref: str, prompt_tokens: int, completion_tokens: int) -> float | None:
    """USD for one call from Settings prices (per million tokens), or None when no price is set.

    Prices may be keyed by the model ref ("3/openai/gpt-x") or by the bare model name ("openai/gpt-x").
    """
    table = prices()
    p = table.get(model_ref) or table.get(model_ref.partition("/")[2])
    if not p:
        return None
    return (prompt_tokens * float(p.get("in", 0)) + completion_tokens * float(p.get("out", 0))) / 1_000_000


def month_start() -> str:
    n = datetime.now(timezone.utc)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds")


_CALLS = "FROM actions WHERE kind = 'model_call'"


def agent_month(agent_id: int) -> dict:
    r = db.one(
        "SELECT COALESCE(SUM(usd_cost), 0) * 100 AS cents, COALESCE(SUM(tokens_in + tokens_out), 0) AS tokens, "
        f"COALESCE(SUM(usd_cost IS NULL), 0) AS unpriced {_CALLS} AND agent_id = ? AND ts >= ?",
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
    total = db.one(f"SELECT COALESCE(SUM(usd_cost), 0) * 100 AS c, COALESCE(SUM(tokens_in + tokens_out), 0) AS t {_CALLS} AND ts >= ?", start)
    by_model = db.all(
        "SELECT model, SUM(tokens_in + tokens_out) AS tokens, ROUND(COALESCE(SUM(usd_cost), 0) * 100, 2) AS cents, "
        f"SUM(usd_cost IS NULL) AS unpriced_calls {_CALLS} AND ts >= ? GROUP BY model ORDER BY tokens DESC",
        start,
    )
    by_channel = db.all("SELECT channel, ROUND(COALESCE(SUM(usd_cost), 0) * 100, 2) AS cents, SUM(tokens_in + tokens_out) AS tokens "
                        f"{_CALLS} AND ts >= ? GROUP BY channel", start)
    return {"month_cents": round(total["c"], 2), "month_tokens": total["t"], "by_model": by_model, "by_channel": by_channel,
            "prices": prices()}
