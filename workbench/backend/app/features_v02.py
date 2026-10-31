"""Which plan each v0.2 feature belongs to (integration/v0.2: union of every v0.2 branch's tiers).

``FEATURE_TIERS`` is the single list. ``account.PRO_FEATURES`` and its labels are built from it (see account.py), so
``account.allowed(feature)`` gates every Pro id here. While billing is not live (#32's ``BILLING_LIVE`` off, or no cloud
URL in the build) ``account.allowed`` returns True for everything, so nothing is locked during testing.

Rules that always win: exports are never gated (data portability) and the Neon theme is free.
"""

from __future__ import annotations

from fastapi import HTTPException

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
    # feat/v02-core
    "projects": "free",               # Projects with memory: FREE_LIMITS["projects"] open projects on Free
    "projects_unlimited": "pro",      # more than FREE_LIMITS["projects"] open projects
    "action_history": "free",         # the timeline and one-tap undo are always free (undo is a safety feature)
    "action_history_export": "free",  # integration/v0.2: CSV / PDF export is FREE - exports are never gated
    "checked_answers": "pro",         # "Check this": every claim cited and re-verified
    # feat/v02-packs
    "packs.trade": "pro",                 # quotes, invoices, reminder drafts, ServiceM8 / Xero push
    "packs.legal": "pro",                 # clause library, redlines, document checks, matter timeline
    "packs.study": "free",                # flashcards, worked maths, references: free for students
    "packs.study.lab_notebook": "pro",    # new lab notebook entries (exporting existing entries is always free)
    # feat/team-lead
    "my_tasks": "free",                   # your own task list, always free
    "team_tasks": "team",                 # Team/Business: staff, assigning, tracking, nudges, plan, member links
}

# Plain-English names shown on the upgrade card and the "Your plan" page.
FEATURE_LABELS: dict[str, str] = {
    "automations_unlimited": "Unlimited automations",
    "jobs_board": "Background jobs board",
    "voice_conversation": "Hands-free Talk",
    "meeting_companion": "Meeting companion",
    "projects_unlimited": "Unlimited projects",
    "checked_answers": "Checked answers",
    "packs.trade": "Trade pack",
    "packs.legal": "Legal pack",
    "packs.study.lab_notebook": "Lab notebook",
    "team_tasks": "Team lead: assign and track your team",
}
LABELS = FEATURE_LABELS  # feat/v02-core name

FREE_LIMITS: dict[str, int] = {"automations": 3, "projects": 1}


def tier(feature: str) -> str:
    return FEATURE_TIERS.get(feature, "free")


def pro_features() -> set[str]:
    return {k for k, v in FEATURE_TIERS.items() if v == "pro"}


def _account_allowed(feature: str) -> bool:
    """account.allowed (PR #32): True for everything while billing isn't live, else the signed plan decides."""
    try:
        from . import account
    except ImportError:
        return True  # no accounts module in this build: nothing is gated
    fn = getattr(account, "allowed", None)
    return bool(fn(feature)) if callable(fn) else True


def allowed(feature: str) -> bool:
    """True when the user's plan includes the feature. Free features are always allowed."""
    if tier(feature) == "free":
        return True
    try:
        return _account_allowed(feature)
    except Exception:  # noqa: BLE001 - a licence check problem never locks the user out of their own app
        return True


def describe(feature: str) -> dict:
    return {"feature": feature, "tier": tier(feature), "allowed": allowed(feature)}


def team_features() -> set[str]:
    """feat/team-lead: features only the Team and Business plans include."""
    return {k for k, v in FEATURE_TIERS.items() if v == "team"}


def upgrade_detail(feature: str) -> dict:
    label = FEATURE_LABELS.get(feature, feature)
    if tier(feature) == "team":
        return {"error": "upgrade_required", "feature": feature, "label": label, "plan": "team",
                "message": f"{label} is part of the Team and Business plans. Open Your plan to set up your team."}
    return {"error": "upgrade_required", "feature": feature, "label": label,
            "message": f"{label} is part of Pro. Open Your plan to upgrade or sign in."}


def require(feature: str) -> None:
    """Raise the same 402 the accounts module uses, so the app shows its friendly upgrade card."""
    if not allowed(feature):
        raise HTTPException(402, upgrade_detail(feature))


def tiers() -> dict:
    return {"tiers": FEATURE_TIERS, "free_limits": FREE_LIMITS,
            "unlocked": {f: allowed(f) for f in FEATURE_TIERS}}
