"""Assistant shortcuts for the packs (feat/v02-packs). Each tool only shows up once its pack is added, so the core
assistant stays generic. None of them send, post or pay: reminders are drafts, pushes to ServiceM8/Xero happen only on
the Trade page after the user approves the preview."""

from __future__ import annotations

from . import legal, registry, study, trade


def _ids(v) -> list[int]:
    if isinstance(v, list):
        return [int(x) for x in v if str(x).strip().isdigit()]
    return [int(x) for x in str(v or "").replace(" ", ",").split(",") if x.strip().isdigit()]


def _on(pack: str):
    return lambda ctx: registry.is_installed(pack)


def register_tools() -> None:
    from .. import assistant

    if "trade_draft_quote" in assistant.TOOL_MAP:
        return
    S, I = assistant.S, assistant.I
    T = assistant.Tool

    async def draft_quote(description: str = "", photo_file: str = "", transcript: str = "", customer_name: str = "",
                          customer_email: str = "", title: str = "") -> dict:
        d = await trade.draft_quote(description, int(photo_file) if str(photo_file).isdigit() else None, transcript,
                                    {"name": customer_name, "email": customer_email}, title)
        return {"id": d["id"], "number": d["number"], "items": d["items"], "totals": d["totals"], "questions": d.get("questions", []),
                "page": f"/packs/trade?doc={d['id']}", "note": "Draft quote saved. Lines marked estimate need checking before it goes out."}

    def list_docs(kind: str = "") -> list[dict]:
        return [{"id": d["id"], "kind": d["kind"], "number": d["number"], "customer": d["customer"].get("name"), "title": d["title"],
                 "total": d["totals"]["total"], "status": d["status"], "due_on": d["due_on"], "overdue_days": d["overdue_days"]}
                for d in trade.listing(kind if kind in ("quote", "invoice") else "")]

    def make_invoice(quote_id: int) -> dict:
        d = trade.invoice_from_quote(int(quote_id))
        return {"id": d["id"], "number": d["number"], "total": d["totals"]["total"], "due_on": d["due_on"], "page": f"/packs/trade?doc={d['id']}"}

    def pdf(document_id: int) -> dict:
        f = trade.pdf(int(document_id))
        return {"file_id": f["id"], "name": f["name"], "download": f["download"]}

    def check_document(file: str = "", text: str = "") -> dict:
        return legal.check_document(int(file) if str(file).isdigit() else None, text)

    def find_clause(query: str = "", tag: str = "") -> list[dict]:
        return legal.clauses(query, tag)[:10]

    def timeline(files: str = "", text: str = "") -> dict:
        return legal.timeline(_ids(files), text)

    async def flashcards(file: str = "", text: str = "", deck: str = "", count: int = 20) -> dict:
        r = await study.make_cards(int(file) if str(file).isdigit() else None, text, deck, count)
        return {"deck": r["deck"], "made": r["made"], "sample": [{"front": c["front"], "back": c["back"]} for c in r["cards"][:5]],
                "page": "/packs/study?tab=cards", "note": r["note"]}

    def lab_entry(title: str, body: str = "", tags: str = "", attachments: str = "") -> dict:
        e = study.save_lab_entry({"title": title, "body": body, "tags": tags, "attachments": _ids(attachments)})
        return {"id": e["id"], "entry_date": e["entry_date"], "title": e["title"], "page": "/packs/study?tab=lab"}

    tools = [
        T("trade_draft_quote", "Trade pack: draft an itemised quote (labour and materials, markup, GST from the user's settings) from a "
          "job description, a voice-note transcript and/or a site photo file id. Saves a draft; sends nothing.",
          {"description": S(), "photo_file": S("file id of a site photo"), "transcript": S("voice note transcript"), "customer_name": S(),
           "customer_email": S(), "title": S()}, draft_quote, (), when=_on("trade"), effect="write", untrusted=True),
        T("trade_list_documents", "Trade pack: list quotes and invoices with totals, status and days overdue.", {"kind": S("quote or invoice")},
          list_docs, (), when=_on("trade")),
        T("trade_make_invoice", "Trade pack: turn an accepted quote into an invoice (same lines and totals). Sends nothing.",
          {"quote_id": I()}, make_invoice, ("quote_id",), when=_on("trade"), effect="write"),
        T("trade_make_pdf", "Trade pack: make the PDF of a quote or invoice with the user's logo, ABN and GST.", {"document_id": I()}, pdf,
          ("document_id",), when=_on("trade"), effect="write"),
        T("trade_reminder_drafts", "Trade pack: write friendly, firm and final payment reminder DRAFTS for an invoice. They are never "
          "sent automatically; show them to the user to copy or send themselves.", {"invoice_id": I()},
          lambda invoice_id: trade.reminder_drafts(int(invoice_id)), ("invoice_id",), when=_on("trade"), effect="write"),
        T("legal_check_document", "Legal pack: check a contract for defined terms used but not defined, broken cross-references and "
          "citation format. Every finding has its location. Not legal advice: always say so and give the sources.",
          {"file": S("file id"), "text": S("or pasted text")}, check_document, (), when=_on("legal"), untrusted=True),
        T("legal_find_clause", "Legal pack: search the user's saved clause library.", {"query": S(), "tag": S()}, find_clause, (),
          when=_on("legal")),
        T("legal_timeline", "Legal pack: build a dated matter timeline from documents (file ids, comma separated) or text, each event "
          "with its source passage. Flags ambiguous dates. Not legal advice.", {"files": S("file ids, comma separated"), "text": S()},
          timeline, (), when=_on("legal"), untrusted=True),
        T("study_make_flashcards", "Study pack: make flashcards from a file or notes into a deck for spaced-repetition review.",
          {"file": S("file id"), "text": S(), "deck": S(), "count": I()}, flashcards, (), when=_on("study"), effect="write", untrusted=True),
        T("study_maths", "Study pack: work a maths problem step by step with every step and the answer checked by sympy. task: auto, "
          "solve, simplify, factor, expand, differentiate, integrate, limit or evaluate. Show the steps as LaTeX between $$.",
          {"expression": S("e.g. x^2 - 5x + 6 = 0"), "task": S(), "variable": S(), "to": S("limit point")},
          lambda expression, task="auto", variable="", to="": study.maths(expression, task or "auto", variable, to), ("expression",),
          when=_on("study")),
        T("study_check_steps", "Study pack: check someone's working line by line (one expression or equation per line) and point to the "
          "first wrong step.", {"lines": S("working, one step per line")},
          lambda lines: study.check_steps(str(lines).splitlines()), ("lines",), when=_on("study")),
        T("study_lab_entry", "Study pack (Pro): add a dated lab notebook entry with optional attachment file ids.",
          {"title": S(), "body": S(), "tags": S(), "attachments": S("file ids, comma separated")}, lab_entry, ("title",),
          when=lambda ctx: registry.is_installed("study") and registry.allowed(study.LAB), effect="write"),
    ]
    for t in tools:
        assistant.TOOLS.append(t)
        assistant.TOOL_MAP[t.name] = t
