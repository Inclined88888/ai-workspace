"""/api/mobile/packs: what the paired phone needs from the packs (feat/v02-packs).

Phone parity is deliberately small: see which packs are added, review due flashcards, and look over quotes and invoices
(with reminder drafts the phone hands to its own share sheet). Nothing here sends anything.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import mobile
from . import registry, store, study, trade

router = APIRouter(prefix="/api/mobile/packs", dependencies=[Depends(mobile.require_device), Depends(lambda: store.ensure())])


class GradeIn(BaseModel):
    grade: str


@router.get("")
def packs():
    return [{"id": p["id"], "name": p["name"], "tagline": p["tagline"], "tier": p["tier"], "installed": p["installed"]}
            for p in registry.catalogue()]


@router.get("/study/decks")
def decks():
    registry.require("study")
    return study.decks()


@router.get("/study/due")
def due(deck: str = ""):
    registry.require("study")
    return study.due(deck)


@router.post("/study/cards/{cid}/review")
def review(cid: int, data: GradeIn):
    registry.require("study")
    return study.review(cid, data.grade)


@router.get("/trade/docs")
def docs():
    registry.require("trade")
    return trade.listing()


@router.get("/trade/docs/{did}")
def doc(did: int):
    registry.require("trade")
    return trade.get(did)


@router.post("/trade/docs/{did}/reminders")
def reminders(did: int):
    registry.require("trade")
    return trade.reminder_drafts(did)
