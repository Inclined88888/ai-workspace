"""integration/v0.2 wire-up: v0.2 tiers feed account.PRO_FEATURES, Neon is free, exports are never gated, and the
unattended runners (site watchers, automations, jobs, learn runs, background learning) ask account.allowed()."""

import asyncio

import pytest
from fastapi import HTTPException

from tests.test_account import FakeCloud, cloud  # noqa: F401 - reuse the fake signed-entitlement cloud


def test_v02_pro_ids_are_account_pro_features(client):
    from app import account, features_v02

    pro = features_v02.pro_features()
    assert {"jobs_board", "voice_conversation", "meeting_companion", "projects_unlimited", "checked_answers",
            "packs.trade", "packs.legal", "packs.study.lab_notebook", "automations_unlimited"} <= pro
    for f in pro:
        assert f in account.PRO_FEATURES and account.LABELS[f]
    assert features_v02.tier("action_history_export") == "free"  # exports are never gated
    assert "neon" not in account.PREMIUM_THEMES and "cherry" in account.PREMIUM_THEMES


def test_everything_unlocked_while_billing_is_off(client, monkeypatch):
    from app import account, features_v02

    monkeypatch.delenv("AIW_CLOUD_URL", raising=False)
    monkeypatch.delenv("AIW_ENFORCE_PLANS", raising=False)
    assert not account.billing_live()
    assert all(features_v02.allowed(f) for f in features_v02.FEATURE_TIERS)
    s = client.get("/api/account").json()
    assert {p["id"] for p in s["pro_features"]} >= features_v02.pro_features()
    assert all(p["unlocked"] for p in s["pro_features"])


def test_free_plan_gates_v02_and_paid_plan_unlocks_even_with_an_older_token(client, cloud, monkeypatch):  # noqa: F811
    from app import account, features_v02

    client.get("/api/account")  # learns billing_live from the cloud
    assert account.billing_live()
    assert not features_v02.allowed("checked_answers") and not features_v02.allowed("packs.trade")
    assert features_v02.allowed("packs.study") and features_v02.allowed("ask_anywhere")
    assert client.get("/api/history/export.csv").status_code == 200  # exports never gated, even on Free
    assert client.get("/api/history").json()["export_allowed"] is True
    # Sign in to Pro: the fake cloud's Pro token lists only the v0.1 ids, the signed plan still unlocks every v0.2 id
    client.post("/api/account/sign-in/email", json={"email": "amy@example.com"})
    client.post("/api/account/sign-in/code", json={"code": "123456"})
    cloud.plan = "pro"
    client.post("/api/account/refresh")
    assert all(features_v02.allowed(f) for f in features_v02.pro_features())
    client.post("/api/account/sign-out")


def test_unattended_runners_respect_the_plan(client, monkeypatch):
    from app import account, automations, jobs, sites
    from app.adapt import hooks as adapt_hooks, playbooks

    monkeypatch.setattr(account, "allowed", lambda f: False)
    # scheduled site watcher: paused, never checks the site
    called = []
    monkeypatch.setattr(sites, "check", lambda *a, **k: called.append(a))
    sites.ensure()
    out = asyncio.run(sites.run_watch({"id": 0, "site_id": 0, "cron": "0 7 * * *", "rule": "{}"}))
    assert out.startswith("Paused:") and not called
    # learn runs (Show me how replays from routines, automations and jobs)
    with pytest.raises(HTTPException) as e:
        asyncio.run(playbooks.run(1, {}, "automation"))
    assert e.value.status_code == 402
    # background learning after a chat turn does nothing on Free
    mined = []
    monkeypatch.setattr(adapt_hooks.knowledge, "mine_conversation", lambda cid: mined.append(cid))
    asyncio.run(adapt_hooks.after_turn(1, "chat"))
    assert not mined
    # a computer-use job queued by an automation fails politely instead of driving the screen
    jobs.ensure() if hasattr(jobs, "ensure") else None
    j = jobs.create("computer", "open the invoice", "Computer job", {}, None, False)
    done = asyncio.run(jobs.run_job(j["id"]))
    assert done["status"] == "failed" and "Pro" in done["detail"]
    # automations: only the Free number run
    monkeypatch.setattr(automations, "limit", lambda: 1)
    assert automations._within_plan([1, 2, 3]) == [1]


def test_history_wrapper_coexists_with_other_set_setting_wrappers(client):
    from app import db, history

    seen = []
    base = db.set_setting

    def other(key, value):
        seen.append(key)
        return base(key, value)

    db.set_setting = other  # another feature wraps after history did
    try:
        history.install()  # idempotent: does not replace the other wrapper
        assert db.set_setting is other
        db.set_setting("timezone", "Australia/Brisbane")
        assert "timezone" in seen
    finally:
        db.set_setting = base
