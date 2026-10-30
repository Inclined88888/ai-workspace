"""Plan tiers for the v0.2 core features (feat/v02-core): Projects, Action history and Checked answers.

PR #32 (accounts and Pro) adds `account.allowed(feature)`. This module does not depend on it: it declares each
feature's tier here and asks `account.allowed` only when that module exists. Without it (or while billing is not
live) everything is unlocked, exactly like the rest of the app during the beta.

Merge note: add the "pro" ids below to account.PRO_FEATURES / LABELS so they show on the Your plan page.
"""

from __future__ import annotations

import importlib

from fastapi import HTTPException

# feature id -> "free" | "pro"
FEATURE_TIERS: dict[str, str] = {
    "projects": "free",                 # Projects with memory: one project free
    "projects_unlimited": "pro",        # more than FREE_LIMITS["projects"] active projects
    "action_history": "free",           # the timeline and one-tap undo are always free (undo is a safety feature)
    "action_history_export": "pro",     # CSV / PDF export of the timeline
    "checked_answers": "pro",           # "Check this": every claim cited and re-verified
}
FREE_LIMITS: dict[str, int] = {"projects": 1}
LABELS: dict[str, str] = {
    "projects_unlimited": "Unlimited projects",
    "action_history_export": "Export your history (CSV and PDF)",
    "checked_answers": "Checked answers",
}


def _account_allowed(feature: str) -> bool:
    try:
        account = importlib.import_module("app.account")
    except ImportError:
        return True  # no accounts module in this build: nothing is gated
    fn = getattr(account, "allowed", None)
    return bool(fn(feature)) if fn else True


def allowed(feature: str) -> bool:
    if FEATURE_TIERS.get(feature, "free") == "free":
        return True
    try:
        return _account_allowed(feature)
    except Exception:  # noqa: BLE001 - a broken plan check must never lock someone out of their own app
        return True


def upgrade_detail(feature: str) -> dict:
    label = LABELS.get(feature, feature)
    return {"error": "upgrade_required", "feature": feature, "label": label,
            "message": f"{label} is part of Pro. Open Your plan to upgrade or sign in."}


def require(feature: str) -> None:
    """Raise the same 402 the accounts module uses, so the app shows its friendly upgrade card."""
    if not allowed(feature):
        raise HTTPException(402, upgrade_detail(feature))


def tiers() -> dict:
    return {"tiers": FEATURE_TIERS, "free_limits": FREE_LIMITS,
            "unlocked": {f: allowed(f) for f in FEATURE_TIERS}}
