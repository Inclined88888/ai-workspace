"""Professional workspaces: say what kind of work you do once, and the app, the terminal and every chat app work that way.

A workspace is a setting, not a silo. There is still one memory, one mailbox list, one set of chats and skills ("one
brain"). Choosing a workspace:

* adds the pack that kind of work needs (Trade, Legal, Study & research), through the pack's own security gate, and
  only after the plan check. If the pack cannot be added nothing changes;
* tells the assistant how that work is done (its working style), in Chat, the terminal and chat apps alike, because
  ``context()`` is read by ``assistant.system_prompt``, which every channel uses;
* sets the usual project kind for new projects (a job, a legal matter, a course).

Going back to General never removes a pack: the pack's quotes, clauses, cards and notes are the user's data. Removing a
pack stays its own step (``rooks pack remove``).

The choice is stored in the settings table under ``SETTING``. An unknown stored value reads as General.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from . import db
from .security import require_user

SETTING = "professional_workspace"
DEFAULT = "general"

# Plain words only on anything the user sees (AGENTS.md). ``instructions`` goes to the model, never to a screen.
WORKSPACES: dict[str, dict] = {
    "general": {
        "name": "General",
        "for": "Anyone. A personal assistant for life and work, with nothing extra added.",
        "pack": None,
        "project_kind": "other",
        "starters": ["Plan my week", "Sort my inbox", "Help me write a reply"],
        "instructions": "",
    },
    "trade": {
        "name": "Trade",
        "for": "Tradies, builders, cleaners, landscapers and any service business.",
        "pack": "trade",
        "project_kind": "job",
        "starters": ["Quote this job from a photo", "Turn my last quote into an invoice", "Draft a friendly payment reminder"],
        "instructions": (
            "The user runs a trade or service business. Treat each job or site as a project of kind 'job'. "
            "Quotes, invoices and payment reminders come from the trade_ tools (trade_draft_quote, trade_make_invoice, "
            "trade_make_pdf, trade_reminder_drafts); use them instead of writing figures by hand. Labour, materials, "
            "markup, tax and payment terms come from the user's business settings: never invent a rate, a price or a "
            "tax number. Reminders are drafts the user sends themselves. Keep messages to customers short and plain."
        ),
    },
    "legal": {
        "name": "Legal",
        "for": "Lawyers, paralegals, contract managers, property and HR teams.",
        "pack": "legal",
        "project_kind": "matter",
        "starters": ["Check this contract", "Find our standard confidentiality clause", "Build a timeline from these documents"],
        "instructions": (
            "The user does legal work. Treat each matter as a project of kind 'matter'. Use the legal_ tools "
            "(legal_check_document, legal_find_clause, legal_timeline) for document checks, clauses and timelines. "
            "Every answer about the law or a document says where in the document it came from, and says it is not "
            "legal advice. Citation checks cover the format only: never state that a case or section says something "
            "you have not read. Never send anything to another party; prepare drafts."
        ),
    },
    "study": {
        "name": "Study and research",
        "for": "Students, teachers, researchers and scientists.",
        "pack": "study",
        "project_kind": "course",
        "starters": ["Make flashcards from these notes", "Check my working on this problem", "Add an entry to my lab notebook"],
        "instructions": (
            "The user studies, teaches or does research. Treat each course, subject or study as a project of kind "
            "'course' or 'research'. Use the study_ tools for flashcards, worked maths (checked in the sandbox) and "
            "the lab notebook. Explain steps so the user learns them, cite sources for every claim, and never "
            "invent a reference, a DOI or a result."
        ),
    },
}


def _public(wid: str, active: str) -> dict:
    w = WORKSPACES[wid]
    out = {"id": wid, "name": w["name"], "for": w["for"], "pack": w["pack"], "project_kind": w["project_kind"],
           "starters": list(w["starters"]), "active": wid == active, "tier": "free", "allowed": True, "pack_added": False}
    if w["pack"]:
        from .packs import registry

        feature = f"packs.{w['pack']}"
        out["tier"] = registry.feature_tier(feature)
        out["allowed"] = registry.allowed(feature)
        out["pack_added"] = registry.is_installed(w["pack"])
    return out


def active_id() -> str:
    try:
        wid = db.get_setting(SETTING, DEFAULT) or DEFAULT
    except Exception:  # noqa: BLE001 - read inside every chat turn; never break one
        return DEFAULT
    return wid if wid in WORKSPACES else DEFAULT


def catalogue() -> list[dict]:
    from .packs import store

    store.ensure()
    cur = active_id()
    return [_public(wid, cur) for wid in WORKSPACES]


def current() -> dict:
    from .packs import store

    store.ensure()
    return _public(active_id(), active_id())


def choose(wid: str) -> dict:
    """Make ``wid`` the active workspace. Adds its pack first; if the plan or the pack's security gate says no, the
    active workspace stays what it was and the error (402 upgrade card, 409 gate refusal) reaches the caller."""
    if wid not in WORKSPACES:
        raise HTTPException(404, "There is no workspace with that name.")
    pack = WORKSPACES[wid]["pack"]
    if pack:
        from .packs import registry, store

        store.ensure()
        registry.require_feature(f"packs.{pack}")  # 402 before anything is installed
        if not registry.is_installed(pack):
            registry.add(pack)  # all skills through the gate, or none; raises on refusal
    db.set_setting(SETTING, wid)
    return current()


def context() -> list[str]:
    """System prompt lines for the active workspace (empty for General). Read by assistant.system_prompt."""
    wid = active_id()
    w = WORKSPACES[wid]
    if not w["instructions"]:
        return []
    line = f"Professional workspace: {w['name']}. {w['instructions']}"
    if w["pack"]:
        from .packs import registry

        if not registry.is_installed(w["pack"]):  # removed after choosing: say so instead of promising tools
            line += f" The {w['name']} tools are not added right now; say so if the user asks for them."
    return [line]


class ChooseIn(BaseModel):
    id: str


router = APIRouter(prefix="/api/workspaces", dependencies=[Depends(require_user)])


@router.get("")
def api_list():
    return catalogue()


@router.get("/active")
def api_active():
    return current()


@router.post("/active")
def api_choose(data: ChooseIn):
    return choose(data.id)
