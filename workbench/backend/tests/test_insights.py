"""Insights: per-day model call trends with no gaps, honest unpriced cost, and p95 from successful calls (Phase 3)."""

from datetime import datetime, timedelta, timezone


def test_daily_trends(client):
    from app import actions, db

    today = datetime.now(timezone.utc).date()
    yday = (today - timedelta(days=1)).isoformat()
    ids = []
    for ms in (100, 200, 300, 400, 5000):
        ids.append(actions.add("model_call", "ok", provider="InsightsP", model="ip/m", latency_ms=ms, tokens_in=10, tokens_out=5, usd_cost=0.01))
    ids.append(actions.add("model_call", "error", provider="InsightsP", model="ip/m", latency_ms=90000, failure_reason="timeout"))
    ids.append(actions.add("model_call", "ok", provider="LocalP", model="lp/m", latency_ms=50, tokens_in=1, tokens_out=1, usd_cost=None))
    db.run(f"UPDATE actions SET ts = ? WHERE id = ?", yday + "T12:00:00+00:00", ids[-1])
    try:
        r = client.get("/api/usage/daily?days=7")
        assert r.status_code == 200
        d = r.json()
        assert len(d["days"]) == 7 and d["days"][-1]["day"] == today.isoformat()  # no gaps, oldest first
        t = d["days"][-1]
        assert t["calls"] >= 6 and t["failures"] >= 1
        assert t["p95_ms"] < 90000  # the timeout doesn't count toward latency
        y = next(x for x in d["days"] if x["day"] == yday)
        assert y["calls"] >= 1 and y["unpriced"] >= 1
        p = next(x for x in d["providers"] if x["provider"] == "InsightsP")
        assert p["calls"] == 6 and p["failures"] == 1
        assert client.get("/api/usage/daily?days=500").status_code == 422
    finally:
        for i in ids:
            db.run("DELETE FROM actions WHERE id = ?", i)


def test_quiet_day_cost_is_unknown_not_zero(client):
    d = client.get("/api/usage/daily?days=90").json()
    oldest = d["days"][0]
    assert oldest["calls"] == 0 and oldest["usd"] is None
