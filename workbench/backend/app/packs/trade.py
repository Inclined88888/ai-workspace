"""Trade pack: site photo or voice note -> itemised quote -> invoice -> payment reminder drafts (feat/v02-packs).

Works for any service business. Australian defaults (GST 10%, ABN, "Tax invoice") and every one of them is a setting.
Money is computed with Decimal and rounded half-up to the cent. Reminders are drafts only: nothing here sends anything.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from fastapi import HTTPException

from .. import analysis, db
from . import ai, registry, store

PACK = "trade"
DEFAULTS = {
    "business_name": "", "abn": "", "address": "", "phone": "", "email": "", "logo_file_id": None,
    "tax_registered": True, "tax_name": "GST", "tax_rate": 10.0, "prices_include_tax": False,
    "materials_markup": 20.0, "labour_markup": 0.0, "labour_rate": 95.0, "labour_unit": "hour", "callout_fee": 0.0,
    "currency": "AUD", "currency_symbol": "$", "payment_terms_days": 14, "quote_valid_days": 30,
    "payment_details": "", "quote_prefix": "Q-", "invoice_prefix": "INV-", "next_quote": 1, "next_invoice": 1,
    "country": "AU",
}
KINDS = ("labour", "materials", "other")
CENT = Decimal("0.01")


def _d(v) -> Decimal:
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


def settings() -> dict:
    return store.settings(PACK, DEFAULTS)


def save_settings(data: dict) -> dict:
    cur = settings()
    for k, v in data.items():
        if k in DEFAULTS and k not in ("next_quote", "next_invoice"):
            cur[k] = v
    if cur["abn"] and not abn_valid(cur["abn"]):
        raise HTTPException(400, "That ABN doesn't pass the ABN check. It should be 11 digits, like 51 824 753 556.")
    store.save_settings(PACK, cur)
    return public_settings()


def public_settings() -> dict:
    s = settings()
    return {**s, "abn_valid": abn_valid(s["abn"]) if s["abn"] else None, "is_tax_invoice": is_tax_invoice(s)}


def abn_valid(abn: str) -> bool:
    """The ATO's ABN checksum: subtract 1 from the first digit, weight, sum, divisible by 89."""
    digits = [int(c) for c in re.sub(r"\D", "", str(abn))]
    if len(digits) != 11:
        return False
    digits[0] -= 1
    weights = (10, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19)
    return sum(d * w for d, w in zip(digits, weights)) % 89 == 0


def is_tax_invoice(s: dict) -> bool:
    """In Australia only a GST-registered business with an ABN issues tax invoices. Elsewhere: registered for tax."""
    if not s.get("tax_registered"):
        return False
    return abn_valid(s.get("abn", "")) if s.get("country", "AU") == "AU" else True


# --- maths ------------------------------------------------------------------------------------------------------------
def clean_items(items: list) -> list[dict]:
    out = []
    for it in items or []:
        if not isinstance(it, dict) or not str(it.get("description", "")).strip():
            continue
        kind = it.get("kind") if it.get("kind") in KINDS else "other"
        row = {"kind": kind, "description": str(it["description"]).strip()[:300], "qty": float(_d(it.get("qty", 1)) or 1),
               "unit": str(it.get("unit") or ("hour" if kind == "labour" else "each"))[:20], "unit_cost": float(_money(_d(it.get("unit_cost")))),
               "estimate": bool(it.get("estimate"))}
        if it.get("markup") not in (None, ""):
            row["markup"] = float(_d(it["markup"]))
        out.append(row)
    return out[:200]


def totals(items: list[dict], s: dict | None = None) -> dict:
    """Each line: qty x unit cost, plus markup (materials by default). Then tax on the subtotal, or inside the prices."""
    s = s or settings()
    rate = _d(s["tax_rate"]) if s.get("tax_registered") else Decimal(0)
    lines, by_kind, sub = [], {k: Decimal(0) for k in KINDS}, Decimal(0)
    for it in items:
        default = s["materials_markup"] if it["kind"] == "materials" else s["labour_markup"] if it["kind"] == "labour" else 0
        mk = _d(it.get("markup", default))
        cost = _d(it["qty"]) * _d(it["unit_cost"])
        price = _money(cost * (1 + mk / 100))
        lines.append({**it, "markup_applied": float(mk), "unit_price": float(_money(_d(it["unit_cost"]) * (1 + mk / 100))),
                      "amount": float(price), "cost": float(_money(cost))})
        by_kind[it["kind"]] += price
        sub += price
    if s.get("prices_include_tax"):
        total = sub
        tax = _money(total * rate / (100 + rate)) if rate else Decimal(0)
        ex = total - tax
    else:
        ex = sub
        tax = _money(ex * rate / 100)
        total = ex + tax
    cost_total = sum((_d(x["cost"]) for x in lines), Decimal(0))
    return {"lines": lines, "labour": float(by_kind["labour"]), "materials": float(by_kind["materials"]), "other": float(by_kind["other"]),
            "subtotal_ex_tax": float(_money(ex)), "tax": float(tax), "total": float(_money(total)), "tax_name": s["tax_name"],
            "tax_rate": float(rate), "prices_include_tax": bool(s.get("prices_include_tax")), "margin": float(_money(ex - cost_total)),
            "currency": s["currency"]}


def fmt(v: float, s: dict) -> str:
    return f"{s['currency_symbol']}{v:,.2f}"


# --- drafting -----------------------------------------------------------------------------------------------------------
QUOTE_PROMPT = (
    "You price jobs for a {trade}service business. From the job description (and what a site photo shows), list the work as "
    "line items. Use the business's rates: labour {labour_rate} {currency} per {labour_unit}. Estimate materials at trade cost "
    "before markup (markup is added later). Split labour and materials. Mark any guessed quantity or cost as estimate true. "
    'Reply with only JSON: {{"title": "short job title", "items": [{{"kind": "labour|materials|other", "description": "...", '
    '"qty": number, "unit": "hour|each|m|m2|l|...", "unit_cost": number, "estimate": true|false}}], "questions": ["anything '
    'that would change the price"]}}. Max 25 items.'
)
LINE = re.compile(r"^\s*(?:[-*•]\s*)?(?:(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>h|hrs?|hours?|m2|m|l|ea|each|x)?\s*(?:x|×|of)?\s+)?"
                  r"(?P<desc>.+?)(?:\s*(?:(?:@|at|for|-|–|:)\s*\$?|\$)\s*(?P<cost>\d+(?:[.,]\d+)?))?\s*$", re.I)
LABOUR_WORDS = re.compile(r"\b(labour|labor|hours?|hrs?|install|fit|repair|remove|call.?out|service|clean|paint|mow|travel)\b", re.I)


def parse_lines(text: str, s: dict) -> list[dict]:
    """No model: read lines like '3 x downlights @ 45', '2 hours labour', 'skip bin $380'."""
    items = []
    for raw in re.split(r"[\n;]+", text or ""):
        raw = raw.strip()
        if not raw or len(raw) > 200:
            continue
        m = LINE.match(raw.replace("$", " $ ").replace("  ", " "))
        if not m or not m.group("desc"):
            continue
        desc = m.group("desc").strip(" $-").strip()
        if not m.group("qty") and not m.group("cost") and (raw.endswith(".") or len(desc.split()) >= 5):
            continue  # a sentence describing the job, not a priced line
        unit = (m.group("unit") or "").lower()
        hours = unit.startswith("h") or bool(re.search(r"\bhours?\b|\bhrs?\b", raw, re.I))
        kind = "labour" if hours or (LABOUR_WORDS.search(desc) and not m.group("cost")) else "materials"
        cost = m.group("cost")
        items.append({"kind": kind, "description": desc[:1].upper() + desc[1:], "qty": float(m.group("qty") or 1),
                      "unit": "hour" if kind == "labour" else ("each" if unit in ("", "x", "ea", "each") else unit),
                      "unit_cost": float((cost or "0").replace(",", ".")) if cost else (s["labour_rate"] if kind == "labour" else 0.0),
                      "estimate": cost is None})
    return items


async def draft_quote(description: str = "", photo_file_id: int | None = None, transcript: str = "", customer: dict | None = None,
                      title: str = "") -> dict:
    registry.require(PACK)
    s = settings()
    text = "\n".join(x for x in (description.strip(), transcript.strip()) if x)
    seen = ""
    if photo_file_id:
        try:
            seen = (await analysis.describe_image(photo_file_id, "Describe the job site for a quote: what work is needed, sizes, "
                                                                 "materials, access and any damage. Be specific and brief."))["description"]
        except HTTPException as e:
            seen = ""
            note_photo = str(e.detail)
        else:
            note_photo = ""
    else:
        note_photo = ""
    if not text and not seen:
        raise HTTPException(400, "Describe the job, add a site photo, or record a voice note first.")
    payload = f"Job description:\n{text or '(none)'}\n\nSite photo shows:\n{seen or '(no photo)'}"
    data = await ai.try_json(QUOTE_PROMPT.format(trade="", labour_rate=s["labour_rate"], currency=s["currency"], labour_unit=s["labour_unit"]),
                             payload, "trade-quote")
    via = "model"
    items = clean_items((data or {}).get("items") if isinstance(data, dict) else [])
    if not items:
        via = "rules"
        items = clean_items(parse_lines(text, s))
    if not items:
        items = [{"kind": "labour", "description": (text or seen)[:120] or "Labour", "qty": 1.0, "unit": s["labour_unit"],
                  "unit_cost": float(s["labour_rate"]), "estimate": True}]
    if s.get("callout_fee"):
        items.insert(0, {"kind": "labour", "description": "Call-out fee", "qty": 1.0, "unit": "each", "unit_cost": float(s["callout_fee"]), "estimate": False})
    questions = [str(q) for q in ((data or {}).get("questions") or [])][:5] if isinstance(data, dict) else []
    t = title or (data or {}).get("title") if isinstance(data, dict) else title
    doc = create("quote", items, customer or {}, str(t or (text or seen)[:60] or "Quote"),
                 source={"description": description, "transcript": transcript, "photo_file_id": photo_file_id, "photo_seen": seen, "via": via})
    doc["questions"] = questions
    if note_photo:
        doc["photo_note"] = note_photo
    return doc


def _next_number(kind: str) -> str:
    s = settings()
    key = "next_quote" if kind == "quote" else "next_invoice"
    n = int(s[key])
    while db.one("SELECT id FROM trade_docs WHERE kind = ? AND number = ?", kind, f"{s[kind + '_prefix']}{n:04d}"):
        n += 1
    s[key] = n + 1
    store.save_settings(PACK, s)
    return f"{s[kind + '_prefix']}{n:04d}"


def create(kind: str, items: list, customer: dict, title: str, source: dict | None = None, quote_id: int | None = None) -> dict:
    store.ensure()
    s = settings()
    items = clean_items(items)
    today = date.today()
    due = today + timedelta(days=int(s["quote_valid_days"] if kind == "quote" else s["payment_terms_days"]))
    did = db.run("INSERT INTO trade_docs (kind, number, customer, title, items, totals, source, quote_id, issued_on, due_on, created_at, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kind, _next_number(kind), json.dumps(_customer(customer)), title[:200],
                 json.dumps(items), json.dumps(totals(items, s)), json.dumps(source or {}), quote_id, today.isoformat(), due.isoformat(),
                 db.now(), db.now())
    return get(did)


def _customer(c: dict) -> dict:
    return {k: str((c or {}).get(k) or "")[:300] for k in ("name", "email", "phone", "address")}


def get(did: int) -> dict:
    store.ensure()
    r = db.one("SELECT * FROM trade_docs WHERE id = ?", int(did))
    if not r:
        raise HTTPException(404, "No such quote or invoice")
    for k in ("customer", "items", "totals", "source", "pushed"):
        r[k] = json.loads(r[k] or "{}")
    r["overdue_days"] = overdue_days(r)
    return r


def overdue_days(r: dict) -> int:
    if r["kind"] != "invoice" or r["status"] == "paid" or not r["due_on"]:
        return 0
    return max(0, (date.today() - date.fromisoformat(r["due_on"])).days)


def listing(kind: str = "") -> list[dict]:
    store.ensure()
    rows = db.all("SELECT id FROM trade_docs " + ("WHERE kind = ? " if kind else "") + "ORDER BY id DESC LIMIT 300", *([kind] if kind else []))
    return [get(r["id"]) for r in rows]


def update(did: int, data: dict) -> dict:
    r = get(did)
    items = clean_items(data["items"]) if "items" in data else r["items"]
    customer = _customer(data["customer"]) if "customer" in data else r["customer"]
    status = data.get("status", r["status"])
    allowed = ("draft", "sent", "accepted", "declined") if r["kind"] == "quote" else ("draft", "sent", "paid")
    if status not in allowed:
        raise HTTPException(400, f"Status must be one of {', '.join(allowed)}")
    db.run("UPDATE trade_docs SET items = ?, totals = ?, customer = ?, title = ?, notes = ?, status = ?, due_on = ?, pdf_file_id = NULL, updated_at = ? WHERE id = ?",
           json.dumps(items), json.dumps(totals(items)), json.dumps(customer), str(data.get("title", r["title"]))[:200],
           str(data.get("notes", r["notes"]))[:4000], status, str(data.get("due_on", r["due_on"])), db.now(), did)
    return get(did)


def delete(did: int) -> dict:
    get(did)
    db.run("DELETE FROM trade_reminders WHERE invoice_id = ?", did)
    db.run("DELETE FROM trade_docs WHERE id = ?", did)
    return {"ok": True}


def invoice_from_quote(qid: int) -> dict:
    registry.require(PACK)
    q = get(qid)
    if q["kind"] != "quote":
        raise HTTPException(400, "That is already an invoice")
    existing = db.one("SELECT id FROM trade_docs WHERE kind = 'invoice' AND quote_id = ?", qid)
    if existing:
        return get(existing["id"])
    db.run("UPDATE trade_docs SET status = 'accepted', updated_at = ? WHERE id = ?", db.now(), qid)
    return create("invoice", q["items"], q["customer"], q["title"], source={"from_quote": q["number"]}, quote_id=qid)


def _long_date(iso: str) -> str:
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%B %Y')}"  # no %-d: Windows strftime has no flag for it


# --- reminders (drafts only) ------------------------------------------------------------------------------------------
def reminder_drafts(did: int) -> dict:
    registry.require(PACK)
    inv = get(did)
    if inv["kind"] != "invoice":
        raise HTTPException(400, "Reminders are for invoices")
    s = settings()
    tpl = json.loads(registry.template(PACK, "reminders.json"))
    days = inv["overdue_days"]
    vals = {"number": inv["number"], "business": s["business_name"] or "us", "customer": inv["customer"].get("name") or "there",
            "total": fmt(inv["totals"]["total"], s), "due": _long_date(inv["due_on"]),
            "days": days, "payment": ("How to pay:\n" + s["payment_details"]) if s["payment_details"] else ""}
    drafts = []
    db.run("DELETE FROM trade_reminders WHERE invoice_id = ?", did)
    due = date.fromisoformat(inv["due_on"]) if inv["due_on"] else date.today()
    for tone, after in (("friendly", 0), ("firm", 7), ("final", 30)):
        # Not that late yet: write the firmer drafts for the day they'd go out, so they never say "0 days overdue".
        tv = {**vals, "days": max(days, after)}
        subject, body = tpl[tone]["subject"].format(**tv), re.sub(r"\n{3,}", "\n\n", tpl[tone]["body"].format(**tv))
        rid = db.run("INSERT INTO trade_reminders (invoice_id, tone, subject, body, created_at) VALUES (?, ?, ?, ?, ?)",
                     did, tone, subject, body, db.now())
        send_on = max(date.today(), due + timedelta(days=after)).isoformat()
        drafts.append({"id": rid, "tone": tone, "to": inv["customer"].get("email", ""), "subject": subject, "body": body,
                       "send_on": send_on})
    suggested = "final" if days > 30 else "firm" if days > 7 else "friendly"
    return {"invoice": inv["number"], "overdue_days": days, "suggested": suggested, "drafts": drafts, "sent": False,
            "note": "These are drafts. Nothing has been sent: copy one into your email, or open it in Mail and send it yourself."}


# --- PDF ------------------------------------------------------------------------------------------------------------------
def pdf(did: int) -> dict:
    registry.require(PACK)
    r = get(did)
    s = settings()
    try:
        from fpdf import FPDF
    except ImportError as e:
        raise HTTPException(400, "Making PDF files needs the files add-on.") from e
    font = analysis._unicode_font()
    p = FPDF()
    p.set_auto_page_break(True, 18)
    p.add_page()
    fam = "helvetica"
    if font:
        p.add_font("body", "", font)
        p.add_font("body", "B", font)
        fam = "body"
    enc = (lambda x: str(x)) if font else (lambda x: str(x).encode("latin-1", "replace").decode("latin-1"))
    if s.get("logo_file_id"):
        try:
            p.image(analysis.get(int(s["logo_file_id"]))["path"], x=150, y=10, w=45)
        except Exception:  # noqa: BLE001 - a missing logo must not lose the document
            pass
    heading = "QUOTE" if r["kind"] == "quote" else ("TAX INVOICE" if is_tax_invoice(s) else "INVOICE")
    p.set_font(fam, "B", 22)
    p.cell(0, 12, enc(heading), new_x="LMARGIN", new_y="NEXT")
    p.set_font(fam, "", 10)
    for line in (s["business_name"], f"ABN {s['abn']}" if s["abn"] else "", s["address"], s["phone"], s["email"]):
        if line:
            p.cell(0, 5, enc(line), new_x="LMARGIN", new_y="NEXT")
    p.ln(4)
    p.set_font(fam, "B", 10)
    p.cell(95, 5, enc("For"), new_x="RIGHT", new_y="TOP")
    p.cell(0, 5, enc(f"{'Quote' if r['kind'] == 'quote' else 'Invoice'} {r['number']}"), new_x="LMARGIN", new_y="NEXT")
    p.set_font(fam, "", 10)
    cust = [r["customer"].get(k) for k in ("name", "address", "email", "phone") if r["customer"].get(k)]
    meta = [f"Date {date.fromisoformat(r['issued_on']).strftime('%d/%m/%Y')}",
            (f"Valid until {date.fromisoformat(r['due_on']).strftime('%d/%m/%Y')}" if r["kind"] == "quote" else
             f"Due {date.fromisoformat(r['due_on']).strftime('%d/%m/%Y')}")]
    for i in range(max(len(cust), len(meta))):
        p.cell(95, 5, enc(cust[i] if i < len(cust) else ""), new_x="RIGHT", new_y="TOP")
        p.cell(0, 5, enc(meta[i] if i < len(meta) else ""), new_x="LMARGIN", new_y="NEXT")
    p.ln(4)
    if r["title"]:
        p.set_font(fam, "B", 12)
        p.multi_cell(0, 6, enc(r["title"]), new_x="LMARGIN", new_y="NEXT")
        p.ln(1)
    widths = (90, 18, 18, 30, 34)
    p.set_font(fam, "B", 9)
    p.set_fill_color(255, 243, 214)
    for w, h, a in zip(widths, ("Description", "Qty", "Unit", "Unit price", "Amount"), "LRLRR"):
        p.cell(w, 7, enc(h), border="B", align=a, fill=True)
    p.ln()
    p.set_font(fam, "", 9)
    t = r["totals"]
    for kind in KINDS:
        rows = [x for x in t["lines"] if x["kind"] == kind]
        if not rows:
            continue
        p.set_font(fam, "B", 9)
        p.cell(0, 6, enc({"labour": "Labour", "materials": "Materials", "other": "Other"}[kind]), new_x="LMARGIN", new_y="NEXT")
        p.set_font(fam, "", 9)
        for x in rows:
            q = f"{x['qty']:g}"
            for w, v, a in zip(widths, (x["description"][:60], q, x["unit"], fmt(x["unit_price"], s), fmt(x["amount"], s)), "LRLRR"):
                p.cell(w, 6, enc(v), align=a)
            p.ln()
    p.ln(2)
    tax_label = f"{t['tax_name']} ({t['tax_rate']:g}%)"
    rows = [("Subtotal (ex " + t["tax_name"] + ")", t["subtotal_ex_tax"]), (tax_label + (" included" if t["prices_include_tax"] else ""), t["tax"]),
            ("Total" + (f" (inc {t['tax_name']})" if t["tax"] else ""), t["total"])] if s.get("tax_registered") else [("Total", t["total"])]
    for i, (label, v) in enumerate(rows):
        p.set_font(fam, "B" if i == len(rows) - 1 else "", 11 if i == len(rows) - 1 else 10)
        p.cell(sum(widths[:4]), 7, enc(label), align="R")
        p.cell(widths[4], 7, enc(fmt(v, s)), align="R", new_x="LMARGIN", new_y="NEXT")
    if not s.get("tax_registered"):
        p.set_font(fam, "", 9)
        p.cell(0, 6, enc(f"No {s['tax_name']} has been charged."), new_x="LMARGIN", new_y="NEXT")
    p.ln(4)
    p.set_font(fam, "", 9)
    if r["notes"]:
        p.multi_cell(0, 5, enc(r["notes"]), new_x="LMARGIN", new_y="NEXT")
        p.ln(2)
    if r["kind"] == "quote":
        tax_note = (f"include {t['tax_name']}" if t["prices_include_tax"] else f"exclude {t['tax_name']} unless shown") if s.get("tax_registered") else f"no {t['tax_name']} applies"
        terms = registry.template(PACK, "quote-terms.md").format(valid_days=s["quote_valid_days"], date=date.fromisoformat(r["issued_on"]).strftime("%d/%m/%Y"),
                                                                 currency=s["currency"], tax_note=tax_note)
        for kind, _, text in analysis._blocks(terms):
            p.set_font(fam, "B" if kind == "h" else "", 10 if kind == "h" else 9)
            p.multi_cell(0, 5, enc(("•  " if kind == "li" else "") + text), new_x="LMARGIN", new_y="NEXT")
    elif s["payment_details"]:
        p.set_font(fam, "B", 10)
        p.cell(0, 6, enc("How to pay"), new_x="LMARGIN", new_y="NEXT")
        p.set_font(fam, "", 9)
        p.multi_cell(0, 5, enc(s["payment_details"]), new_x="LMARGIN", new_y="NEXT")
    out = analysis.outputs_dir() / f"{r['number']}-{uuid.uuid4().hex[:6]}.pdf"
    p.output(str(out))
    f = analysis._register_output(Path(out), f"{heading.title()} {r['number']}.pdf")
    db.run("UPDATE trade_docs SET pdf_file_id = ?, updated_at = ? WHERE id = ?", f["id"], db.now(), did)
    return {**f, "download": f"/api/files/{f['id']}/download"}
