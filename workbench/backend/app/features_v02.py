"""Plan tiers for the v0.2 features (feat/v02-automations and the other v0.2 branches).

Each feature names the plan it needs. PR #32 (accounts and Pro) adds `account.allowed(feature)`; until that is merged,
or while billing is off, everything is allowed so nothing breaks during testing. Wire-up at merge: `allowed()` below
already calls `account.allowed` when that module exists.
"""

FEATURE_TIERS = {
    # feat/v02-automations
    "automations": "free",            # trigger automations, up to FREE_LIMITS["automations"] on Free
    "automations_unlimited": "pro",   # more than the Free number of automations
    "jobs_board": "pro",              # several background jobs at once, the board, phone/desktop notifications
    "live_documents": "free",         # editable docs, sheets and charts inside replies, with export
}

FREE_LIMITS = {"automations": 3}


def allowed(feature: str) -> bool:
    """True when the user's plan includes the feature. Without the accounts module everything is allowed."""
    try:
        from . import account  # type: ignore[attr-defined]  # added by feat/accounts-and-pro (PR #32)
    except ImportError:
        return True
    fn = getattr(account, "allowed", None)
    if not callable(fn):
        return True
    try:
        return bool(fn(feature))
    except Exception:  # noqa: BLE001 - a licence check problem never locks the user out of their own app
        return True


def tier(feature: str) -> str:
    return FEATURE_TIERS.get(feature, "free")
