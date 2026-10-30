"""Which plan each v0.2 feature belongs to (feat/v02-anywhere-voice).

PR #32 (feat/accounts-and-pro) adds ``account.allowed(feature)``. This branch does not depend on it: the tiers are
declared here so they can be wired at merge. Until then :func:`allowed` lets everything through, exactly like the
"billing off" state of PR #32, so nothing is locked during testing.

Other v0.2 branches add their own keys to FEATURE_TIERS; on merge, union the dicts.
"""

FEATURE_TIERS: dict[str, str] = {
    # feat/v02-anywhere-voice
    "ask_anywhere": "free",          # global shortcut, floating ask bar, paste back
    "voice_dictation": "free",       # speak instead of typing, anywhere there is a text box
    "voice_conversation": "pro",     # hands-free back-and-forth, wake word, spoken approvals
    "meeting_companion": "pro",      # record or import a meeting, notes, actions, follow-up draft
}


def tier(feature: str) -> str:
    return FEATURE_TIERS.get(feature, "free")


def allowed(feature: str) -> bool:
    """True when the signed-in plan includes the feature. Free features are always allowed."""
    if tier(feature) == "free":
        return True
    try:
        from . import account  # type: ignore[attr-defined]  # PR #32
    except ImportError:
        return True
    try:
        return bool(account.allowed(feature))
    except Exception:  # noqa: BLE001  # never break a feature because the plan check failed; PR #32 fails open offline
        return True


def describe(feature: str) -> dict:
    return {"feature": feature, "tier": tier(feature), "allowed": allowed(feature)}
