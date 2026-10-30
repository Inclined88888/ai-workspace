"""Tiers for v0.2 features, so Pro gating (feat/accounts-and-pro, account.allowed(feature)) can be wired at merge.

"free": everyone. "pro": paid plan. Other v0.2 branches add their own keys here; merge by taking the union.
"""

FEATURE_TIERS: dict[str, str] = {
    # feat/v02-packs
    "packs.trade": "pro",                 # quotes, invoices, reminder drafts, ServiceM8 / Xero push
    "packs.legal": "pro",                 # clause library, redlines, document checks, matter timeline
    "packs.study": "free",                # flashcards, worked maths, references: free for students
    "packs.study.lab_notebook": "pro",    # lab notebook entries and exports
}


def tier(feature: str) -> str:
    return FEATURE_TIERS.get(feature, "free")
