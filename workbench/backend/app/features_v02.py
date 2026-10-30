"""Which plan each v0.2 feature belongs to (integration/v0.2: union of every v0.2 branch's tiers).

``FEATURE_TIERS`` is the single list. ``account.PRO_FEATURES`` and its labels are built from it (see account.py), so
``account.allowed(feature)`` gates every Pro id here. While billing is not live (#32's ``BILLING_LIVE`` off, or no cloud
URL in the build) ``account.allowed`` returns True for everything, so nothing is locked during testing.

Rules that always win: exports are never gated (data portability) and the Neon theme is free.
"""

FEATURE_TIERS: dict[str, str] = {
    # feat/v02-automations
    "automations": "free",            # trigger automations, up to FREE_LIMITS["automations"] on Free
    "automations_unlimited": "pro",   # more than the Free number of automations
    "jobs_board": "pro",              # several background jobs at once, the board, phone/desktop notifications
    "live_documents": "free",         # editable docs, sheets and charts inside replies, with export
    # feat/v02-anywhere-voice
    "ask_anywhere": "free",           # global shortcut, floating ask bar, paste back
    "voice_dictation": "free",        # speak instead of typing, anywhere there is a text box
    "voice_conversation": "pro",      # hands-free back-and-forth, wake word, spoken approvals
    "meeting_companion": "pro",       # record or import a meeting, notes, actions, follow-up draft
}

# Plain-English names shown on the upgrade card and the "Your plan" page.
FEATURE_LABELS: dict[str, str] = {
    "automations_unlimited": "Unlimited automations",
    "jobs_board": "Background jobs board",
    "voice_conversation": "Hands-free Talk",
    "meeting_companion": "Meeting companion",
}

FREE_LIMITS = {"automations": 3}


def tier(feature: str) -> str:
    return FEATURE_TIERS.get(feature, "free")


def pro_features() -> set[str]:
    return {k for k, v in FEATURE_TIERS.items() if v == "pro"}


def allowed(feature: str) -> bool:
    """True when the user's plan includes the feature. Free features are always allowed."""
    if tier(feature) == "free":
        return True
    try:
        from . import account
    except ImportError:
        return True
    fn = getattr(account, "allowed", None)
    if not callable(fn):
        return True
    try:
        return bool(fn(feature))
    except Exception:  # noqa: BLE001 - a licence check problem never locks the user out of their own app
        return True


def describe(feature: str) -> dict:
    return {"feature": feature, "tier": tier(feature), "allowed": allowed(feature)}
