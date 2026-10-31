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


def daily(days: int = 30) -> dict:
    """Per-day model call trends for the Insights page: calls, failures, tokens, cost, latency, and who answered.

    Days are UTC dates with no gaps (a quiet day is a zero row), oldest first. p95 latency is computed per day from the
    successful calls only, so a provider timing out doesn't hide behind its own error. Cost is None for a day whose
    calls were all unpriced (local models, unknown prices), never a misleading $0.
    """
    from datetime import timedelta

    days = max(1, min(int(days), 90))
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    rows = db.all(
        "SELECT substr(ts, 1, 10) AS day, COUNT(*) AS calls, SUM(outcome != 'ok') AS failures, "
        "COALESCE(SUM(tokens_in), 0) AS tokens_in, COALESCE(SUM(tokens_out), 0) AS tokens_out, "
        "SUM(usd_cost) AS usd, SUM(usd_cost IS NULL) AS unpriced, "
        "CAST(AVG(CASE WHEN outcome = 'ok' THEN latency_ms END) AS INTEGER) AS avg_ms "
        f"{_CALLS} AND ts >= ? GROUP BY day", first.isoformat())
    lat: dict[str, list[int]] = {}
    for r in db.all(f"SELECT substr(ts, 1, 10) AS day, latency_ms {_CALLS} AND ts >= ? AND outcome = 'ok' ORDER BY day, latency_ms",
                    first.isoformat()):
        lat.setdefault(r["day"], []).append(r["latency_ms"])
    by_day = {r["day"]: r for r in rows}
    out = []
    for i in range(days):
        d = (first + timedelta(days=i)).isoformat()
        r = by_day.get(d) or {}
        ms = lat.get(d) or []
        out.append({"day": d, "calls": r.get("calls") or 0, "failures": r.get("failures") or 0,
                    "tokens_in": r.get("tokens_in") or 0, "tokens_out": r.get("tokens_out") or 0,
                    "usd": round(r["usd"], 6) if r.get("usd") is not None else None, "unpriced": r.get("unpriced") or 0,
                    "avg_ms": r.get("avg_ms") or 0, "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))] if ms else 0})
    providers = db.all(
        f"SELECT provider, COUNT(*) AS calls, SUM(outcome != 'ok') AS failures, "
        f"CAST(AVG(CASE WHEN outcome = 'ok' THEN latency_ms END) AS INTEGER) AS avg_ms {_CALLS} AND ts >= ? "
        "GROUP BY provider ORDER BY calls DESC", first.isoformat())
    channels = db.all(f"SELECT channel, COUNT(*) AS calls {_CALLS} AND ts >= ? GROUP BY channel ORDER BY calls DESC LIMIT 12",
                      first.isoformat())
    tot = {k: sum(x[k] for x in out) for k in ("calls", "failures", "tokens_in", "tokens_out", "unpriced")}
    priced = [x["usd"] for x in out if x["usd"] is not None]
    tot["usd"] = round(sum(priced), 6) if priced else None
    return {"days": out, "providers": providers, "channels": channels, "totals": tot}

