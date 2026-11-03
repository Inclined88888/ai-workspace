"""/api/packs: the Packs gallery and the three pack pages (feat/v02-packs)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from ..security import require_user
from . import legal, registry, store, study, trade, trade_push

router = APIRouter(prefix="/api/packs", dependencies=[Depends(require_user), Depends(lambda: store.ensure())])


# --- gallery -------------------------------------------------------------------------------------------------------------
@router.get("")
def gallery():
    return registry.catalogue()


@router.post("/{pack_id}/add")
def add(pack_id: str):
    registry.manifest(pack_id)
    return registry.add(pack_id)


@router.post("/{pack_id}/remove")
def remove(pack_id: str):
    return registry.remove(pack_id)


# --- trade ---------------------------------------------------------------------------------------------------------------
class QuoteIn(BaseModel):
    description: str = ""
    transcript: str = ""
    photo_file_id: int | None = None
    customer: dict = Field(default_factory=dict)
    title: str = ""


class DocIn(BaseModel):
    kind: str = "quote"
    items: list[dict] = Field(default_factory=list)
    customer: dict = Field(default_factory=dict)
    title: str = ""


class PushIn(BaseModel):
    document_id: int
    approval_code: str = ""


@router.get("/trade/settings")
def trade_settings():
    registry.require("trade")
    return trade.settings()


@router.put("/trade/settings")
def trade_save_settings(data: dict):
    registry.require("trade")
    return trade.save_settings(data)


@router.post("/trade/totals")
def trade_totals(data: dict):
    """Live totals while the user edits lines (nothing saved)."""
    registry.require("trade")
    return trade.totals(trade.clean_items(data.get("items") or []))


@router.post("/trade/quotes/draft")
async def trade_draft(data: QuoteIn):
    return await trade.draft_quote(data.description, data.photo_file_id, data.transcript, data.customer, data.title)


@router.get("/trade/docs")
def trade_docs(kind: str = ""):
    registry.require("trade")
    return trade.listing(kind)


@router.post("/trade/docs")
def trade_create(data: DocIn):
    registry.require("trade")
    if data.kind not in ("quote", "invoice"):
        data.kind = "quote"
    return trade.create(data.kind, data.items, data.customer, data.title or ("Quote" if data.kind == "quote" else "Invoice"))


@router.get("/trade/docs/{did}")
def trade_doc(did: int):
    registry.require("trade")
    return trade.get(did)


@router.put("/trade/docs/{did}")
def trade_update(did: int, data: dict):
    registry.require("trade")
    return trade.update(did, data)


@router.delete("/trade/docs/{did}")
def trade_delete(did: int):
    registry.require("trade")
    return trade.delete(did)


@router.post("/trade/docs/{did}/invoice")
def trade_invoice(did: int):
    return trade.invoice_from_quote(did)


@router.post("/trade/docs/{did}/reminders")
def trade_reminders(did: int):
    return trade.reminder_drafts(did)


@router.post("/trade/docs/{did}/pdf")
def trade_pdf(did: int):
    return trade.pdf(did)


@router.get("/trade/push")
def trade_push_status():
    registry.require("trade")
    return trade_push.status()


@router.put("/trade/push/{target}")
def trade_push_creds(target: str, data: dict):
    registry.require("trade")
    return trade_push.save_creds(target, data)


@router.post("/trade/push/{target}/preview")
def trade_push_preview(target: str, data: PushIn):
    return trade_push.preview(target, data.document_id)


@router.post("/trade/push/{target}/send")
async def trade_push_send(target: str, data: PushIn):
    return await trade_push.push(target, data.document_id, data.approval_code)


# --- legal ---------------------------------------------------------------------------------------------------------------
class SourceIn(BaseModel):
    file_id: int | None = None
    text: str = ""


class FilesIn(BaseModel):
    file_ids: list[int] = Field(default_factory=list)
    text: str = ""
    day_first: bool | None = None


class CompareIn(BaseModel):
    a: int
    b: int
    report: bool = True


@router.get("/legal/settings")
def legal_settings():
    registry.require("legal")
    return {**legal.settings(), "notice": legal.NOTICE}


@router.put("/legal/settings")
def legal_save_settings(data: dict):
    registry.require("legal")
    return legal.save_settings(data)


@router.get("/legal/clauses")
def legal_clauses(q: str = "", tag: str = ""):
    registry.require("legal")
    return legal.clauses(q, tag)


@router.post("/legal/clauses")
def legal_add_clause(data: dict):
    registry.require("legal")
    return legal.save_clause(data)


@router.put("/legal/clauses/{cid}")
def legal_edit_clause(cid: int, data: dict):
    registry.require("legal")
    return legal.save_clause(data, cid)


@router.post("/legal/clauses/{cid}/use")
def legal_use_clause(cid: int):
    registry.require("legal")
    return legal.use_clause(cid)


@router.delete("/legal/clauses/{cid}")
def legal_delete_clause(cid: int):
    registry.require("legal")
    return legal.delete_clause(cid)


@router.post("/legal/check")
def legal_check(data: SourceIn):
    return legal.check_document(data.file_id, data.text)


@router.post("/legal/compare")
def legal_compare(data: CompareIn):
    return legal.compare(data.a, data.b, data.report)


@router.post("/legal/timeline")
def legal_timeline(data: FilesIn):
    return legal.timeline(data.file_ids, data.text, data.day_first)


@router.post("/legal/timeline/report")
def legal_timeline_report(data: FilesIn):
    return legal.timeline_report(data.file_ids, data.text)


# --- study ---------------------------------------------------------------------------------------------------------------
class CardsIn(BaseModel):
    file_id: int | None = None
    text: str = ""
    deck: str = ""
    count: int = 20


class GradeIn(BaseModel):
    grade: str


class MathsIn(BaseModel):
    expression: str
    task: str = "auto"
    variable: str = ""
    to: str = ""


class StepsIn(BaseModel):
    lines: list[str]


class IdsIn(BaseModel):
    ids: list[int] = Field(default_factory=list)


@router.post("/study/cards")
async def study_make_cards(data: CardsIn):
    return await study.make_cards(data.file_id, data.text, data.deck, data.count)


@router.get("/study/decks")
def study_decks():
    registry.require("study")
    return study.decks()


@router.get("/study/due")
def study_due(deck: str = ""):
    registry.require("study")
    return study.due(deck)


@router.get("/study/cards")
def study_cards(deck: str = ""):
    registry.require("study")
    from .. import db

    return db.all("SELECT * FROM study_cards WHERE deck = ? ORDER BY id", deck) if deck else db.all("SELECT * FROM study_cards ORDER BY id LIMIT 2000")


@router.post("/study/cards/{cid}/review")
def study_review(cid: int, data: GradeIn):
    registry.require("study")
    return study.review(cid, data.grade)


@router.put("/study/cards/{cid}")
def study_edit_card(cid: int, data: dict):
    registry.require("study")
    return study.update_card(cid, data)


@router.delete("/study/cards/{cid}")
def study_delete_card(cid: int):
    registry.require("study")
    return study.delete_card(cid)


@router.post("/study/maths")
def study_maths(data: MathsIn):
    return study.maths(data.expression, data.task, data.variable, data.to)


@router.post("/study/maths/check")
def study_check(data: StepsIn):
    return study.check_steps(data.lines)


@router.get("/study/refs")
def study_refs():
    registry.require("study")
    return study.refs()


@router.post("/study/refs")
def study_add_ref(data: dict):
    return study.add_ref(data)


@router.post("/study/refs/doi")
async def study_doi(data: dict):
    return await study.from_doi(str(data.get("doi") or ""))


@router.post("/study/refs/import")
def study_import(data: dict):
    return study.import_bibtex(str(data.get("bibtex") or ""))


@router.delete("/study/refs/{rid}")
def study_delete_ref(rid: int):
    registry.require("study")
    return study.delete_ref(rid)


@router.get("/study/refs/export", response_class=PlainTextResponse)
def study_export(format: str = "bibtex", ids: str = ""):  # noqa: A002 - query name the page uses
    registry.require("study")
    chosen = [study.ref(int(i)) for i in ids.split(",") if i.strip().isdigit()] or study.refs()
    return PlainTextResponse(study.to_ris(chosen) if format == "ris" else study.to_bibtex(chosen),
                             media_type="application/x-research-info-systems" if format == "ris" else "application/x-bibtex")


@router.get("/study/zotero")
async def study_zotero():
    registry.require("study")
    return await study.zotero_status()


@router.post("/study/zotero/import")
async def study_zotero_import():
    return await study.zotero_import()


@router.post("/study/zotero/send")
async def study_zotero_send(data: IdsIn):
    return await study.zotero_send(data.ids)


@router.get("/study/lab")
def study_lab(q: str = ""):
    return study.lab_entries(q)


@router.post("/study/lab")
def study_lab_add(data: dict):
    return study.save_lab_entry(data)


@router.put("/study/lab/{eid}")
def study_lab_edit(eid: int, data: dict):
    return study.save_lab_entry(data, eid)


@router.post("/study/lab/export")
def study_lab_export(data: dict):
    registry.require("study")  # integration/v0.2: exporting your own notebook is never gated
    return study.lab_export(str(data.get("format") or "md"), [int(i) for i in data.get("ids") or []])
